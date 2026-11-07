"""The ``scan`` command: search near a postcode, read each advert, save results."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from . import storage
from .extract import analyse
from .geo import PostcodeError, bulk_lookup, geocode, haversine_miles, normalise_postcode
from .matching import assess_fit
from .sources import AdzunaSource, DemoSource, JobSource, ReedSource, SourceError


def build_sources(names: list[str], origin, fetch_pages: bool) -> list[JobSource]:
    sources: list[JobSource] = []
    for name in names:
        if name == "reed":
            sources.append(ReedSource())
        elif name == "adzuna":
            sources.append(AdzunaSource(fetch_pages=fetch_pages))
        elif name == "demo":
            sources.append(DemoSource(origin))
    return sources


def scan(
    keywords: str,
    postcode: str,
    radius_miles: float,
    sources: list[JobSource],
    limit: int = 50,
    profile: dict | None = None,
    log: Callable[[str], None] = print,
    replace: bool = False,
) -> list[dict]:
    postcode = normalise_postcode(postcode)
    origin = geocode(postcode)
    if origin is None:
        log(f"Couldn't geocode {postcode} (offline?) — relying on each job board's own radius filter.")

    scanned: list[dict] = []
    for source in sources:
        ready, why = source.available()
        if not ready:
            log(f"[{source.name}] skipped: {why}")
            continue
        try:
            listings = source.search(keywords, postcode, radius_miles, limit)
        except SourceError as exc:
            log(f"[{source.name}] {exc}")
            continue
        log(f"[{source.name}] {len(listings)} listings within {radius_miles:g} miles of {postcode}")
        _place_postcode_locations(listings)

        for i, listing in enumerate(listings, 1):
            try:
                listing = source.fetch_details(listing)
            except SourceError as exc:
                log(f"  ! couldn't read full advert for '{listing.title}': {exc}")
            if origin and listing.latitude is not None and listing.longitude is not None:
                listing.distance_miles = round(haversine_miles(origin, (listing.latitude, listing.longitude)), 1)
            if listing.distance_miles is not None and listing.distance_miles > radius_miles + 0.5:
                continue

            job = listing.to_dict()
            job["analysis"] = analyse(listing.description, listing.title, listing.structured)
            job["fit"] = assess_fit(job, profile)
            job["scanned_at"] = datetime.now().isoformat(timespec="seconds")
            scanned.append(job)
            log(f"  {i:>3}. {listing.title[:60]} — {job['analysis']['pay']['display']}")

    merge_jobs(storage.load_jobs(), scanned, replace=replace)
    return scanned


def _as_postcode(location: str) -> str | None:
    try:
        return normalise_postcode(location) if location and len(location.replace(" ", "")) >= 5 else None
    except PostcodeError:
        return None


def _place_postcode_locations(listings) -> None:
    """Reed sometimes gives a bare postcode ("CV32TX") as the location: show the
    town and position it so the distance can be worked out."""
    codes = {lst.location: _as_postcode(lst.location) for lst in listings if lst.latitude is None}
    codes = {k: v for k, v in codes.items() if v}
    if not codes:
        return
    found = bulk_lookup(list(codes.values()))
    for lst in listings:
        pc = codes.get(lst.location)
        if not pc:
            continue
        info = found.get(pc)
        if info:
            lst.latitude, lst.longitude = info["lat"], info["lon"]
        town = info["town"] if info else ""
        lst.location = f"{town} ({pc})" if town else pc


def merge_jobs(existing: list[dict], scanned: list[dict], replace: bool = False) -> list[dict]:
    """Merge new results into saved ones, keeping your toggles and application status."""
    old = {j["id"]: j for j in existing}
    merged: dict[str, dict] = {} if replace else dict(old)
    for job in scanned:
        prev = old.get(job["id"], {})
        job["selected"] = prev.get("selected", False)
        job["application"] = prev.get("application")
        merged[job["id"]] = job
    # Drop duplicates of the same advert posted on two boards (same title + company).
    seen: dict[tuple, str] = {}
    result = []
    for job in merged.values():
        key = (job["title"].strip().lower(), (job.get("company") or "").strip().lower())
        if key in seen:
            continue
        seen[key] = job["id"]
        result.append(job)
    storage.save_jobs(result)
    return result
