from datetime import date

import pytest

from jobbot.cv_parser import parse_cv
from jobbot.geo import PostcodeError, find_postcode, haversine_miles, normalise_postcode
from jobbot.profile import parse_days, resolve_start_date


def test_parse_txt_cv(sample_cv):
    cv = parse_cv(sample_cv)
    assert cv["full_name"] == "Jane Doe"
    assert (cv["first_name"], cv["last_name"]) == ("Jane", "Doe")
    assert cv["email"] == "jane.doe@example.com"
    assert cv["phone"] == "07700 900123"
    assert cv["postcode"] == "M32 0AB"
    assert cv["address"] == "12 Chester Road"
    assert cv["town"] == "Manchester"
    assert "linkedin.com/in/jane-doe-uk" in cv["linkedin"]
    assert cv["current_title"] == "Warehouse Operative"
    assert cv["current_employer"] == "Northern Freight Ltd"
    assert "Customer service" in cv["skills"] and "RF scanner" in cv["skills"]
    assert cv["summary"].startswith("Reliable warehouse")


def test_parse_docx_cv(tmp_path, sample_cv):
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    for line in sample_cv.read_text().splitlines():
        doc.add_paragraph(line)
    path = tmp_path / "cv.docx"
    doc.save(path)
    cv = parse_cv(path)
    assert cv["email"] == "jane.doe@example.com" and cv["full_name"] == "Jane Doe"


def test_parse_pdf_cv(tmp_path, sample_cv):
    sync_api = pytest.importorskip("playwright.sync_api")
    html = "<pre style='font:14px sans-serif'>" + sample_cv.read_text() + "</pre>"
    path = tmp_path / "cv.pdf"
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        page.pdf(path=str(path))
        browser.close()
    cv = parse_cv(path)
    assert cv["email"] == "jane.doe@example.com"
    assert cv["postcode"] == "M32 0AB"


def test_unsupported_cv(tmp_path):
    bad = tmp_path / "cv.doc"
    bad.write_bytes(b"\xd0\xcf")
    with pytest.raises(ValueError, match="docx"):
        parse_cv(bad)


@pytest.mark.parametrize("raw,expected", [
    ("sw1a1aa", "SW1A 1AA"), ("M32 0AB", "M32 0AB"), (" ec1a 1bb ", "EC1A 1BB"), ("LS6", "LS6"),
])
def test_postcodes(raw, expected):
    assert normalise_postcode(raw) == expected


def test_bad_postcode():
    with pytest.raises(PostcodeError):
        normalise_postcode("12345")


def test_find_postcode_and_distance():
    assert find_postcode("Flat 2, Leeds LS6 3AB, UK") == "LS6 3AB"
    london, manchester = (51.5074, -0.1278), (53.4808, -2.2426)
    assert 160 < haversine_miles(london, manchester) < 170


def test_start_date_and_days():
    today = date(2026, 11, 7)
    assert resolve_start_date("immediately", today) == "2026-11-07"
    assert resolve_start_date("2 weeks", today) == "2026-11-21"
    assert resolve_start_date("01/12/2026", today) == "2026-12-01"
    assert parse_days("Mon-Fri") == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert parse_days("mon, wed, sat") == ["Mon", "Wed", "Sat"]
    assert parse_days("Fri-Mon") == ["Mon", "Fri", "Sat", "Sun"]


TWO_COLUMN_CV = """
<style>
  body { font: 11px sans-serif; width: 560px; margin: 30px; }
  .cols { display: flex; gap: 40px; } .left { width: 330px; } .right { width: 190px; }
  .head { display: flex; justify-content: space-between; }
  h2 { font-size: 12px; margin: 10px 0 4px; } p, li { margin: 2px 0; } ul { padding-left: 12px; }
</style>
<h1>SAM PATEL</h1>
<div class="head"><div>sam.patel@leeds.ac.<br>+44 7700 900456<br>uk</div><div>14 Kirkstall Road<br>LS3 1HS</div>
<div>Available now</div></div>
<div class="cols">
 <div class="left">
  <h2>PROFILE</h2>
  <p>Hard-working student with retail and warehouse experience, looking for flexible weekend work in Leeds city centre.</p>
  <h2>EXPERIENCE</h2>
  <p>6 Month FTC Picker Packer - Amazon</p><p>Mar 2025 - Sep 2025</p>
  <p>• Picked and packed customer orders to tight deadlines on a busy rotating night shift pattern every week.</p>
  <p>• Worked safely around forklifts and kept the area tidy and organised throughout every shift worked.</p>
  <p>Barista - Costa Coffee</p><p>Oct 2025 - Present</p>
  <p>• Served customers quickly during the morning rush and handled cash and card payments accurately.</p>
  <p>• Kept the store clean and stocked and trained two new starters on the coffee machines each month.</p>
 </div>
 <div class="right">
  <h2>EDUCATION</h2><p>A levels @ Leeds College</p><p>• Maths (A)</p><p>• English Language</p>
  <h2>KEY SKILLS</h2>
  <p>• Customer service, cash and card handling</p><p>• Teamwork &amp; reliable work ethic</p>
  <p>• Stock rotation, replenishment &amp; inventory checks</p><p>• Food hygiene level 2</p>
 </div>
</div>
"""


def test_two_column_pdf_cv(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    path = tmp_path / "two_col.pdf"
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(TWO_COLUMN_CV)
        page.pdf(path=str(path))
        browser.close()
    cv = parse_cv(path)
    assert cv["full_name"] == "Sam Patel"
    assert cv["email"] == "sam.patel@leeds.ac.uk"  # wrapped over two lines in the PDF
    assert cv["address"] == "14 Kirkstall Road"
    assert cv["postcode"] == "LS3 1HS"
    # The job marked "Present", not a stray line from the other column.
    assert (cv["current_title"], cv["current_employer"]) == ("Barista", "Costa Coffee")
    assert "Customer service, cash and card handling" in cv["skills"]
    assert "Stock rotation, replenishment & inventory checks" in cv["skills"]
    assert not any("Picked and packed" in s for s in cv["skills"])
    assert cv["summary"].startswith("Hard-working student")


def test_role_prefixes_are_stripped():
    from jobbot.cv_parser import _guess_current_role

    lines = ["4 Month FTC Packing Operator - Reckitt", "May 2026 - Sept 2026", "• Packed things"]
    assert _guess_current_role(lines) == ("Packing Operator", "Reckitt")


def test_env_txt_from_notepad_is_read(jobbot_home, monkeypatch):
    import os

    from jobbot import storage

    monkeypatch.delenv("REED_API_KEY", raising=False)
    jobbot_home.mkdir(parents=True, exist_ok=True)
    (jobbot_home / ".env.txt").write_bytes("﻿REED_API_KEY=abc-123\r\n".encode("utf-8"))
    storage.load_env()
    assert os.environ["REED_API_KEY"] == "abc-123"
