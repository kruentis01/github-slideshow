"""`apply --connect`: fill forms in a Chrome window the person opened themselves.

Kept in its own module: Playwright allows one sync session per process at a
time, and test_autofill.py holds one open for its module-scoped browser.
"""
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
DEMO_FORM = (Path(__file__).parents[1] / "jobbot" / "demo" / "apply_form.html").resolve().as_uri()
JOB = {"id": "x", "title": "Warehouse Operative", "company": "Northgate Logistics", "source": "reed",
       "fit": {"matched_skills": ["RF scanner", "picking"]}}


def test_apply_connects_to_own_chrome_and_leaves_it_open(profile, tmp_path):
    import os
    import socket
    import subprocess
    import time

    from jobbot.applier import debug_port_open, run_apply

    with sync_api.sync_playwright() as p:
        exe = p.chromium.executable_path
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    args = [exe, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={tmp_path / 'chrome'}",
            "--no-first-run", "about:blank"]
    if os.name != "nt" and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")
    chrome = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            if debug_port_open(port):
                break
            time.sleep(0.25)
        job = {**JOB, "id": "c1", "url": DEMO_FORM + "?job=c1"}
        reports = run_apply([job], profile, wait=False, connect_port=port)
        assert reports[0].filled
        assert chrome.poll() is None  # the person's Chrome is still running
        with sync_api.sync_playwright() as p:
            pages = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}").contexts[0].pages
            form = next(pg for pg in pages if "job=c1" in pg.url)
            assert form.input_value("#fname") == "Jane"
            assert form.evaluate("document.body.dataset.submitted") is None
            assert any(pg.url == "about:blank" for pg in pages)  # existing tab left alone
    finally:
        chrome.terminate()
        chrome.wait(timeout=10)
