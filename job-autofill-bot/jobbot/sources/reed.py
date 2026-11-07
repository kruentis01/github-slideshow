"""Reed.co.uk Jobseeker API — https://www.reed.co.uk/developers/jobseeker

Free key; searches by postcode with a radius in miles, and the details call
returns the full job description, so this is the recommended source.
"""

from __future__ import annotations

import os
import time

import requests

from ..textutil import html_to_text
from .base import JobSource, Listing, SourceError

API = "https://www.reed.co.uk/api/1.0"


class ReedSource(JobSource):
    name = "reed"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None, delay: float = 0.3):
        self.api_key = api_key or os.environ.get("REED_API_KEY", "")
        self.http = session or requests.Session()
        self.delay = delay

    def available(self):
        if not self.api_key:
            return False, "set REED_API_KEY (free key from reed.co.uk/developers/jobseeker)"
        return True, ""

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            resp = self.http.get(f"{API}{path}", params=params, auth=(self.api_key, ""), timeout=20)
        except requests.RequestException as exc:
            raise SourceError(f"Reed request failed: {exc}") from exc
        if resp.status_code == 401:
            raise SourceError("Reed rejected the API key (401). Check REED_API_KEY.")
        if resp.status_code == 429:
            raise SourceError("Reed rate limit reached (429). Wait a few minutes and scan again.")
        if not resp.ok:
            raise SourceError(f"Reed returned HTTP {resp.status_code}")
        return resp.json()

    def search(self, keywords, postcode, radius_miles, limit):
        listings: list[Listing] = []
        skip = 0
        while len(listings) < limit:
            take = min(100, limit - len(listings))
            data = self._get("/search", {
                "keywords": keywords,
                "locationName": postcode,
                "distanceFromLocation": int(round(radius_miles)),
                "resultsToTake": take,
                "resultsToSkip": skip,
            })
            results = data.get("results") or []
            for r in results:
                listings.append(Listing(
                    source=self.name,
                    source_id=str(r["jobId"]),
                    title=r.get("jobTitle", ""),
                    company=r.get("employerName", ""),
                    location=r.get("locationName", ""),
                    url=r.get("jobUrl", ""),
                    apply_url=r.get("jobUrl", ""),
                    description=html_to_text(r.get("jobDescription", "")),
                    posted=r.get("date", ""),
                    structured={
                        "salary_min": r.get("minimumSalary"),
                        "salary_max": r.get("maximumSalary"),
                    },
                ))
            if len(results) < take:
                break
            skip += take
        return listings[:limit]

    def fetch_details(self, listing):
        time.sleep(self.delay)
        d = self._get(f"/jobs/{listing.source_id}")
        listing.description = html_to_text(d.get("jobDescription", "")) or listing.description
        listing.url = d.get("jobUrl") or listing.url
        listing.apply_url = d.get("externalUrl") or listing.url
        listing.structured.update({
            "salary_min": d.get("minimumSalary") or listing.structured.get("salary_min"),
            "salary_max": d.get("maximumSalary") or listing.structured.get("salary_max"),
            "salary_type": d.get("salaryType"),
            "contract_type": d.get("contractType"),
            "full_time": d.get("fullTime"),
            "part_time": d.get("partTime"),
        })
        return listing
