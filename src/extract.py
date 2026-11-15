"""Stage 4: extract evidence from visible page text. Pure functions, no I/O."""
import re

from bs4 import BeautifulSoup

SNIPPET_PAD = 90  # chars either side of a match -> ~200 char snippets


# ---- visible text ----------------------------------------------------------------------------
def visible_text(html: bytes | str) -> tuple[str, str, BeautifulSoup]:
    """Return (title, visible text with collapsed whitespace, soup)."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "template", "svg", "head"]):
        tag.extract()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return title, text, soup


def snippet(text: str, start: int, end: int) -> str:
    """The match at text[start:end] with SNIPPET_PAD characters of context on each side."""
    return text[max(0, start - SNIPPET_PAD): end + SNIPPET_PAD].strip()


# ---- VAT numbers -----------------------------------------------------------------------------
VAT_WEIGHTS = (8, 7, 6, 5, 4, 3, 2)
VAT_LABEL = re.compile(r"(?<![A-Za-z])V\.?A\.?T(?![A-Za-z]|\.[A-Za-z])|(?<![A-Za-z])VAT(?=Reg|No|Num)", re.I)
VAT_NUMBER = re.compile(r"(?<![A-Za-z0-9])(?:GB)?[ \-.]?(\d(?:[ \-.]?\d){8})(?!\d)", re.I)
VAT_WINDOW = 60


def vat_checksum_variant(number: str) -> str | None:
    """'plain' if total % 97 == 0, 'plus55' if (total + 55) % 97 == 0, else None."""
    if not re.fullmatch(r"\d{9}", number) or number == "000000000":
        return None
    total = sum(w * int(d) for w, d in zip(VAT_WEIGHTS, number[:7])) + int(number[7:])
    if total % 97 == 0:
        return "plain"
    if (total + 55) % 97 == 0:
        return "plus55"
    return None


def find_vat_numbers(text: str) -> list[dict]:
    """Checksum-valid 9-digit numbers starting within 60 chars after a label containing 'VAT'."""
    found: dict[str, dict] = {}
    for label in VAT_LABEL.finditer(text):
        base = label.end()
        window = text[base: base + VAT_WINDOW + 20]
        for m in VAT_NUMBER.finditer(window):
            if m.start(1) > VAT_WINDOW:
                break
            number = re.sub(r"\D", "", m.group(1))
            variant = vat_checksum_variant(number)
            if variant and number not in found:
                found[number] = {
                    "number": number,
                    "variant": variant,
                    "snippet": snippet(text, label.start(), base + m.end()),
                }
    return list(found.values())


# ---- company numbers -------------------------------------------------------------------------
CH_PREFIXES = "SC|NI|OC|SO|NC|LP|SL|NL|FC|BR|SF|NF|RC|IP|SP|NP|RS|CE|CS|OE|ZC|SE|GE"
COMPANY_LABEL = re.compile(
    r"company\s*(?:reg(?:istration|istered)?\.?\s*)?(?:no\b|number|num\b|#)"
    r"|co(?:mpany)?\.?\s*reg"
    r"|registered\s*(?:company\s*)?(?:no\b|number)"
    r"|registration\s*(?:no\b|number)"
    r"|reg(?:istered)?\.?\s*no\b"
    r"|registered\s+in\b"
    r"|registered\s+company"
    r"|\bCRN\b",
    re.I,
)
COMPANY_NUMBER = re.compile(
    rf"(?<![A-Za-z0-9])(?:(?:{CH_PREFIXES})\s?\d{{4,6}}R?|\d{{6,8}})(?![A-Za-z0-9])", re.I
)
COMPANY_WINDOW = 80


def normalise_company_number(raw: str) -> str:
    """Uppercase, strip spaces, zero-pad: 1234567 -> 01234567, SC12345 -> SC012345, IP11569R kept."""
    s = re.sub(r"\s+", "", raw or "").upper()
    if s.isdigit():
        return s.zfill(8)
    m = re.fullmatch(r"([A-Z]{2})(\d+)(R?)", s)
    if m and not m.group(3):
        return m.group(1) + m.group(2).zfill(6)
    return s


def find_company_numbers(text: str) -> list[dict]:
    """First company-number-shaped token within 80 chars after each registration label."""
    found: dict[str, dict] = {}
    for label in COMPANY_LABEL.finditer(text):
        base = label.end()
        m = COMPANY_NUMBER.search(text[base: base + COMPANY_WINDOW])
        if not m:
            continue
        number = normalise_company_number(m.group(0))
        if number not in found:
            found[number] = {"number": number, "snippet": snippet(text, label.start(), base + m.end())}
    return list(found.values())


# ---- "not VAT registered" statements ---------------------------------------------------------
_REG = r"(?:VAT[\s\-]?registered|registered\s+for\s+VAT)"
NOT_REGISTERED = re.compile(
    rf"\bnot\s+(?:currently\s+|yet\s+|presently\s+)?{_REG}"
    rf"|\bnon[\s\-]VAT[\s\-]registered"
    rf"|(?:isn['’]?t|aren['’]?t)\s+(?:currently\s+|yet\s+)?{_REG}",
    re.I,
)


def find_not_registered(text: str) -> list[dict]:
    """Snippets of up to three 'not VAT registered' statements."""
    return [{"snippet": snippet(text, m.start(), m.end())} for m in NOT_REGISTERED.finditer(text)][:3]


# ---- name matching ---------------------------------------------------------------------------
STOP_TOKENS = {"limited", "the", "and", "of", "co", "company", "uk", "plc", "llp", "lp"}


def name_tokens(name: str) -> list[str]:
    """Lowercase tokens; punctuation ignored; '&' -> 'and'; 'ltd' and 'limited' are the same word.

    The suffix is deliberately KEPT, so HOWELLS LEGAL LIMITED does not match inside
    HOWELLS LEGAL (HOLDINGS) LIMITED or vice versa.
    """
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"['’`]", "", s)
    tokens = re.findall(r"[a-z0-9]+", s)
    return ["limited" if t == "ltd" else t for t in tokens]


def _token_pattern(token: str) -> str:
    """Regex for one name token: ltd == limited, and == &, apostrophes optional."""
    if token == "limited":
        return r"(?:limited|ltd)"
    if token == "and":
        return r"(?:and|&)"
    return r"['’`]?".join(re.escape(c) for c in token)


def legal_name_regex(name: str) -> re.Pattern | None:
    """Regex matching the whole name with any punctuation between its tokens; None if no tokens."""
    tokens = name_tokens(name)
    if not tokens:
        return None
    body = r"[^A-Za-z0-9&]*".join(_token_pattern(t) for t in tokens)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.I)


def find_legal_name(text: str, name: str) -> dict | None:
    """Exact legal name in text, ignoring case and punctuation, Ltd == Limited."""
    rx = legal_name_regex(name)
    m = rx.search(text) if rx else None
    return {"name": name, "snippet": snippet(text, m.start(), m.end())} if m else None


def distinctive_tokens(name: str) -> list[str]:
    """Name tokens without stop words and single characters; falls back to all tokens."""
    tokens = [t for t in name_tokens(name) if t not in STOP_TOKENS and len(t) > 1]
    return tokens or [t for t in name_tokens(name) if len(t) > 1] or name_tokens(name)


def name_tokens_present(name: str, text: str, domain: str) -> bool:
    """name_only test: every distinctive name token is a word in the text or a substring of the domain."""
    wanted = distinctive_tokens(name)
    if not wanted:
        return False
    words = set(name_tokens(text))
    squashed_domain = re.sub(r"[^a-z0-9]", "", domain.lower())
    return all(t in words or t in squashed_domain for t in wanted)
