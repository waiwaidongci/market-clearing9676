"""Daily settlement: turn a recurring plan into one tracked settlement day per date.

A day is only queued once a batch that passes the clearing checks exists. Days are
created in settlement-date order, so a restart after downtime catches up oldest
first, while a day without a qualifying batch is held (`blocked`) instead of
stopping the days that follow it. Importing a newer batch re-evaluates held days.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.config.database import Session
from src.constants.enums import DayStatus, Status
from src.domain.analysis import analyze
from src.domain.spec import SPEC
from src.models.jobs import Delivery, Run, Schedule, SettlementDay
from src.models.source import Dataset
from src.services.audit import record
from src.utils.time import now

log = logging.getLogger(__name__)

DAY_KEY = SPEC['schedule_date_param']
DAY_CANDIDATE_LIMIT = 50
DAY_BACKLOG_LIMIT = 400
DAY_STEP_LIMIT = 80
MAX_COMPUTE_FAILURES = 3


def trial(rows, report_day):
    """Return (qualifies, headline) for one batch on one settlement day."""
    try:
        result = analyze(rows, {DAY_KEY: report_day})
    except ValueError as error:
        return False, str(error)[:500]
    return bool(result.get('ready')), str(result.get('headline', ''))[:500]


def _dates(cache, dataset):
    """Set of posting dates in a batch, memoized for the duration of one worker pass."""
    key = ('dates', dataset.id)
    if cache is not None and key in cache:
        return cache[key]
    dates = {row.get('posting_date') for row in dataset.rows}
    if cache is not None:
        cache[key] = dates
    return dates


def choose(db, report_day, cache=None, since=None):
    """Newest imported batch that passes clearing for the day, with the newest headline.

    ``since`` limits the search to batches newer than a dataset already known to qualify,
    which keeps the pre-delivery recheck cheap.
    """
    query = select(Dataset).order_by(Dataset.id.desc()).limit(DAY_CANDIDATE_LIMIT)
    if since is not None:
        query = query.where(Dataset.id > since)
    datasets = db.scalars(query).all()
    newest_reason = '尚未导入数据批次'
    for index, dataset in enumerate(datasets):
        # Cheap pre-filter: a batch with no row for the day can never settle it.
        if report_day not in _dates(cache, dataset):
            if index == 0:
                newest_reason = f'最新批次没有 {report_day} 的入账流水'
            continue
        key = (dataset.id, report_day)
        if cache is not None and key in cache:
            qualifies, headline = cache[key]
        else:
            qualifies, headline = trial(dataset.rows, report_day)
            if cache is not None:
                cache[key] = (qualifies, headline)
        if index == 0:
            newest_reason = headline
        if qualifies:
            return dataset, headline
    return None, newest_reason


def _ensure_day(db, plan, report_day, scheduled_at):
    existing = db.scalar(select(SettlementDay.id).where(
        SettlementDay.schedule_id == plan.id, SettlementDay.report_day == report_day.isoformat()))
    if existing:
        return
    db.add(SettlementDay(schedule_id=plan.id, report_day=report_day.isoformat(), recipient=plan.recipient,
                         scheduled_at=scheduled_at, status=DayStatus.PENDING, step='等待择优批次'))


def materialize_due():
    """Create every missing settlement day up to now, oldest first, in one pass."""
    if not SPEC.get('scheduled'):
        return
    stamp = now()
    with Session.begin() as db:
        schedules = db.scalars(select(Schedule)
                               .where(Schedule.enabled.is_(True), Schedule.next_at <= stamp)
                               .order_by(Schedule.next_at).limit(20)
                               .with_for_update(skip_locked=True)).all()
        for plan in schedules:
            due = plan.next_at
            params = dict(plan.params)
            try:
                report_day = date.fromisoformat(params.get(DAY_KEY) or SPEC['params'][0]['default'])
            except (TypeError, ValueError):
                report_day = date.fromisoformat(SPEC['params'][0]['default'])
            if not plan.repeat_daily:
                plan.enabled = False
                db.add(Run(dataset_id=plan.dataset_id, schedule_id=plan.id, scheduled_at=due, name=plan.name,
                           params={**params, DAY_KEY: report_day.isoformat()}, author=plan.author))
                continue
            created = 0
            while due <= stamp and created < DAY_BACKLOG_LIMIT:
                _ensure_day(db, plan, report_day, due)
                report_day += timedelta(days=1)
                due += timedelta(days=1)
                created += 1
            plan.next_at = due
            plan.params = {**params, DAY_KEY: report_day.isoformat()}


def advance_days(limit=DAY_STEP_LIMIT):
    """Queue qualifying days and hold days without a passing batch; later days keep moving."""
    if not SPEC.get('scheduled'):
        return
    with Session.begin() as db:
        newest = db.scalar(select(Dataset.id).order_by(Dataset.id.desc()).limit(1))
        cache = {}
        # Pending days always progress. Held days are retried only when a newer batch could help,
        # so a long backlog of held dates can never starve dates that still need a first pick.
        pending = db.scalars(select(SettlementDay).where(SettlementDay.status == DayStatus.PENDING)
                             .order_by(SettlementDay.report_day, SettlementDay.id).limit(limit)
                             .with_for_update(skip_locked=True)).all()
        held = db.scalars(select(SettlementDay).where(SettlementDay.status == DayStatus.BLOCKED,
                                                      SettlementDay.observed_dataset_id.is_distinct_from(newest))
                          .order_by(SettlementDay.report_day, SettlementDay.id).limit(limit)
                          .with_for_update(skip_locked=True)).all()
        days = sorted(pending + held, key=lambda item: (item.report_day, item.id))[:limit]
        for day in days:
            plan = db.get(Schedule, day.schedule_id)
            # Disabling a plan stops new dates; dates already materialized still finish.
            if plan is None:
                continue
            picked, headline = choose(db, day.report_day, cache)
            if picked is None:
                changed = day.status != DayStatus.BLOCKED or day.detail != headline
                day.status = DayStatus.BLOCKED
                day.step = '双账批次未通过清分，已挂起等待补账'
                day.detail = headline
                day.observed_dataset_id = newest
                if changed:
                    record(db, 'worker', 'settlement.blocked', day.id, day=day.report_day, reason=headline)
                continue
            run = Run(dataset_id=picked.id, schedule_id=plan.id, settlement_day_id=day.id,
                      name=plan.name, params={**plan.params, DAY_KEY: day.report_day}, author=plan.author)
            db.add(run)
            db.flush()
            day.run_id = run.id
            day.dataset_id = picked.id
            day.observed_dataset_id = picked.id
            day.status = DayStatus.QUEUED
            day.step = '已选定合格批次，等待后台计算'
            day.detail = headline
            record(db, 'worker', 'settlement.queued', day.id, day=day.report_day,
                   dataset_id=picked.id, run_id=run.id)

        queued = db.scalars(select(SettlementDay)
                            .where(SettlementDay.status == DayStatus.QUEUED)
                            .order_by(SettlementDay.report_day, SettlementDay.id).limit(limit)
                            .with_for_update(skip_locked=True)).all()
        for day in queued:
            run = db.get(Run, day.run_id) if day.run_id else None
            # Only a run that has not started computing can be switched in place.
            if run is None or run.status != Status.QUEUED or run.attempt:
                continue
            picked, headline = choose(db, day.report_day, cache, since=run.dataset_id)
            if picked is None:
                continue
            run.dataset_id = picked.id
            day.dataset_id = picked.id
            day.observed_dataset_id = picked.id
            day.step = '已换用更新合格批次，等待后台计算'
            day.detail = headline
            record(db, 'worker', 'settlement.reselected', day.id, day=day.report_day, dataset_id=picked.id)


def fail_day(db, day, failure, dataset_id):
    """Record a compute failure; retry a few times on the same batch, then hold for new data."""
    if day.status == DayStatus.DELIVERED:
        return
    if day.failed_dataset_id != dataset_id:
        day.attempt = 0
        day.failed_dataset_id = dataset_id
    day.attempt = (day.attempt or 0) + 1
    day.detail = failure
    if day.attempt >= MAX_COMPUTE_FAILURES:
        day.status = DayStatus.BLOCKED
        day.step = '计算多次失败，已挂起等待补账或人工重跑'
        # Hold until the next import so a persistently failing batch does not spin new runs.
        day.observed_dataset_id = db.scalar(select(Dataset.id).order_by(Dataset.id.desc()).limit(1))
    else:
        day.status = DayStatus.PENDING
        day.step = '计算失败，等待重试'


def settle(db, job, day, result, export):
    """Deliver the day's report, or requeue once if a newer qualifying batch appeared before delivery."""
    if day is not None and day.status == DayStatus.DELIVERED:
        return
    if day is not None and result.get('ready', True):
        picked, headline = choose(db, day.report_day, since=job.dataset_id)
        if picked is not None:
            # Re-arm the same run for the newer batch; the delivered artifact stays on disk untouched.
            previous_export = job.export_file
            job.dataset_id = picked.id
            job.status = Status.QUEUED
            job.attempt = 0
            job.result = None
            job.export_file = None
            job.error = None
            job.finished_at = None
            job.lease_until = None
            day.dataset_id = picked.id
            day.observed_dataset_id = picked.id
            day.status = DayStatus.QUEUED
            day.step = '交付前发现更新合格批次，已换批重算'
            day.detail = headline
            record(db, 'worker', 'settlement.reselected', day.id, day=day.report_day,
                   dataset_id=picked.id, run_id=job.id, previous_export=previous_export)
            return
    plan = db.get(Schedule, job.schedule_id) if job.schedule_id else None
    recipient = plan.recipient if plan else (day.recipient if day is not None else '站内收件箱')
    ready = bool(result.get('ready', True))
    headline = str(result.get('headline', ''))[:500]
    report_day = day.report_day if day is not None else job.params.get(DAY_KEY)
    receipt = db.scalar(select(Delivery).where(Delivery.run_id == job.id))
    if receipt is not None and receipt.state == 'delivered':
        # Idempotent: a restart or a concurrent retry must not ship the same run twice.
        if day is not None:
            day.status = DayStatus.DELIVERED
            day.step = '已交付'
            day.delivered_at = day.delivered_at or now()
        return
    if day is not None and not ready:
        # Defensive: the day was queued from a passing batch, so this means data or rules moved.
        day.status = DayStatus.BLOCKED
        day.step = '清分未通过，已挂起等待补账'
        day.detail = headline
        day.observed_dataset_id = job.dataset_id
        _receipt(db, job, recipient, 'blocked', headline, export, report_day, receipt, day)
        record(db, 'worker', 'delivery.blocked', job.id, recipient=recipient, report_day=report_day)
        return
    detail = '已保存 CSV 到站内收件箱' if ready else headline
    state = 'delivered' if ready else 'blocked'
    _receipt(db, job, recipient, state, detail, export, report_day, receipt, day)
    record(db, 'worker', f'delivery.{state}', job.id,
           recipient=recipient, dataset_id=job.dataset_id, report_day=report_day)
    if day is not None:
        day.status = DayStatus.DELIVERED
        day.step = '已交付'
        day.detail = detail
        day.dataset_id = job.dataset_id
        day.delivered_at = now()


def _receipt(db, job, recipient, state, detail, export, report_day, receipt=None, day=None):
    """Keep exactly one receipt per run; a retry updates the previous state in place."""
    if receipt is None:
        candidate = Delivery(run_id=job.id, recipient=recipient, state=state, detail=detail,
                             dataset_id=job.dataset_id, export_file=export, report_day=report_day,
                             settlement_day_id=day.id if day is not None else job.settlement_day_id,
                             created_at=now())
        try:
            # The unique run_id is the final guard against two workers delivering the same run.
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            return candidate
        except IntegrityError:
            receipt = db.scalar(select(Delivery).where(Delivery.run_id == job.id).with_for_update())
            if receipt is None:
                raise
    if receipt.state == 'delivered' and state != 'delivered':
        # Never downgrade a receipt that already shipped.
        return receipt
    receipt.recipient = recipient
    receipt.state = state
    receipt.detail = detail
    receipt.dataset_id = job.dataset_id
    receipt.export_file = export
    receipt.report_day = report_day
    receipt.settlement_day_id = day.id if day is not None else job.settlement_day_id
    return receipt
