import json
import threading
import urllib.error
import urllib.request

import pytest

from jobbot import storage, table
from jobbot.scanner import scan
from jobbot.sources import DemoSource


@pytest.fixture
def scanned(profile):
    scan("", "M32 0AB", 15, [DemoSource()], profile=profile, log=lambda m: None)
    return storage.load_jobs()


def test_terminal_table_has_required_columns(scanned):
    out = table.render_terminal(scanned, width=160)
    for heading in ("Contract length", "Advertised shifts", "Hourly rate", "Summary"):
        assert heading in out
    assert "[ ]" in out and "£13.50/hr" in out


def test_parse_selection():
    assert table.parse_selection("1 3-4", 5) == {0, 2, 3}
    assert table.parse_selection("9", 5) is None
    assert table.parse_selection("x", 5) is None


def test_html_escapes_script_tags(scanned):
    scanned[0]["title"] = "</script><script>alert(1)</script>"
    html = table.render_html(scanned, token="t")
    assert "</script><script>alert(1)" not in html


def test_server_toggles_require_token(scanned):
    server, token = table.make_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    job_id = scanned[1]["id"]
    body = json.dumps({"selected": {job_id: True}}).encode()
    try:
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(urllib.request.Request(f"{base}/api/select", data=body, method="POST"))
        assert err.value.code == 403
        req = urllib.request.Request(f"{base}/api/select", data=body, method="POST",
                                     headers={"X-JobBot-Token": token, "Content-Type": "application/json"})
        assert json.load(urllib.request.urlopen(req))["selected"] == 1
        assert {j["id"]: j["selected"] for j in storage.load_jobs()}[job_id] is True
    finally:
        server.shutdown()
        server.server_close()


def test_browser_toggle_button_saves(scanned):
    sync_api = pytest.importorskip("playwright.sync_api")
    server, _ = table.make_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            toggles = page.locator("button.toggle")
            assert toggles.count() == len(scanned)
            toggles.nth(2).click()
            page.wait_for_function("document.querySelector('#count').textContent.startsWith('1 of')")
            assert toggles.nth(2).get_attribute("aria-checked") == "true"
            browser.close()
        assert sum(1 for j in storage.load_jobs() if j["selected"]) == 1
    finally:
        server.shutdown()
        server.server_close()
