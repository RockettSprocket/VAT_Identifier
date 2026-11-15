"""Make the modules in src/ and probes/ importable from the tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
for _probe in ("eori_scan", "common_crawl", "public_records"):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "probes" / _probe))
