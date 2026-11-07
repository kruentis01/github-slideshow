"""First-run setup: read the CV, then ask about preferences and availability."""

from __future__ import annotations

import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import click

from . import storage
from .cv_parser import parse_cv
from .geo import PostcodeError, lookup_town, normalise_postcode

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
FULL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
CONTRACT_CHOICES = ["permanent", "temporary", "seasonal", "fixed-term", "contract", "temp to perm", "zero hours",
                    "apprenticeship"]
# UK National Living Wage (age 21+) from April 2026. Check gov.uk/national-minimum-wage-rates when it changes.
NATIONAL_LIVING_WAGE = 12.71

DEFAULT_COVER_LETTER = """Dear Hiring Manager,

I am writing to apply for the {job_title} position at {company}. {summary_sentence}

{skills_sentence} I am available to start {start_phrase} and can work {availability_phrase}.

Thank you for considering my application. I would welcome the chance to discuss how I could contribute to your team.

Kind regards,
{full_name}
{phone}
{email}"""


def resolve_start_date(answer: str, today: date | None = None) -> str:
    """Turn "immediately", "2 weeks", "1 month" or a date into YYYY-MM-DD."""
    today = today or date.today()
    text = (answer or "").strip().lower()
    if not text or text in {"now", "immediately", "asap", "immediate", "today"}:
        return today.isoformat()
    m = re.fullmatch(r"(\d+)\s*(day|week|month)s?", text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"day": 1, "week": 7, "month": 30}[unit] * n
        return (today + timedelta(days=days)).isoformat()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(answer.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    raise click.BadParameter("use 'immediately', a period like '2 weeks', or a date like 01/12/2026")


def _split_list(value: str) -> list[str]:
    return [v.strip() for v in re.split(r"[,;]", value or "") if v.strip()]


def _ask_postcode(label: str, default: str) -> str:
    while True:
        raw = click.prompt(label, default=default or None, show_default=bool(default))
        try:
            return normalise_postcode(raw)
        except PostcodeError as exc:
            click.secho(f"  {exc}", fg="red")


def _ask_days(default: list[str]) -> list[str]:
    while True:
        raw = click.prompt("  Which days can you work? (e.g. Mon-Fri, or Mon,Wed,Sat)", default=",".join(default))
        days = parse_days(raw)
        if days:
            return days
        click.secho("  Use day names like Mon, Tue … Sun, or a range like Mon-Fri.", fg="red")


def parse_days(raw: str) -> list[str]:
    raw = raw.strip().lower()
    if raw in {"any", "all", "every day", "7 days"}:
        return DAYS[:]
    picked: set[int] = set()
    for part in _split_list(raw.replace(" ", ",") if "-" not in raw else raw):
        part = part.strip()
        if "-" in part:
            a, b = (p.strip()[:3].title() for p in part.split("-", 1))
            if a in DAYS and b in DAYS:
                i, j = DAYS.index(a), DAYS.index(b)
                picked.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
        elif part[:3].title() in DAYS:
            picked.add(DAYS.index(part[:3].title()))
    return [DAYS[i] for i in sorted(picked)]


def run_setup(cv_path: str, existing: dict | None = None) -> dict:
    existing = existing or {}
    src = Path(cv_path).expanduser().resolve()
    click.secho(f"\nScanning CV: {src}", bold=True)
    cv = parse_cv(src)

    # Keep a copy so the CV can still be uploaded if the original moves.
    cv_dir = storage.data_dir() / "cv"
    cv_dir.mkdir(exist_ok=True)
    stored_cv = cv_dir / src.name
    if stored_cv.resolve() != src:
        shutil.copy2(src, stored_cv)

    old_cv = existing.get("cv", {})
    click.secho("\n1/3  Check the details read from your CV (press Enter to keep)", fg="cyan", bold=True)

    def ask(label, key, required=False):
        default = cv.get(key) or old_cv.get(key) or ""
        # default=None makes click insist on an answer; "" lets Enter leave it blank.
        value = click.prompt(f"  {label}", default=default or (None if required else ""),
                             show_default=bool(default))
        cv[key] = value.strip()

    ask("Full name", "full_name", required=True)
    parts = cv["full_name"].split()
    cv["first_name"] = parts[0] if parts else ""
    cv["last_name"] = parts[-1] if len(parts) > 1 else ""
    ask("Email", "email", required=True)
    ask("Phone", "phone")
    ask("Address (first line)", "address")
    if not cv.get("town") and not old_cv.get("town") and cv.get("postcode"):
        cv["town"] = lookup_town(cv["postcode"])  # e.g. CV1 2LA -> Coventry
    ask("Town / city", "town")
    cv["postcode"] = _ask_postcode("  Home postcode", cv.get("postcode") or old_cv.get("postcode", ""))
    ask("LinkedIn URL", "linkedin")
    ask("Current / most recent job title", "current_title")
    ask("Current / most recent employer", "current_employer")
    skills_default = ", ".join(cv.get("skills") or old_cv.get("skills") or [])
    if skills_default:
        click.echo(f"  Skills found: {skills_default}")
    cv["skills"] = _split_list(click.prompt("  Key skills (comma separated, Enter to keep the list above)",
                                            default=skills_default, show_default=False))
    click.echo(f"  ({len(cv['skills'])} skills saved)")

    old_prefs = existing.get("preferences", {})
    click.secho("\n2/3  Job preferences", fg="cyan", bold=True)
    prefs = {
        "keywords": click.prompt("  Job titles / keywords to search for",
                                 default=old_prefs.get("keywords") or cv.get("current_title") or "")
                    .strip(),
        "postcode": _ask_postcode("  Search around which postcode?", old_prefs.get("postcode") or cv["postcode"]),
        "radius_miles": click.prompt("  Search radius (miles)", type=click.FloatRange(1, 100),
                                     default=old_prefs.get("radius_miles", 10)),
        "min_hourly_rate": click.prompt("  Minimum hourly rate in £ (0 for no minimum)", type=click.FloatRange(0, 500),
                                        default=old_prefs.get("min_hourly_rate") or 0) or None,
        "expected_hourly_rate": click.prompt("  Hourly rate to put on applications (£)", type=click.FloatRange(0, 500),
                                             default=old_prefs.get("expected_hourly_rate") or NATIONAL_LIVING_WAGE),
    }
    click.echo(f"  Contract types: {', '.join(CONTRACT_CHOICES)}")
    contracts = click.prompt("  Which contract types do you want? (comma separated, or 'any')",
                             default=", ".join(old_prefs.get("contract_types") or []) or "any")
    prefs["contract_types"] = [] if contracts.strip().lower() == "any" else _split_list(contracts)

    old_av = existing.get("availability", {})
    click.secho("\n3/3  Availability", fg="cyan", bold=True)
    while True:
        start = click.prompt("  When can you start? (immediately, 2 weeks, or a date like 01/12/2026)",
                             default=old_av.get("start_answer") or "immediately")
        try:
            start_date = resolve_start_date(start)
            break
        except click.BadParameter as exc:
            click.secho(f"  {exc.message}", fg="red")
    availability = {
        "start_answer": start,
        "start_date": start_date,
        "notice_period": click.prompt("  Notice period", default=old_av.get("notice_period") or
                                      ("None" if start.strip().lower() in {"immediately", "now", "asap"} else start)),
        "days": _ask_days(old_av.get("days") or DAYS[:5]),
        "hours_per_week": click.prompt("  Hours per week you want", type=click.IntRange(1, 80),
                                       default=old_av.get("hours_per_week", 37)),
        "days_shifts": click.confirm("  Can you work day shifts?", default=old_av.get("days_shifts", True)),
        "evenings": click.confirm("  Can you work evenings?", default=old_av.get("evenings", False)),
        "nights": click.confirm("  Can you work nights?", default=old_av.get("nights", False)),
        "weekends": click.confirm("  Can you work weekends?", default=old_av.get("weekends", False)),
        "right_to_work": click.confirm("  Do you have the right to work in the UK?",
                                       default=old_av.get("right_to_work", True)),
        "needs_sponsorship": False,
        "driving_licence": click.confirm("  Do you hold a full UK driving licence?",
                                         default=old_av.get("driving_licence", False)),
        "over_18": click.confirm("  Are you 18 or over?", default=old_av.get("over_18", True)),
    }
    availability["needs_sponsorship"] = not availability["right_to_work"]
    availability["own_transport"] = availability["driving_licence"] and click.confirm(
        "  Do you have your own transport?", default=old_av.get("own_transport", False))

    profile = {
        "cv_path": str(stored_cv),
        "cv": cv,
        "preferences": prefs,
        "availability": availability,
        "cover_letter_template": existing.get("cover_letter_template", DEFAULT_COVER_LETTER),
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    storage.save_profile(profile)
    click.secho(f"\nProfile saved to {storage.profile_path()}", fg="green")
    click.echo("Edit the cover letter template there any time (key: cover_letter_template).")
    return profile


def availability_phrase(av: dict) -> str:
    days = av.get("days") or []
    if days == DAYS:
        day_text = "any day of the week"
    elif days == DAYS[:5]:
        day_text = "Monday to Friday"
    elif days and [DAYS.index(d) for d in days] == list(range(DAYS.index(days[0]), DAYS.index(days[-1]) + 1)) \
            and len(days) > 2:
        day_text = f"{FULL_DAYS[DAYS.index(days[0])]} to {FULL_DAYS[DAYS.index(days[-1])]}"
    elif days:
        day_text = ", ".join(days)
    else:
        day_text = "flexible days"
    shifts = [name for key, name in (("days_shifts", "days"), ("evenings", "evenings"),
                                     ("nights", "nights"), ("weekends", "weekends")) if av.get(key)]
    text = day_text
    if shifts:
        text += f" ({', '.join(shifts)})"
    if av.get("hours_per_week"):
        text += f", up to {av['hours_per_week']} hours per week"
    return text


def start_phrase(av: dict) -> str:
    iso = av.get("start_date")
    if not iso or iso <= date.today().isoformat():
        return "immediately"
    d = date.fromisoformat(iso)
    return f"from {d.day} {d.strftime('%B %Y')}"
