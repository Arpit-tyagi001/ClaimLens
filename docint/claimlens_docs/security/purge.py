"""Delete uploaded files once they are older than the retention window.

Lead B's worker calls purge_expired() on a schedule and removes the matching
database rows; this module only decides what is expired and deletes files.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_RETENTION = timedelta(hours=24)


def is_expired(created_at: datetime, now: datetime, retention: timedelta = DEFAULT_RETENTION) -> bool:
    return now - created_at >= retention


def purge_expired(
    upload_dir: Path,
    retention: timedelta = DEFAULT_RETENTION,
    now: datetime | None = None,
) -> list[Path]:
    """Delete files in upload_dir older than retention. Returns what was deleted."""
    now = now or datetime.now(timezone.utc)
    deleted = []
    for path in Path(upload_dir).rglob("*"):
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if is_expired(modified, now, retention):
            path.unlink()
            deleted.append(path)
    return deleted
