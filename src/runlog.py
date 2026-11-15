"""Append-only evidence trail: one JSON line per search query and per HTTP fetch."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
LOG_PATH: Path | None = None   # set per run with set_log_path(); nothing is logged before that

_context: dict = {}


def set_log_path(path: Path) -> None:
    global LOG_PATH
    LOG_PATH = path


def set_context(**kw) -> None:
    """Fields (e.g. company_number) added to every following log line."""
    _context.clear()
    _context.update(kw)


def log(kind: str, url: str, status=None, nbytes: int = 0, error=None, **extra) -> None:
    if LOG_PATH is None:
        return
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "url": url,
        "status": status,
        "bytes": nbytes,
        "error": error,
        **_context,
        **extra,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
