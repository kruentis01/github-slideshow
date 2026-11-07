from jobbot import storage
from jobbot.scanner import merge_jobs, scan
from jobbot.sources import AdzunaSource, DemoSource, ReedSource


class FakeResponse:
    def __init__(self, data, status=200, url=""):
        self._data, self.status_code, self.url = data, status, url
        self.ok = status < 400
        self.text = data if isinstance(data, str) else ""

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params, kw))
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp
        raise AssertionError(f"unexpected URL {url}")


def test_reed_search_and_details():
    session = FakeSession({
        "https://www.reed.co.uk/api/1.0/search": FakeResponse({"results": [{
            "jobId": 555, "jobTitle": "Picker", "employerName": "Acme", "locationName": "Leeds",
            "jobDescription": "Short snippet", "jobUrl": "https://www.reed.co.uk/jobs/555",
            "minimumSalary": 12.5, "maximumSalary": None,
        }]}),
        "https://www.reed.co.uk/api/1.0/jobs/555": FakeResponse({
            "jobDescription": "<p>Full advert. <b>£12.50 per hour</b>. Night shifts. 6 month contract.</p>",
            "jobUrl": "https://www.reed.co.uk/jobs/555", "externalUrl": "https://acme.example/apply",
            "salaryType": "per hour", "contractType": "Contract", "fullTime": True,
        }),
    })
    src = ReedSource(api_key="k", session=session, delay=0)
    listings = src.search("picker", "LS6 3AB", 10, 20)
    assert session.calls[0][1]["distanceFromLocation"] == 10
    assert session.calls[0][1]["locationName"] == "LS6 3AB"
    assert session.calls[0][2]["auth"] == ("k", "")
    full = src.fetch_details(listings[0])
    assert full.description.startswith("Full advert.")
    assert full.apply_url == "https://acme.example/apply"
    assert full.structured["salary_type"] == "per hour"


def test_adzuna_converts_miles_to_km():
    session = FakeSession({"https://api.adzuna.com": FakeResponse({"results": [{
        "id": "9", "title": "<strong>Cleaner</strong>", "description": "Cleaner needed…",
        "redirect_url": "https://adzuna.example/9", "company": {"display_name": "Shine"},
        "location": {"display_name": "Bristol"}, "latitude": 51.45, "longitude": -2.58,
        "salary_min": 23000, "salary_max": 23000, "contract_type": "permanent",
    }]})})
    src = AdzunaSource(app_id="a", app_key="b", session=session)
    listings = src.search("cleaner", "BS1 4DJ", 10, 5)
    assert session.calls[0][1]["distance"] == 16
    assert listings[0].title == "Cleaner"
    assert listings[0].latitude == 51.45


def test_sources_report_missing_keys():
    assert ReedSource(api_key="").available()[0] is False
    assert AdzunaSource(app_id="", app_key="").available()[0] is False


def test_demo_scan_respects_radius_and_keeps_toggles(profile):
    found = scan("", "M32 0AB", 5, [DemoSource()], profile=profile, log=lambda m: None)
    assert found and all(j["distance_miles"] <= 5 for j in found)
    assert all({"pay", "contract", "shifts", "summary"} <= set(j["analysis"]) for j in found)

    jobs = storage.load_jobs()
    jobs[0]["selected"] = True
    storage.save_jobs(jobs)
    scan("", "M32 0AB", 5, [DemoSource()], profile=profile, log=lambda m: None)
    again = {j["id"]: j for j in storage.load_jobs()}
    assert again[jobs[0]["id"]]["selected"] is True


def test_fit_warnings(profile):
    found = scan("nights", "M32 0AB", 15, [DemoSource()], profile=profile, log=lambda m: None)
    warehouse = next(j for j in found if "Warehouse" in j["title"])
    assert "night shifts" in warehouse["fit"]["warnings"]
    assert "RF scanner" in warehouse["fit"]["matched_skills"]


def test_merge_dedupes_same_advert_across_boards():
    a = {"id": "1", "title": "Picker", "company": "Acme", "source": "reed"}
    b = {"id": "2", "title": "picker ", "company": "ACME", "source": "adzuna"}
    assert [j["id"] for j in merge_jobs([], [a, b])] == ["1"]


def test_contract_families():
    from jobbot.matching import contract_matches

    assert contract_matches("Fixed-term", ["temporary", "seasonal"])
    assert contract_matches("Seasonal", ["temporary"])
    assert not contract_matches("Permanent", ["temporary", "seasonal"])
    assert contract_matches("Temp to perm", ["temporary"])


def test_bare_postcode_locations_get_town_and_distance(monkeypatch, profile):
    from jobbot.sources import JobSource, Listing

    class OneJob(JobSource):
        name = "fake"

        def search(self, *a):
            return [Listing(source="fake", source_id="1", title="Cleaner", location="CV32TX")]

    monkeypatch.setattr("jobbot.scanner.geocode", lambda pc: (52.4068, -1.5197))
    monkeypatch.setattr("jobbot.scanner.bulk_lookup",
                        lambda pcs: {"CV3 2TX": {"lat": 52.39, "lon": -1.48, "town": "Coventry"}} if pcs == ["CV3 2TX"] else {})
    job = scan("", "CV1 2LA", 10, [OneJob()], profile=profile, log=lambda m: None)[0]
    assert job["location"] == "Coventry (CV3 2TX)"
    assert 1 < job["distance_miles"] < 4
