from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"
    GONE = "gone"
