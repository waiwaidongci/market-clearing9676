from enum import StrEnum

class Role(StrEnum):
    ADMIN = 'admin'
    ANALYST = 'analyst'
    VIEWER = 'viewer'

class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'

class Review(StrEnum):
    PENDING = 'pending'
    ACCEPTED = 'accepted'
    RETURNED = 'returned'

class DayStatus(StrEnum):
    PENDING = 'pending'
    QUEUED = 'queued'
    RUNNING = 'running'
    BLOCKED = 'blocked'
    DELIVERED = 'delivered'
