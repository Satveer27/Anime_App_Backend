from enum import StrEnum


class SnapshotStatus(StrEnum):
    RUNNING = "running"
    # The three files are uploaded to S3.
    ARCHIVED = "archived"
    # Upstream files are unchanged since the latest archived snapshot.
    SKIPPED = "skipped"
    FAILED = "failed"
