"""Stage 2: drop search results on the blocklist or on a domain detected as a directory."""
from pathlib import Path
from urllib.parse import urlsplit

import tldextract

BLOCKLIST_PATH = Path(__file__).resolve().parent.parent / "config" / "blocklist.txt"

# Bundled public-suffix snapshot only (no network call). Private suffixes are included so that
# hosting platforms (e.g. *.wixsite.com, *.github.io) give one registrable domain per customer.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)


def load_blocklist(path: Path = BLOCKLIST_PATH) -> set[str]:
    """Lower-cased domains from the blocklist file; '#' starts a comment."""
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


def registrable_domain(url_or_host: str) -> str:
    """company.example.co.uk, www.example.co.uk and example.co.uk -> example.co.uk.

    Accepts a URL or a bare host. Hosts without a public suffix (IP addresses, localhost)
    are returned unchanged.
    """
    value = (url_or_host or "").strip()
    host = host_of(value) if "//" in value else value.lower().strip(".")
    if not host:
        return ""
    return (_EXTRACT(host).top_domain_under_public_suffix or host).lower()


def is_blocked(url: str, blocklist: set[str], directories=frozenset()) -> bool:
    """True if the host equals a blocklist entry or is a subdomain of one, or if its
    registrable domain is in `directories` (domains detected as directories for this run)."""
    host = host_of(url)
    if not host:
        return True
    parts = host.split(".")
    if any(".".join(parts[i:]) in blocklist for i in range(len(parts))):
        return True
    return registrable_domain(host) in directories
