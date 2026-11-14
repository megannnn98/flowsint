"""
Format checks and technical normalisation of single values.

These are syntactic checks only (checksums, E.164, e-mail grammar, dates).
They do not find entities in text — the LLM does that — and they never change
what a value means: when a value is doubtful it is marked, not "fixed".
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

import phonenumbers
from email_validator import EmailNotValidError, validate_email

# Cyrillic letters that look like Latin ones (upper and lower case).
_CONFUSABLES = str.maketrans(
    {
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
        "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I", "Ј": "J",
        "Ѕ": "S", "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
        "х": "x", "к": "k", "м": "m", "т": "t", "н": "h", "в": "b", "і": "i",
        "ј": "j", "ѕ": "s",
    }
)  # fmt: skip


@dataclass
class Checked:
    value: str
    valid: bool = True
    issues: List[str] = field(default_factory=list)


def digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def collapse(value: str) -> str:
    return " ".join(value.split())


# ------------------------------------------------------------------ e-mail


def check_email(raw: str) -> Checked:
    value = collapse(raw).strip(" .,;")
    issues: List[str] = []
    valid = True
    if not value.isascii():
        mapped = value.translate(_CONFUSABLES)
        if mapped.isascii():
            changed = sum(1 for a, b in zip(value, mapped) if a != b)
            issues.append(
                f"contains {changed} Cyrillic look-alike character(s); Latin form needs review"
            )
            value = mapped
        else:
            issues.append("contains non-Latin characters")
        valid = False  # never auto-confirm a look-alike address
    try:
        result = validate_email(value, check_deliverability=False)
        value = result.normalized.lower()
    except EmailNotValidError:
        issues.append("not a valid e-mail address")
        return Checked(value.lower(), False, issues)
    return Checked(value, valid, issues)


# ------------------------------------------------------------------- phone


def check_phone(raw: str) -> Checked:
    value = collapse(raw)
    d = digits(value)
    if not d:
        return Checked(value, False, ["no digits"])
    if value.startswith("+"):
        candidate = "+" + d
    elif len(d) == 11 and d[0] in "78":
        candidate = "+7" + d[1:]
    elif len(d) == 10 and d[0] in "3489":
        candidate = "+7" + d
    else:
        candidate = "+" + d
    try:
        parsed = phonenumbers.parse(candidate, "RU")
    except phonenumbers.NumberParseException:
        return Checked(d, False, ["cannot parse phone number"])
    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    if phonenumbers.is_valid_number(parsed):
        return Checked(e164, True, [])
    if phonenumbers.is_possible_number(parsed):
        return Checked(
            e164, False, ["possible but not a valid number in numbering plan"]
        )
    return Checked(d, False, ["not a valid phone number"])


# ------------------------------------------------------------- identifiers


def _inn_ok(d: str) -> bool:
    def cs(nums: str, weights: List[int]) -> int:
        return sum(int(n) * w for n, w in zip(nums, weights)) % 11 % 10

    if len(d) == 10:
        return cs(d, [2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(d[9])
    if len(d) == 12:
        return cs(d, [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == int(d[10]) and cs(
            d, [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        ) == int(d[11])
    return False


def _snils_ok(d: str) -> bool:
    if len(d) != 11:
        return False
    total = sum(int(c) * w for c, w in zip(d[:9], range(9, 0, -1)))
    if total < 100:
        expected = total
    elif total in (100, 101):
        expected = 0
    else:
        expected = total % 101
        if expected == 100:
            expected = 0
    return expected == int(d[9:])


def _ogrn_ok(d: str) -> bool:
    if len(d) == 13:
        return int(d[:12]) % 11 % 10 == int(d[12])
    if len(d) == 15:  # ОГРНИП
        return int(d[:14]) % 13 % 10 == int(d[14])
    return False


def _luhn_ok(d: str) -> bool:
    if not 12 <= len(d) <= 19:
        return False
    total = 0
    for i, c in enumerate(reversed(d)):
        n = int(c)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _iin_kz_ok(d: str) -> bool:
    if len(d) != 12:
        return False
    w1 = list(range(1, 12))
    w2 = [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2]
    cs = sum(int(a) * b for a, b in zip(d, w1)) % 11
    if cs == 10:
        cs = sum(int(a) * b for a, b in zip(d, w2)) % 11
    return cs != 10 and cs == int(d[11])


_SCHEME_CHECKS = {
    "inn": (_inn_ok, "ИНН"),
    "snils": (_snils_ok, "СНИЛС"),
    "ogrn": (_ogrn_ok, "ОГРН"),
    "iin": (_iin_kz_ok, "ИИН"),
    "bank_card": (_luhn_ok, "bank card"),
    "bank_account": (lambda d: len(d) == 20, "bank account"),
}


def check_identifier(raw: str, scheme: Optional[str]) -> Checked:
    d = digits(raw)
    scheme = (scheme or "other").lower()
    check = _SCHEME_CHECKS.get(scheme)
    if not d:
        return Checked(collapse(raw), False, ["no digits"])
    if check is None:
        return Checked(d, True, [])
    ok, label = check[0](d), check[1]
    return Checked(d, ok, [] if ok else [f"{label} checksum/length check failed"])


# ------------------------------------------------------------------- dates


_DATE_PATTERNS = [
    (re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})"), ("d", "m", "y")),
    (re.compile(r"^(\d{4})[./-](\d{1,2})[./-](\d{1,2})"), ("y", "m", "d")),
]


def check_date(raw: str) -> Checked:
    """Return ISO date (YYYY-MM-DD), or YYYY for a bare year."""
    value = collapse(raw).strip(" ,.;г")
    if re.fullmatch(r"\d{4}", value):
        return Checked(value, 1900 <= int(value) <= date.today().year + 1, [])
    for pattern, order in _DATE_PATTERNS:
        m = pattern.match(value)
        if m:
            parts = dict(zip(order, (int(x) for x in m.groups())))
            try:
                return Checked(date(parts["y"], parts["m"], parts["d"]).isoformat())
            except ValueError:
                return Checked(value, False, [f"invalid date '{value}'"])
    return Checked(value, False, [f"unrecognised date '{value}'"])


# ----------------------------------------------------------------- others


def check_username(raw: str) -> Checked:
    value = collapse(raw).lstrip("@")
    if re.fullmatch(r"[\w.\-]{2,64}", value):
        return Checked(value.lower(), True, [])
    return Checked(value, False, ["does not look like a username"])


_PROFILE_HOSTS = {
    "vk.com": "vk",
    "m.vk.com": "vk",
    "vk.ru": "vk",
    "t.me": "telegram",
    "telegram.me": "telegram",
    "ok.ru": "ok",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "facebook.com": "facebook",
    "www.facebook.com": "facebook",
}


def check_social(raw: str) -> Checked:
    value = collapse(raw)
    m = re.match(r"^(?:https?://)?([^/\s]+)(/[^\s?#]*)?", value, re.IGNORECASE)
    if m and "." in m.group(1):
        host = m.group(1).lower()
        path = (m.group(2) or "").rstrip("/")
        if not path:
            return Checked(value, False, ["profile URL without a path"])
        url = f"https://{host}{path}"
        issues = [] if host in _PROFILE_HOSTS else [f"unknown platform host {host}"]
        return Checked(url, True, issues)
    # A bare handle (e.g. Telegram "@name").
    return check_username(value)


def platform_of(url: str) -> Optional[str]:
    m = re.match(r"^https?://([^/]+)", url)
    return _PROFILE_HOSTS.get(m.group(1).lower()) if m else None


def vk_user_id(url: str) -> Optional[str]:
    m = re.match(r"^https?://(?:m\.)?vk\.(?:com|ru)/id(\d+)$", url)
    return m.group(1) if m else None


def text_contains(haystack: str, needle: str) -> bool:
    def norm(s: str) -> str:
        return collapse(s).casefold()

    return bool(needle.strip()) and norm(needle) in norm(haystack)


def words_present(haystack: str, needle: str) -> bool:
    """Every word of ``needle`` occurs in ``haystack`` (order and separators free)."""
    words = re.findall(r"\w+", needle.casefold())
    hay = set(re.findall(r"\w+", haystack.casefold()))
    return bool(words) and all(w in hay for w in words)


def digits_present(fields_text: List[str], value: str, min_digits: int = 5) -> bool:
    """The digits of ``value`` occur in the digits of one of the record's fields
    (formatting such as '+7 (908) …' vs '7908…' does not matter)."""
    d = digits(value)
    if len(d) < min_digits:
        return False
    return any(d in digits(f) for f in fields_text)
