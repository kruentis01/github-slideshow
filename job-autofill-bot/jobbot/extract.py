"""Read a job description and pull out pay, contract, shifts and a short summary.

All of this is rule-based, so it runs offline and costs nothing. When an
advert doesn't say something, the field reads "Not stated" rather than a guess.
"""

from __future__ import annotations

import re

from .textutil import sentences, truncate

DEFAULT_HOURS_PER_WEEK = 37.5
NOT_STATED = "Not stated"

# --------------------------------------------------------------------------- pay

_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(k)?"
_PERIODS = {
    "hour": r"per\s+hour|an\s+hour|a\s+hour|p\.?\s?/?\s?h(?:r|our)?\b|/\s?hr\b|/\s?hour|hourly|ph\b|phr\b",
    "year": r"per\s+annum|p\.?\s?a\.?(?!\w)|a\s+year|per\s+year|/\s?year|/\s?yr|annually|annual|per\s+yr|pro[\s-]rata",
    "day": r"per\s+day|a\s+day|/\s?day|daily|day\s+rate|p\.?d\.?(?!\w)",
    "week": r"per\s+week|a\s+week|/\s?week|weekly|p\.?w\.?(?!\w)",
}
_PERIOD_RE = re.compile("|".join(f"(?P<{k}>{v})" for k, v in _PERIODS.items()), re.I)
_PAY_RE = re.compile(
    rf"£\s?{_NUM}(?:\s*(?:-|–|—|to)\s*£?\s?{_NUM})?(?P<tail>[^£\n]{{0,30}})",
    re.I,
)


def _to_float(num: str, k: str | None) -> float:
    value = float(num.replace(",", ""))
    return value * 1000 if k else value


def _hourly(value: float, period: str, hours_per_week: float) -> float:
    if period == "hour":
        return value
    if period == "day":
        return value / (hours_per_week / 5)
    if period == "week":
        return value / hours_per_week
    return value / (hours_per_week * 52)  # year


def _fmt_money(value: float) -> str:
    return f"£{value:,.2f}" if value < 1000 else f"£{value:,.0f}"


# Lowest plausible UK hourly rate (National Minimum Wage, age 18-20, from April 2026).
_LOWEST_LEGAL_RATE = 10.85


def _pay_result(lo: float, hi: float | None, period: str, hours_per_week: float | None) -> dict:
    hi = hi if hi and hi > lo else None
    # Annual and daily pay for part-time jobs is usually quoted as the full-time
    # equivalent ("£24,000 pro rata"), so convert using a standard 37.5-hour week.
    # Weekly pay is what you actually get, so use the advert's own hours for it.
    hours = DEFAULT_HOURS_PER_WEEK if period in ("year", "day") or not hours_per_week else hours_per_week
    if (period == "year" and hours_per_week and hours_per_week < DEFAULT_HOURS_PER_WEEK
            and _hourly(lo, period, hours) < _LOWEST_LEGAL_RATE):
        hours = hours_per_week  # below minimum wage as FTE, so it must be the actual part-time salary
    h_lo = _hourly(lo, period, hours)
    h_hi = _hourly(hi, period, hours) if hi else None
    rate = f"£{h_lo:.2f}" + (f"–£{h_hi:.2f}" if h_hi else "") + "/hr"
    if period == "hour":
        display = rate
    else:
        original = _fmt_money(lo) + (f"–{_fmt_money(hi)}" if hi else "")
        label = {"year": "yr", "day": "day", "week": "wk"}[period]
        display = f"≈{rate} ({original}/{label})"
    return {
        "hourly_min": round(h_lo, 2),
        "hourly_max": round(h_hi, 2) if h_hi else None,
        "period": period,
        "estimated": period != "hour",
        "display": display,
    }


def parse_pay(text: str, structured: dict | None = None,
              hours_per_week: float | None = None) -> dict:
    """Find the advertised pay and express it per hour.

    ``structured`` may carry ``salary_min``, ``salary_max`` and ``salary_type``
    from a job board API; the description is checked first because adverts
    usually state an hourly rate there more precisely.
    """
    found: list[tuple[str, float, float | None]] = []
    for m in _PAY_RE.finditer(text or ""):
        lo = _to_float(m.group(1), m.group(2))
        hi = _to_float(m.group(3), m.group(4)) if m.group(3) else None
        if m.group(2) and m.group(3) and not m.group(4):
            hi = _to_float(m.group(3), "k")  # "£25-28k"
        period_match = _PERIOD_RE.search(m.group("tail") or "")
        if period_match:
            period = period_match.lastgroup
        elif lo >= 8000:
            period = "year"
        elif 5 <= lo < 60:
            period = "hour"
        else:
            continue  # a bonus, a fee, or something else ambiguous
        if period == "hour" and not 4 <= lo <= 250:
            continue
        found.append((period, lo, hi))

    for preferred in ("hour", "day", "week", "year"):
        for period, lo, hi in found:
            if period == preferred:
                return _pay_result(lo, hi, period, hours_per_week)

    if structured and structured.get("salary_min"):
        lo = float(structured["salary_min"])
        hi = float(structured["salary_max"]) if structured.get("salary_max") else None
        kind = (structured.get("salary_type") or "").lower()
        if "hour" in kind:
            period = "hour"
        elif "day" in kind:
            period = "day"
        elif "week" in kind:
            period = "week"
        elif "annum" in kind or "year" in kind or lo >= 8000:
            period = "year"
        elif lo < 60:
            period = "hour"
        else:
            period = "day"
        return _pay_result(lo, hi, period, hours_per_week)

    return {"hourly_min": None, "hourly_max": None, "period": None, "estimated": False, "display": NOT_STATED}


# ---------------------------------------------------------------------- contract

_WORD_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "eighteen": 18, "twenty-four": 24,
}
_DURATION_RE = re.compile(
    r"\b(\d{1,2}|" + "|".join(_WORD_NUMS) + r")[\s-]*(week|month|year)s?\b", re.I
)
_CONTRACT_CONTEXT = re.compile(
    r"contract|fixed|term|temp|assignment|placement|cover|initial|duration|period of|secondment|project|ongoing|ftc",
    re.I,
)
_NOT_CONTRACT_CONTEXT = re.compile(
    r"\b(?:experience|probation(?:ary)?|old|aged?|notice|warranty|training|course|years? of)\b", re.I
)
_UNTIL_RE = re.compile(
    r"\buntil\s+(?:the\s+end\s+of\s+)?((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4})",
    re.I,
)

_CONTRACT_TYPES = [
    ("Temp to perm", r"temp(?:orary)?[\s-]+to[\s-]+perm(?:anent)?"),
    ("Fixed-term (maternity cover)", r"maternity\s+(?:leave\s+)?cover|cover(?:ing)?\s+(?:a\s+)?maternity"),
    ("Fixed-term", r"fixed[\s-]+term|\bftc\b"),
    ("Apprenticeship", r"apprenticeship"),
    ("Zero hours", r"zero[\s-]+hours?"),
    ("Seasonal", r"\bseasonal\b|christmas\s+temp|peak\s+season"),
    ("Temporary", r"\btemporary\b|\btemp\b(?!\w)|\btemp\s+role|ongoing\s+temp"),
    ("Contract", r"\bcontract\s+(?:role|position|basis|opportunity|job)|\bon\s+a\s+contract\b|\d+[\s-]*(?:week|month)s?\s+contract|\bcontractor\b|\boutside\s+ir35|\binside\s+ir35"),
    ("Permanent", r"\bpermanent\b|\bperm\b"),
]


def _duration_value(raw: str) -> int:
    return int(raw) if raw.isdigit() else _WORD_NUMS[raw.lower()]


def parse_contract(text: str, structured: dict | None = None, title: str = "") -> dict:
    text = text or ""
    structured = structured or {}

    ctype = None
    for label, pattern in _CONTRACT_TYPES:
        if re.search(pattern, text, re.I):
            ctype = label
            break
    if not ctype and structured.get("contract_type"):
        ctype = str(structured["contract_type"]).replace("_", " ").strip().capitalize()

    length = None
    for m in _DURATION_RE.finditer(text):
        window = text[max(0, m.start() - 45): m.end() + 45]
        if _NOT_CONTRACT_CONTEXT.search(window) and not re.search(r"contract|fixed[\s-]+term", window, re.I):
            continue
        if not _CONTRACT_CONTEXT.search(window):
            continue
        n = _duration_value(m.group(1))
        unit = m.group(2).lower()
        length = f"{n} {unit}{'s' if n != 1 else ''}"
        break
    if not length:
        until = _UNTIL_RE.search(text)
        if until:
            length = f"until {until.group(1).title()}"
    if not length and re.search(r"\bongoing\b|\bindefinite\b", text, re.I) and ctype in {"Temporary", "Contract", None}:
        length = "ongoing"
    if not length and ctype == "Permanent":
        length = "permanent"

    hours = None
    if re.search(r"\bfull[\s-]?time\b", text, re.I) or structured.get("full_time"):
        hours = "Full-time"
    if re.search(r"\bfull\s+or\s+part[\s-]?time\b|\bpart\s+or\s+full[\s-]?time\b", text, re.I):
        hours = "Full or part-time"
    elif re.search(r"\bpart[\s-]?time\b", text, re.I) or structured.get("part_time"):
        hours = "Part-time" if not hours else "Full or part-time"
    if not hours and structured.get("contract_time"):
        hours = str(structured["contract_time"]).replace("_", "-").capitalize()
    # The job title is the most reliable signal ("Part Time Cleaner").
    if re.search(r"\bpart[\s-]?time\b", title or "", re.I) and not re.search(r"\bfull\b", title, re.I):
        hours = "Part-time"
    elif re.search(r"\bfull[\s-]?time\b", title or "", re.I) and not re.search(r"\bpart\b", title, re.I):
        hours = "Full-time"

    bits = [b for b in (ctype, length if length not in {"permanent"} else None, hours) if b]
    return {"type": ctype, "length": length, "hours": hours, "display": " · ".join(bits) or NOT_STATED}


# ------------------------------------------------------------------------ shifts

_TIME = r"(?:\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm)|\d{1,2}[:.]\d{2}|noon|midday|midnight)"
_TIME_RANGE_RE = re.compile(rf"\b({_TIME})\s*(?:-|–|—|to|until|till)\s*({_TIME})", re.I)
_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_RANGE_RE = re.compile(
    r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s*(?:-|–|to)\s*(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", re.I
)
_HOURS_WEEK_RE = re.compile(
    r"\b(\d{1,2}(?:\.\d{1,2})?)(?:\s*(?:-|–|to|and)\s*(\d{1,2}(?:\.\d{1,2})?))?\s*(?:hours|hrs?)\s*"
    r"(?:per|a|each|/|p)\s*(?:week|w\b)",
    re.I,
)
_SHIFT_PATTERNS = [
    ("Days", r"\bday\s*shifts?\b|\bdays\s+only\b|\bday\s*time\s+(?:hours|shifts?|work)\b|\bdays\s+(?:mon|monday)"),
    ("Nights", r"\bnight\s*shifts?\b|\bnights\b|\bnight\s+work\b|\bovernight\b"),
    ("Evenings", r"\bevening\s*shifts?\b|\bevenings\b"),
    ("Earlies", r"\bearly\s*shifts?\b|\bearlies\b"),
    ("Lates", r"\blate\s*shifts?\b|\blates\b"),
    ("Twilight", r"\btwilight\b"),
    ("Weekends", r"\bweekends?\b|\bsaturdays?\s+and\s+sundays?\b"),
    ("Rotating shifts", r"\brotating\b|\brotational\b|\brota\b|\bshift\s+pattern\b"),
    ("Split shifts", r"\bsplit\s+shifts?\b"),
    ("Flexible hours", r"\bflexible\s+(?:hours|shifts|working)\b|\bflexi[\s-]?time\b|\bchoose\s+your\s+(?:own\s+)?shifts\b"),
    ("Overtime available", r"\bovertime\b"),
    ("On-call", r"\bon[\s-]call\b"),
]


def _tidy_time(t: str) -> str:
    t = t.lower().replace(" ", "").replace(".", ":")
    return t


def parse_shifts(text: str) -> dict:
    text = text or ""
    found: list[str] = []
    for label, pattern in _SHIFT_PATTERNS:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        before = text[max(0, m.start() - 12): m.start()].lower()
        if label == "Weekends" and re.search(r"\b(no|without|never|free)\s+$", before):
            found.append("No weekends")
            continue
        if re.search(r"\b(no|not)\s+$", before):
            continue
        found.append(label)

    m = _DAY_RANGE_RE.search(text)
    if m:
        found.insert(0, f"{m.group(1).title()}–{m.group(2).title()}")

    m = re.search(r"\b(\d)\s+on,?\s*(\d)\s+off\b", text, re.I)
    if m:
        found.append(f"{m.group(1)} on / {m.group(2)} off")
    m = re.search(r"\b(\d{1,2})[\s-]*(?:hour|hr)\s+shifts?\b", text, re.I)
    if m:
        found.append(f"{m.group(1)}-hour shifts")

    times = []
    for m in _TIME_RANGE_RE.finditer(text):
        # Skip date-like ranges such as "9.30 - 10.00" in a list of prices.
        span = f"{_tidy_time(m.group(1))}–{_tidy_time(m.group(2))}"
        if span not in times:
            times.append(span)
        if len(times) == 3:
            break
    found.extend(times)

    m = _HOURS_WEEK_RE.search(text)
    hours_per_week = None
    if m:
        lo, hi = m.group(1), m.group(2)
        found.append(f"{lo}–{hi} hrs/wk" if hi else f"{lo} hrs/wk")
        hours_per_week = float(hi or lo)
        if hours_per_week > 80:
            hours_per_week = None

    found = list(dict.fromkeys(found))
    return {"items": found, "hours_per_week": hours_per_week, "display": ", ".join(found) or NOT_STATED}


# ----------------------------------------------------------------------- summary

_BOILERPLATE = re.compile(
    r"equal opportunit|apply now|click (?:the )?apply|cookie|employment agency|employment business|"
    r"recruitment agency|acting as an|only successful|unable to respond|gdpr|privacy|data protection|"
    r"if you (?:have not|haven't) heard|due to the high volume|please note|unfortunately|"
    r"diverse and inclusive|disability confident|reference number|job ref|^ref\b|"
    r"we look forward to|don't miss out|apply today|send your cv|submit your cv|^salary|^location|^hours|^pay\b|"
    r"^job type|^contract|^benefits?:?$",
    re.I,
)
_ROLE_WORDS = re.compile(
    r"\byou will\b|\byou'll\b|\brole\b|\bresponsib|\blooking for\b|\bseeking\b|\brecruiting\b|"
    r"\bjoin\b|\bduties\b|\bposition\b|\bopportunity\b|\bwe need\b|\bhiring\b",
    re.I,
)


def summarise(text: str, title: str = "", limit: int = 260) -> str:
    candidates = []
    for sent in sentences(text or "")[:30]:
        if len(sent) < 30 or _BOILERPLATE.search(sent):
            continue
        if title and sent.strip().lower() == title.strip().lower():
            continue
        candidates.append(sent)
    if not candidates:
        return truncate((text or "").strip(), limit) or "No description available."

    start = next((i for i, s in enumerate(candidates[:10]) if _ROLE_WORDS.search(s)), 0)
    picked = candidates[start]
    if len(picked) < limit * 0.6 and start + 1 < len(candidates):
        picked = f"{picked} {candidates[start + 1]}"
    return truncate(picked, limit)


# -------------------------------------------------------------------------- all

def analyse(description: str, title: str = "", structured: dict | None = None) -> dict:
    """Run every extractor over one job description."""
    shifts = parse_shifts(description)
    return {
        "pay": parse_pay(description, structured, hours_per_week=shifts["hours_per_week"]),
        "contract": parse_contract(description, structured, title),
        "shifts": shifts,
        "summary": summarise(description, title),
    }
