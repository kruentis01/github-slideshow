"""Read a CV (PDF, DOCX or plain text) and pull out the details forms ask for.

Extraction is best effort: ``jobbot setup`` shows every value and lets you
correct it before anything is saved.
"""

from __future__ import annotations

import re
from pathlib import Path

from .geo import find_postcode
from .textutil import normalise_whitespace

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(
    r"(?:(?:\+44\s?\(?0?\)?\s?|0)(?:7\d{3}\s?\d{3}\s?\d{3}|[1-3]\d{2,3}\s?\d{3}\s?\d{3,4}|\d{2}\s?\d{4}\s?\d{4}))"
)
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w%-]+/?", re.I)
URL_RE = re.compile(r"(?:https?://|www\.)[^\s,;]+", re.I)

SECTION_ALIASES = {
    "summary": ["profile", "personal profile", "personal statement", "summary", "professional summary",
                "about me", "career objective", "objective", "career summary"],
    "skills": ["skills", "key skills", "core skills", "technical skills", "skills and abilities",
               "core competencies", "competencies", "areas of expertise", "key competencies"],
    "experience": ["experience", "work experience", "employment history", "employment", "work history",
                   "career history", "professional experience", "relevant experience"],
    "education": ["education", "education and qualifications", "qualifications", "academic qualifications",
                  "training", "certifications", "certificates", "courses"],
    "other": ["interests", "hobbies", "references", "additional information", "languages", "volunteering",
              "achievements", "contact", "contact details", "personal details"],
}
_HEADER_LOOKUP = {alias: key for key, aliases in SECTION_ALIASES.items() for alias in aliases}

# Common skills and tickets in UK job ads. Used to spot skills even when a CV
# has no "Skills" section, and later to score how well a job matches.
KNOWN_SKILLS = [
    "customer service", "cash handling", "stock control", "merchandising", "retail", "sales",
    "forklift", "counterbalance", "reach truck", "flt", "pick and pack", "picking", "packing",
    "warehouse", "logistics", "inventory", "goods in", "dispatch", "rf scanner",
    "food hygiene", "food safety", "barista", "kitchen", "food preparation", "bartending", "waiting",
    "cleaning", "housekeeping", "care", "personal care", "dementia care", "manual handling",
    "safeguarding", "first aid", "medication administration", "nvq", "care certificate",
    "cscs", "sia", "door supervisor", "cctv", "security", "driving", "full uk driving licence",
    "cpc", "hgv", "class 1", "class 2", "7.5 tonne",
    "microsoft office", "excel", "word", "outlook", "powerpoint", "data entry", "administration",
    "reception", "call centre", "telephone", "scheduling", "bookkeeping", "payroll", "sage", "xero",
    "teamwork", "communication", "leadership", "time management", "problem solving",
    "python", "javascript", "typescript", "java", "c#", "sql", "react", "node.js", "aws", "azure",
    "docker", "git", "html", "css", "project management", "agile", "scrum", "jira",
    "teaching", "tutoring", "childcare", "dbs", "enhanced dbs", "construction", "labouring",
    "plumbing", "electrical", "carpentry", "painting", "welding", "maintenance", "machine operation",
]


def extract_text(path: str | Path) -> str:
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"CV not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            text = "\n".join(_pdf_page_text(page) for page in pdf.pages)
    elif suffix == ".docx":
        import docx

        doc = docx.Document(str(path))
        lines = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                lines.append(" | ".join(cell.text for cell in row.cells))
        text = "\n".join(lines)
    elif suffix in {".txt", ".md", ".text", ""}:
        text = path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".doc":
        raise ValueError("Old .doc files can't be read. Save your CV as PDF or .docx and try again.")
    else:
        raise ValueError(f"Unsupported CV format '{suffix}'. Use PDF, DOCX or TXT.")
    text = normalise_whitespace(text)
    if not text:
        raise ValueError(
            "No text could be read from the CV. If it's a scanned image, export it as a text PDF or DOCX."
        )
    return text


def _group_lines(words: list[dict], tolerance: float = 3) -> list[tuple[float, str]]:
    """Group pdfplumber words into (top, text) lines, left to right."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - word["top"]) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [(ln[0]["top"], " ".join(w["text"] for w in sorted(ln, key=lambda w: w["x0"]))) for ln in lines]


def _pdf_page_text(page) -> str:
    """Read a PDF page, keeping two-column layouts apart.

    A plain text dump of a two-column CV interleaves the columns line by line
    (skills mixed into work history). If a vertical gap splits the page body
    into two columns, read the header across the full width, then the left
    column, then the right column.
    """
    words = page.extract_words(x_tolerance=1.5)
    plain = page.extract_text() or ""
    if len(words) < 40:
        return plain
    width = page.width
    best = None
    for x in range(int(width * 0.3), int(width * 0.72), 2):
        left = sum(1 for w in words if w["x1"] <= x)
        right = sum(1 for w in words if w["x0"] >= x)
        if min(left, right) < len(words) * 0.15:
            continue
        crossing = len(words) - left - right
        if best is None or crossing < best[0]:
            best = (crossing, x)
    if best is None or best[0] > len(words) * 0.04:
        return plain  # single column
    split = best[1]

    def column(w):
        return "left" if (w["x0"] + w["x1"]) / 2 < split else "right"

    cols = {"left": [w for w in words if column(w) == "left"], "right": [w for w in words if column(w) == "right"]}
    # The header (name, contact details) runs across the page above the first section heading.
    heading_tops = [top for side in cols.values() for top, text in _group_lines(side) if _header_key(text.rstrip(":"))]
    body_top = min(heading_tops) - 1 if heading_tops else 0
    header = _group_lines([w for w in words if w["top"] < body_top])
    parts = [text for _, text in header]
    for side in ("left", "right"):
        parts.append("")
        parts.extend(text for _, text in _group_lines([w for w in cols[side] if w["top"] >= body_top]))
    return "\n".join(parts)


def _repair_email(text: str) -> str:
    """Rejoin UK emails that wrap in the PDF, e.g. "name@uni.ac." + newline + "uk"."""
    def fix(m):
        return m.group(1) + "uk"
    if re.search(r"(?m)^\s*uk\b|\buk\s*$", text, re.I):
        text = re.sub(r"([\w.+-]+@[\w-]+(?:\.[\w-]+)*\.(?:ac|co|gov|nhs|sch|ltd|plc|org|net)\.)(?![\w-])", fix, text)
    return text


def _header_key(line: str) -> str | None:
    cleaned = re.sub(r"[^a-z& ]", "", line.lower().replace("&", "and")).strip()
    if not cleaned or len(cleaned) > 40:
        return None
    return _HEADER_LOOKUP.get(cleaned)


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key = _header_key(stripped.rstrip(":"))
        if key:
            current = key
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(stripped)
    return sections


def _guess_name(lines: list[str]) -> str:
    for line in lines[:6]:
        candidate = re.sub(r"\b(curriculum vitae|cv|resume|résumé)\b", "", line, flags=re.I).strip(" -|:")
        if EMAIL_RE.search(candidate) or re.search(r"\d", candidate):
            continue
        words = candidate.split()
        if 2 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z][A-Za-z'\-.]*", w) for w in words):
            return " ".join(w if not w.isupper() else w.title() for w in words)
    return ""


_BULLET_RE = re.compile(r"^\s*[•▪●◦·*–-]\s+")


def _merge_bullets(lines: list[str]) -> list[str]:
    """Join a bullet's wrapped continuation lines back onto it."""
    merged: list[str] = []
    for line in lines:
        if _BULLET_RE.match(line) or not merged:
            merged.append(_BULLET_RE.sub("", line).strip())
        else:
            merged[-1] = f"{merged[-1]} {line.strip()}"
    return merged


def _split_skill_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    if sum(1 for ln in lines if _BULLET_RE.match(ln)) >= 2:
        # One skill per bullet; commas inside a bullet belong to that skill.
        for bullet in _merge_bullets(lines):
            bullet = re.sub(r"[\s,;.]+$|\s+(and|&)$", "", bullet).strip()
            if 2 <= len(bullet) <= 70 and len(bullet.split()) <= 10 and not _DATE_RANGE_RE.search(bullet):
                items.append(bullet)
        return items
    for line in lines:
        for part in re.split(r"[,;•|·▪●◦]|\s{2,}|\s-\s", line):
            part = part.strip(" -*–:\t")
            if 2 <= len(part) <= 40 and len(part.split()) <= 5:
                items.append(part)
    return items


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def find_known_skills(text: str) -> list[str]:
    lowered = text.lower()
    return [s for s in KNOWN_SKILLS if re.search(rf"(?<![\w]){re.escape(s)}(?![\w])", lowered)]


_DATE_RANGE_RE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?(19|20)\d{2}\s*(?:-|–|to)\s*"
    r"(present|current|now|((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?(19|20)\d{2})",
    re.I,
)


_CONTRACT_PREFIX_RE = re.compile(
    r"^(?:\d+[\s-]*(?:week|month|year)s?\s+)?(?:ftc|fixed[\s-]term|temporary|temp|part[\s-]time|full[\s-]time|"
    r"seasonal|contract|casual|zero[\s-]hours?)\b\s*|^\d+[\s-]*(?:week|month|year)s?\s+",
    re.I,
)


def _split_role(line: str) -> tuple[str, str]:
    clean = _DATE_RANGE_RE.sub("", line).strip(" |,-–()")
    title, employer = clean, ""
    for sep in (" at ", " - ", " – ", " — ", " | ", ", "):
        if sep in clean:
            title, employer = clean.split(sep, 1)
            break
    while True:  # "4 Month FTC Packing Operator" -> "Packing Operator"
        shorter = _CONTRACT_PREFIX_RE.sub("", title.strip(), count=1)
        if shorter == title.strip() or not shorter:
            break
        title = shorter
    return title.strip(), employer.strip(" |,-–")


def _guess_current_role(lines: list[str]) -> tuple[str, str]:
    """Return (job title, employer): the job marked "Present", else the first one listed."""
    entries = []  # (title, employer, is_current)
    for i, line in enumerate(lines):
        if _BULLET_RE.match(line) or len(line) > 90:
            continue
        own_dates = _DATE_RANGE_RE.search(line)
        if own_dates and not _DATE_RANGE_RE.sub("", line).strip(" |,-–()"):
            continue  # a line that is only dates
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        dates = own_dates or _DATE_RANGE_RE.search(nxt)
        if not dates:
            continue
        title, employer = _split_role(line)
        if title:
            entries.append((title, employer, bool(re.search(r"present|current|now", dates.group(3), re.I))))
    if entries:
        current = next((e for e in entries if e[2]), entries[0])
        return current[0], current[1]
    for line in lines[:4]:  # no dates at all: fall back to the first line
        if not _BULLET_RE.match(line) and len(line) <= 90:
            return _split_role(line)
    return "", ""


def _years_experience(text: str) -> int | None:
    years = []
    for m in _DATE_RANGE_RE.finditer(text):
        start = int(re.search(r"(19|20)\d{2}", m.group(0)).group(0))
        end_raw = m.group(3)
        end_year = re.search(r"(19|20)\d{2}", end_raw)
        end = int(end_year.group(0)) if end_year else None
        years.append((start, end))
    if not years:
        return None
    from datetime import date

    first = min(s for s, _ in years)
    last = max((e or date.today().year) for _, e in years)
    return max(0, last - first)


_STREET_RE = re.compile(
    r"\b\d+[a-z]?,?\s+(?:[A-Z][\w'-]*\s+){1,4}(?:Road|Rd|Street|St|Avenue|Ave|Lane|Ln|Close|Drive|Dr|Way|"
    r"Crescent|Court|Ct|Place|Pl|Grove|Terrace|Gardens|Hill|Park|Walk|Row|Square|View|Mews|Rise|Green)\b\.?"
)


def parse_cv(path: str | Path) -> dict:
    text = _repair_email(extract_text(path))
    sections = split_sections(text)
    header = sections.get("header", [])

    email = EMAIL_RE.search(text)
    phone = PHONE_RE.search(text)
    linkedin = LINKEDIN_RE.search(text)
    websites = [u for u in URL_RE.findall(text) if "linkedin.com" not in u.lower()]

    skills = _split_skill_items(sections.get("skills", []))
    listed = " | ".join(skills).lower()
    skills = _dedupe(skills + [s for s in find_known_skills(text) if s not in listed])

    title, employer = _guess_current_role(sections.get("experience", []))
    name = _guess_name(header) or _guess_name(text.splitlines())
    first, _, last = name.partition(" ")

    address, town = "", ""
    for line in header:
        postcode = find_postcode(line)
        if postcode and not EMAIL_RE.search(line):
            # e.g. "12 Chester Road, Stretford, Manchester M32 0AB"
            before = re.split(r"\b" + postcode.split()[0], line, flags=re.I)[0]
            parts = [p.strip() for p in before.split(",") if p.strip()]
            parts = [p for p in parts if p.lower() not in {"uk", "united kingdom", "england"}]
            if len(parts) == 1 and not re.match(r"\d", parts[0]):
                town = parts[0]  # just "Manchester M32 0AB"
            elif parts:
                address = parts[0]
                town = parts[-1] if len(parts) > 1 else ""
            break
    if not _STREET_RE.search(address or ""):
        street = _STREET_RE.search("\n".join(header))
        if street:
            address = street.group(0).rstrip(".,")
    if town and (len(town) < 3 or _STREET_RE.search(town) or re.search(r"\d|@", town)):
        town = ""

    return {
        "full_name": name,
        "first_name": first,
        "last_name": last.split()[-1] if last else "",
        "email": email.group(0) if email else "",
        "phone": re.sub(r"\s+", " ", phone.group(0)).strip() if phone else "",
        "postcode": find_postcode(text) or "",
        "address": address,
        "town": town,
        "linkedin": linkedin.group(0) if linkedin else "",
        "website": websites[0] if websites else "",
        "current_title": title,
        "current_employer": employer,
        "years_experience": _years_experience("\n".join(sections.get("experience", []))),
        "summary": " ".join(sections.get("summary", []))[:1200],
        "skills": skills[:40],
        "education": sections.get("education", [])[:10],
        "raw_text": text,
    }
