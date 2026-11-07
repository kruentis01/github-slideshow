"""Offline demo listings, so every command can be tried without API keys.

Each demo job's application link opens a local practice form bundled with
JobBot, so ``jobbot apply`` can be tried safely too.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..geo import haversine_miles
from .base import JobSource, Listing

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"


class DemoSource(JobSource):
    name = "demo"

    def __init__(self, origin: tuple[float, float] | None = None):
        self.origin = origin

    def search(self, keywords, postcode, radius_miles, limit):
        data = json.loads((DEMO_DIR / "listings.json").read_text(encoding="utf-8"))
        form = (DEMO_DIR / "apply_form.html").resolve().as_uri()
        words = [w for w in (keywords or "").lower().split() if w]
        # Demo jobs are placed relative to the search postcode, so pretend the
        # search point is the demo centre when we couldn't geocode it.
        listings = []
        for item in data:
            listing = Listing(
                source=self.name,
                source_id=item["id"],
                title=item["title"],
                company=item["company"],
                location=item["location"],
                url=f"{form}?job={item['id']}",
                apply_url=f"{form}?job={item['id']}",
                description=item["description"],
                posted=item.get("posted", ""),
                distance_miles=item["distance_miles"],
            )
            if self.origin and item.get("lat"):
                listing.latitude, listing.longitude = item["lat"], item["lon"]
                listing.distance_miles = round(haversine_miles(self.origin, (item["lat"], item["lon"])), 1)
            if listing.distance_miles is not None and listing.distance_miles > radius_miles:
                continue
            haystack = f"{listing.title} {listing.description}".lower()
            if words and not any(w in haystack for w in words):
                continue
            listings.append(listing)
        return listings[:limit]
