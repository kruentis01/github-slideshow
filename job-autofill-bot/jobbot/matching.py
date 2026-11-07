"""Compare a scanned job with your profile: matching skills and clashes."""

from __future__ import annotations

import re

from .cv_parser import find_known_skills


def assess_fit(job: dict, profile: dict | None) -> dict:
    if not profile:
        return {"matched_skills": [], "warnings": []}
    cv = profile.get("cv", {})
    prefs = profile.get("preferences", {})
    avail = profile.get("availability", {})
    description = (job.get("description") or "").lower()

    skills = list({s.lower(): s for s in reversed([*cv.get("skills", []),
                                                    *find_known_skills(cv.get("raw_text", ""))])}.values())[::-1]
    matched = [s for s in skills if len(s) > 1 and re.search(rf"(?<!\w){re.escape(s.lower())}(?!\w)", description)]

    # Drop "forklift" when "Forklift (counterbalance)" already matched.
    matched = [s for s in matched if not any(s.lower() != o.lower() and s.lower() in o.lower() for o in matched)]

    warnings = []
    analysis = job.get("analysis", {})
    pay = analysis.get("pay", {})
    min_rate = prefs.get("min_hourly_rate")
    best_rate = pay.get("hourly_max") or pay.get("hourly_min")
    if min_rate and best_rate and best_rate < float(min_rate):
        warnings.append(f"pay below your £{float(min_rate):.2f}/hr minimum")

    shift_items = set(analysis.get("shifts", {}).get("items", []))
    if "Nights" in shift_items and not avail.get("nights"):
        warnings.append("night shifts")
    if "Weekends" in shift_items and not avail.get("weekends"):
        warnings.append("weekend work")
    if "Evenings" in shift_items and not avail.get("evenings"):
        warnings.append("evening shifts")

    ctype = analysis.get("contract", {}).get("type")
    wanted = prefs.get("contract_types") or []
    if ctype and wanted and not any(w.lower() in ctype.lower() for w in wanted):
        warnings.append(f"{ctype.lower()} (you prefer {', '.join(wanted).lower()})")

    radius = prefs.get("radius_miles")
    if radius and job.get("distance_miles") and job["distance_miles"] > float(radius):
        warnings.append(f"{job['distance_miles']:.1f} miles away")

    return {"matched_skills": matched[:12], "warnings": warnings}
