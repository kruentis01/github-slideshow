import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def jobbot_home(tmp_path, monkeypatch):
    """Every test gets its own empty data folder and no API keys."""
    home = tmp_path / "jobbot-home"
    monkeypatch.setenv("JOBBOT_HOME", str(home))
    for key in ("REED_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY"):
        monkeypatch.delenv(key, raising=False)
    # Never hit postcodes.io from tests.
    monkeypatch.setattr("jobbot.scanner.geocode", lambda pc: None)
    monkeypatch.setattr("jobbot.profile.lookup_town", lambda pc: "")
    monkeypatch.setattr("jobbot.scanner.bulk_lookup", lambda pcs: {})
    return home


@pytest.fixture
def sample_cv():
    return FIXTURES / "sample_cv.txt"


@pytest.fixture
def profile(sample_cv):
    from jobbot.cv_parser import parse_cv
    from jobbot.profile import DEFAULT_COVER_LETTER

    cv = parse_cv(sample_cv)
    return {
        "cv_path": str(sample_cv),
        "cv": cv,
        "preferences": {"keywords": "", "postcode": "M32 0AB", "radius_miles": 15,
                        "min_hourly_rate": 12.5, "expected_hourly_rate": 13.5, "contract_types": []},
        "availability": {"start_date": "2026-12-01", "notice_period": "1 week", "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                         "hours_per_week": 37, "days_shifts": True, "evenings": False, "nights": False,
                         "weekends": False, "right_to_work": True, "needs_sponsorship": False,
                         "driving_licence": False, "own_transport": False},
        "cover_letter_template": DEFAULT_COVER_LETTER,
    }


def demo_listings():
    return json.loads((Path(__file__).parents[1] / "jobbot" / "demo" / "listings.json").read_text())
