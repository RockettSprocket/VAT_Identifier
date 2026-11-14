"""Stage 2: drop search results whose domain is on the blocklist."""
from pathlib import Path
from urllib.parse import urlsplit

BLOCKLIST_PATH = Path(__file__).resolve().parent.parent / "config" / "blocklist.txt"


def load_blocklist(path: Path = BLOCKLIST_PATH) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            out.add(line)
    return out


def host_of(url: str) -> str:
    """Lower-case host without port or leading www."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_blocked(url: str, blocklist: set[str]) -> bool:
    """True if the host equals a blocklist entry or is a subdomain of one."""
    host = host_of(url)
    if not host:
        return True
    parts = host.split(".")
    return any(".".join(parts[i:]) in blocklist for i in range(len(parts)))
