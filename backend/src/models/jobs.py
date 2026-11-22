from datetime import datetime
from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.constants.enums import DayStatus, Review, Status
from src.utils.time import now

class Schedule(Base):
    __tablename__ = 'schedules'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    params: Mapped[dict] = mapped_column(JSON)
    recipient: Mapped[str] = mapped_column(String(100))
    next_at: Mapped[datetime] = mapped_column(index=True)
    repeat_daily: Mapped[bool]
    enabled: Mapped[bool] = mapped_column(default=True)
    author: Mapped[str] = mapped_column(String(64))

class SettlementDay(Base):
    """One settlement date of a plan, tracked independently so a blocked day never stops later days."""

    __tablename__ = 'settlement_days'
    __table_args__ = (UniqueConstraint('schedule_id', 'report_day'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey('schedules.id'), index=True)
    report_day: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(20), default=DayStatus.PENDING, index=True)
    step: Mapped[str] = mapped_column(String(80), default='排队等待选择批次')
    detail: Mapped[str | None] = mapped_column(String(500))
    recipient: Mapped[str] = mapped_column(String(100))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'))
    # Plain reference: runs already point back at the day, a second FK would form a cycle.
    run_id: Mapped[int | None] = mapped_column(index=True)
    scheduled_at: Mapped[datetime | None]
    observed_dataset_id: Mapped[int | None]
    failed_dataset_id: Mapped[int | None]
    attempt: Mapped[int] = mapped_column(default=0)
    delivered_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class Run(Base):
    __tablename__ = 'runs'
    __table_args__ = (UniqueConstraint('schedule_id', 'scheduled_at'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey('schedules.id'))
    settlement_day_id: Mapped[int | None] = mapped_column(ForeignKey('settlement_days.id'))
    scheduled_at: Mapped[datetime | None]
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default=Status.QUEUED, index=True)
    params: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(500))
    export_file: Mapped[str | None] = mapped_column(String(100))
    review: Mapped[str] = mapped_column(String(20), default=Review.PENDING)
    review_note: Mapped[str | None] = mapped_column(String(500))
    decision: Mapped[str | None] = mapped_column(String(120))
    attempt: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None]
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None]

class Delivery(Base):
    __tablename__ = 'deliveries'

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('runs.id'), unique=True)
    settlement_day_id: Mapped[int | None] = mapped_column(ForeignKey('settlement_days.id'))
    recipient: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str] = mapped_column(String(500))
    report_day: Mapped[str | None] = mapped_column(String(10))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'))
    export_file: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=now)
