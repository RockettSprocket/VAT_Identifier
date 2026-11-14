"""Append-only evidence trail: one JSON line per search query and per HTTP fetch."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG_PATH = DATA / "run_log.jsonl"

_context: dict = {}


def set_context(**kw) -> None:
    """Fields (e.g. company_number) added to every following log line."""
    _context.clear()
    _context.update(kw)


def log(kind: str, url: str, status=None, nbytes: int = 0, error=None, **extra) -> None:
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
