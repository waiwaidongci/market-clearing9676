import logging
from datetime import timedelta
from sqlalchemy import and_, or_, select
from src.config.database import Session
from src.constants.enums import DayStatus, Status
from src.domain.analysis import analyze
from src.models.jobs import Run, SettlementDay
from src.models.source import Dataset
from src.services import settlement
from src.services.audit import record
from src.utils.exports import write_report
from src.utils.time import now

log = logging.getLogger(__name__)


def materialize_due():
    settlement.materialize_due()


def advance():
    settlement.advance_days()


def claim():
    """Lease the next runnable job, skipping stale duplicates so one bad row cannot head-of-line block."""
    stamp = now()
    for _ in range(10):
        with Session.begin() as db:
            # Settlement backlog is drained in settlement-date order; manual runs interleave by id.
            report_day = (select(SettlementDay.report_day)
                          .where(SettlementDay.id == Run.settlement_day_id).scalar_subquery())
            query = (select(Run)
                     .where(or_(Run.status == Status.QUEUED,
                                and_(Run.status == Status.RUNNING, Run.lease_until < stamp)))
                     .order_by(report_day.asc().nulls_last(), Run.id)
                     .limit(1).with_for_update(skip_locked=True))
            job = db.scalar(query)
            if job is None:
                return None
            day = db.get(SettlementDay, job.settlement_day_id, with_for_update=True) if job.settlement_day_id else None
            if day is not None and day.status == DayStatus.DELIVERED:
                # A duplicate or stale run must never recompute a day that already shipped.
                job.status = Status.FAILED
                job.error = '该结算日已交付，重复任务已跳过'
                job.finished_at = stamp
                record(db, 'worker', 'analysis.skipped', job.id, day=day.report_day)
                continue
            if job.attempt >= 3:
                job.status = Status.FAILED
                job.error = '后台进程多次中断，请发起新的运行'
                job.finished_at = stamp
                record(db, 'worker', 'analysis.failed', job.id, reason=job.error)
                _abandon(db, job)
                continue
            job.status = Status.RUNNING
            job.attempt += 1
            job.lease_until = stamp + timedelta(minutes=3)
            if day is not None:
                day.status = DayStatus.RUNNING
                day.step = '后台正在计算'
            records = db.get(Dataset, job.dataset_id).rows
            return job.id, job.attempt, records, job.params
    return None


def _abandon(db, job):
    """A crash is retried a few times, then the day is held for an operator or fresh data."""
    day = db.get(SettlementDay, job.settlement_day_id, with_for_update=True) if job.settlement_day_id else None
    if day is None or day.status == DayStatus.DELIVERED:
        return
    day.run_id = None
    settlement.fail_day(db, day, job.error or '后台多次中断，已挂起等待补跑', job.dataset_id)


def execute(lease):
    job_id, attempt, rows, params = lease
    result = export = failure = None
    try:
        result = analyze(rows, params)
        export = write_report(job_id, attempt, result)
    except ValueError as error:
        failure = str(error)[:500]
    except Exception:
        log.exception('Analysis %s failed', job_id)
        failure = '计算失败，请检查字段口径后重试'
    with Session.begin() as db:
        job = db.get(Run, job_id, with_for_update=True)
        if job.attempt != attempt:
            return
        day = db.get(SettlementDay, job.settlement_day_id, with_for_update=True) if job.settlement_day_id else None
        if job.status != Status.RUNNING:
            # Someone else re-queued or settled this run first; never overwrite their state.
            return
        if day is not None and day.status == DayStatus.DELIVERED:
            return
        job.lease_until = None
        job.finished_at = now()
        job.status = Status.FAILED if failure else Status.COMPLETE
        job.result = None if failure else result
        job.export_file = export
        job.error = failure
        record(db, 'worker', f'analysis.{job.status}', job.id, attempt=attempt, reason=failure)
        if failure:
            if day is not None:
                day.run_id = None
                settlement.fail_day(db, day, failure, job.dataset_id)
            elif job.schedule_id:
                settlement.settle(db, job, None, {'ready': False, 'headline': failure}, export)
            return
        if day is not None:
            settlement.settle(db, job, day, result, export)
        elif job.schedule_id:
            settlement.settle(db, job, None, result, export)
