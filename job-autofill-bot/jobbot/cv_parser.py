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
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
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


def _split_skill_items(lines: list[str]) -> list[str]:
    items: list[str] = []
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


def _guess_current_role(lines: list[str]) -> tuple[str, str]:
    """Return (job title, employer) from the first entry of the experience section."""
    for line in lines[:4]:
        clean = _DATE_RANGE_RE.sub("", line).strip(" |,-–()")
        if not clean or len(clean) > 90:
            continue
        for sep in (" at ", " - ", " – ", " | ", ", "):
            if sep in clean:
                title, employer = clean.split(sep, 1)
                return title.strip(), employer.strip(" |,-–")
        return clean, ""
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


def parse_cv(path: str | Path) -> dict:
    text = extract_text(path)
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
            if len(parts) == 1 and not re.match(r"\d", parts[0]):
                town = parts[0]  # just "Manchester M32 0AB"
            elif parts:
                address = parts[0]
                town = parts[-1] if len(parts) > 1 else ""
            break

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
