from datetime import UTC, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.config.settings import ARTIFACTS
from src.constants.enums import DayStatus, Role
from src.controllers.auth import allow, current
from src.domain.spec import SPEC
from src.models.identity import User
from src.models.jobs import Delivery, Run, Schedule, SettlementDay
from src.models.source import Dataset
from src.schemas.requests import ScheduleRequest
from src.services.audit import record
from src.services.validation import normalize_params
from src.utils import views
from src.utils.time import now
from src.utils.wakeup import wake

router = APIRouter()


@router.get('/schedules')
def schedules(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.schedule(item) for item in db.scalars(select(Schedule).order_by(Schedule.id.desc()).limit(100))]


@router.get('/settlement-days')
def settlement_days(db: Session = Depends(connection), actor: User = Depends(current)):
    rows = db.execute(select(SettlementDay, Dataset.name).outerjoin(Dataset, Dataset.id == SettlementDay.dataset_id)
                      .order_by(SettlementDay.report_day.desc(), SettlementDay.id.desc()).limit(200)).all()
    return [views.day(item, name) for item, name in rows]


@router.post('/settlement-days/{day_id}/retry')
def retry_day(day_id: int, db: Session = Depends(connection), actor: User = Depends(allow(Role.ADMIN))):
    day = db.get(SettlementDay, day_id, with_for_update=True)
    if not day:
        raise HTTPException(404, '找不到该结算日')
    if day.status == DayStatus.DELIVERED:
        raise HTTPException(409, '该结算日已交付，不能重跑')
    if day.status not in (DayStatus.BLOCKED, DayStatus.PENDING):
        raise HTTPException(409, '该结算日正在执行，请等待本轮结束')
    day.status = DayStatus.PENDING
    day.step = '已请求重新择优批次'
    day.detail = None
    day.run_id = None
    day.attempt = 0
    day.failed_dataset_id = None
    day.observed_dataset_id = None
    record(db, actor.username, 'settlement.retried', day.id, day=day.report_day)
    db.commit()
    wake()
    return views.day(day)


@router.post('/schedules', status_code=201)
def add(body: ScheduleRequest, db: Session = Depends(connection), actor: User = Depends(allow(Role.ADMIN))):
    if not db.get(Dataset, body.dataset_id):
        raise HTTPException(404, '找不到指定的数据批次')
    if body.first_at.utcoffset() is None:
        raise ValueError('首次运行时间需要明确时区')
    first = body.first_at.astimezone(UTC).replace(tzinfo=None)
    stamp = now()
    if first < stamp - timedelta(seconds=30) or first > stamp + timedelta(days=366):
        raise ValueError('请选择从现在起一年内的首次运行时间')
    item = Schedule(dataset_id=body.dataset_id, params=normalize_params(body.params), name=body.name,
                    recipient=body.recipient, next_at=first, repeat_daily=body.repeat_daily, author=actor.username)
    db.add(item)
    db.flush()
    record(db, actor.username, 'schedule.created', item.id, recipient=item.recipient)
    db.commit()
    wake()
    return views.schedule(item)


@router.post('/schedules/{schedule_id}/disable')
def disable(schedule_id: int, db: Session = Depends(connection), actor: User = Depends(allow(Role.ADMIN))):
    item = db.get(Schedule, schedule_id, with_for_update=True)
    if not item:
        raise HTTPException(404, '找不到该分发计划')
    if item.enabled:
        item.enabled = False
        record(db, actor.username, 'schedule.disabled', item.id)
        db.commit()
    return views.schedule(item)


@router.get('/deliveries')
def outbox(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.delivery(item) for item in db.scalars(select(Delivery).order_by(Delivery.id.desc()).limit(100))]


@router.get('/deliveries/{delivery_id}/export')
def delivery_export(delivery_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    receipt = db.get(Delivery, delivery_id)
    if not receipt:
        raise HTTPException(404, '找不到该分发回执')
    name = receipt.export_file
    if not name and receipt.run_id:
        # Older receipts predate the stored artifact name; fall back to the run's export.
        run = db.get(Run, receipt.run_id)
        name = run.export_file if run else None
    if receipt.state != 'delivered' or not name:
        raise HTTPException(409, '该回执没有可下载的报告')
    path = ARTIFACTS / name
    if not path.is_file():
        raise HTTPException(410, '报告文件已不可用')
    day = receipt.report_day or ''
    return FileResponse(path, media_type='text/csv', filename=f'{SPEC["short_name"]}-{day or receipt.run_id}.csv',
                        headers={'Cache-Control': 'no-store', 'ETag': f'"{name}"'})
