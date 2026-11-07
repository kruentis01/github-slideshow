"""Adzuna API — https://developer.adzuna.com/

Free key. The search API returns only the first ~500 characters of each
description; pass ``fetch_pages=True`` (``jobbot scan --fetch-pages``) to also
download each advert page and read the full text.
"""

from __future__ import annotations

import os
import time

import requests

from ..geo import MILES_TO_KM
from ..textutil import html_to_text
from .base import JobSource, Listing, SourceError

API = "https://api.adzuna.com/v1/api/jobs/gb/search"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


class AdzunaSource(JobSource):
    name = "adzuna"

    def __init__(self, app_id=None, app_key=None, session=None, fetch_pages=False, delay=0.5):
        self.app_id = app_id or os.environ.get("ADZUNA_APP_ID", "")
        self.app_key = app_key or os.environ.get("ADZUNA_APP_KEY", "")
        self.http = session or requests.Session()
        self.fetch_pages = fetch_pages
        self.delay = delay

    def available(self):
        if not (self.app_id and self.app_key):
            return False, "set ADZUNA_APP_ID and ADZUNA_APP_KEY (free from developer.adzuna.com)"
        return True, ""

    def search(self, keywords, postcode, radius_miles, limit):
        listings: list[Listing] = []
        page = 1
        while len(listings) < limit:
            per_page = min(50, limit - len(listings))
            try:
                resp = self.http.get(f"{API}/{page}", params={
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "what": keywords,
                    "where": postcode,
                    "distance": max(1, int(round(radius_miles * MILES_TO_KM))),
                    "results_per_page": per_page,
                    "content-type": "application/json",
                }, timeout=20)
            except requests.RequestException as exc:
                raise SourceError(f"Adzuna request failed: {exc}") from exc
            if resp.status_code in (401, 403):
                raise SourceError("Adzuna rejected the app id/key. Check ADZUNA_APP_ID and ADZUNA_APP_KEY.")
            if not resp.ok:
                raise SourceError(f"Adzuna returned HTTP {resp.status_code}")
            results = resp.json().get("results") or []
            for r in results:
                listings.append(Listing(
                    source=self.name,
                    source_id=str(r.get("id")),
                    title=html_to_text(r.get("title", "")),
                    company=(r.get("company") or {}).get("display_name", ""),
                    location=(r.get("location") or {}).get("display_name", ""),
                    url=r.get("redirect_url", ""),
                    apply_url=r.get("redirect_url", ""),
                    description=html_to_text(r.get("description", "")),
                    posted=r.get("created", ""),
                    latitude=r.get("latitude"),
                    longitude=r.get("longitude"),
                    structured={
                        "salary_min": r.get("salary_min"),
                        "salary_max": r.get("salary_max"),
                        "salary_type": "per annum" if (r.get("salary_min") or 0) >= 1000 else "",
                        "contract_type": r.get("contract_type"),
                        "contract_time": r.get("contract_time"),
                    },
                ))
            if len(results) < per_page:
                break
            page += 1
        return listings[:limit]

    def fetch_details(self, listing):
        if not self.fetch_pages or not listing.url:
            return listing
        time.sleep(self.delay)
        try:
            resp = self.http.get(listing.url, headers={"User-Agent": USER_AGENT}, timeout=20)
            resp.raise_for_status()
        except requests.RequestException:
            return listing  # keep the snippet
        full = html_to_text(resp.text, focus=r"adp-body|job[-_]?description|jobdescription|description")
        if len(full) > len(listing.description):
            listing.description = full
        listing.apply_url = resp.url or listing.apply_url
        return listing
