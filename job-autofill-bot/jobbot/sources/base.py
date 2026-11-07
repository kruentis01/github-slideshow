"""Common shape for every job source."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field


@dataclass
class Listing:
    source: str
    source_id: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""            # the advert
    apply_url: str = ""      # where the application form lives, if different
    description: str = ""    # full plain-text description when available
    posted: str = ""
    latitude: float | None = None
    longitude: float | None = None
    distance_miles: float | None = None
    structured: dict = field(default_factory=dict)  # salary/contract fields from the API

    @property
    def id(self) -> str:
        digest = hashlib.sha1(f"{self.source}:{self.source_id}".encode()).hexdigest()
        return digest[:8]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["id"] = self.id
        return data


class SourceError(RuntimeError):
    pass


class JobSource:
    name = "base"

    def available(self) -> tuple[bool, str]:
        """Return (ready, reason) — e.g. False when an API key is missing."""
        return True, ""

    def search(self, keywords: str, postcode: str, radius_miles: float, limit: int) -> list[Listing]:
        raise NotImplementedError

    def fetch_details(self, listing: Listing) -> Listing:
        """Fill in the full description. Default: keep what search returned."""
        return listing
