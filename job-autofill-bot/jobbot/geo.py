"""UK postcode handling: validation, formatting, geocoding and distances."""

from __future__ import annotations

import math
import re

import requests

# Covers every format of UK postcode, including GIR 0AA.
POSTCODE_RE = re.compile(
    r"\b(GIR ?0AA|[A-PR-UWYZ](?:[0-9]{1,2}|[A-HK-Y][0-9]{1,2}|[0-9][A-HJKS-UW]|[A-HK-Y][0-9][ABEHMNPRV-Y])"
    r" ?[0-9][ABD-HJLNP-UW-Z]{2})\b",
    re.I,
)
OUTCODE_RE = re.compile(
    r"^(?:[A-PR-UWYZ](?:[0-9]{1,2}|[A-HK-Y][0-9]{1,2}|[0-9][A-HJKS-UW]|[A-HK-Y][0-9][ABEHMNPRV-Y]))$",
    re.I,
)

POSTCODES_IO = "https://api.postcodes.io"
MILES_TO_KM = 1.609344


class PostcodeError(ValueError):
    pass


def normalise_postcode(raw: str) -> str:
    """Return a postcode in canonical form, e.g. ``"sw1a1aa"`` -> ``"SW1A 1AA"``.

    A bare outcode such as ``"M1"`` or ``"LS6"`` is accepted too.
    """
    compact = re.sub(r"\s+", "", raw or "").upper()
    if OUTCODE_RE.match(compact):
        return compact
    if not POSTCODE_RE.fullmatch(compact):
        raise PostcodeError(f"'{raw}' doesn't look like a UK postcode")
    return f"{compact[:-3]} {compact[-3:]}"


def find_postcode(text: str) -> str | None:
    match = POSTCODE_RE.search(text or "")
    return normalise_postcode(match.group(1)) if match else None


def geocode(postcode: str, timeout: float = 10) -> tuple[float, float] | None:
    """Look up a postcode's latitude and longitude with postcodes.io (free, no key).

    Returns None when the lookup fails, so callers can carry on without distances.
    """
    pc = normalise_postcode(postcode)
    path = "outcodes" if " " not in pc else "postcodes"
    try:
        resp = requests.get(f"{POSTCODES_IO}/{path}/{pc.replace(' ', '')}", timeout=timeout)
        if resp.status_code == 404:
            raise PostcodeError(f"postcodes.io doesn't know the postcode '{pc}'")
        resp.raise_for_status()
        result = resp.json().get("result") or {}
        if result.get("latitude") is None:
            return None
        return float(result["latitude"]), float(result["longitude"])
    except requests.RequestException:
        return None


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 3958.7613 * math.asin(math.sqrt(h))


def lookup_town(postcode: str, timeout: float = 10) -> str:
    """Return the town/district for a postcode (e.g. "CV1 2LA" -> "Coventry"), or "" if unknown."""
    try:
        pc = normalise_postcode(postcode)
        resp = requests.get(f"{POSTCODES_IO}/postcodes/{pc.replace(' ', '')}", timeout=timeout)
        if not resp.ok:
            return ""
        result = resp.json().get("result") or {}
        return result.get("admin_district") or result.get("parish") or ""
    except (requests.RequestException, PostcodeError, ValueError):
        return ""


def bulk_lookup(postcodes: list[str], timeout: float = 15) -> dict[str, dict]:
    """Look up many postcodes in one call (postcodes.io allows 100 at a time).

    Returns {normalised postcode: {"lat", "lon", "town"}}; unknown or unreachable ones are left out.
    """
    out: dict[str, dict] = {}
    wanted = sorted({p for p in postcodes if p and " " in p})
    for i in range(0, len(wanted), 100):
        chunk = wanted[i:i + 100]
        try:
            resp = requests.post(f"{POSTCODES_IO}/postcodes", json={"postcodes": chunk}, timeout=timeout)
            resp.raise_for_status()
            rows = resp.json().get("result") or []
        except (requests.RequestException, ValueError):
            return out
        for row in rows:
            res = row.get("result") or {}
            if res.get("latitude") is None:
                continue
            out[normalise_postcode(row.get("query", ""))] = {
                "lat": float(res["latitude"]), "lon": float(res["longitude"]),
                "town": res.get("admin_district") or res.get("parish") or "",
            }
    return out
