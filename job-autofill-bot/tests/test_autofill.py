from pathlib import Path

import pytest

from jobbot.autofill import cover_letter, fill_page, match_rule, plan_fills, profile_values

sync_api = pytest.importorskip("playwright.sync_api")
DEMO_FORM = (Path(__file__).parents[1] / "jobbot" / "demo" / "apply_form.html").resolve().as_uri()
JOB = {"id": "x", "title": "Warehouse Operative", "company": "Northgate Logistics", "source": "reed",
       "fit": {"matched_skills": ["RF scanner", "picking"]}}


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def field(**kw):
    base = {"id": "f", "tag": "input", "type": "text", "name": "", "el_id": "", "placeholder": "", "aria": "",
            "autocomplete": "", "labels": "", "group": "", "near": "", "value": "", "option_value": "",
            "required": False, "maxlength": None, "options": []}
    return {**base, **kw}


@pytest.mark.parametrize("kw,key", [
    ({"labels": "First name"}, "first_name"),
    ({"name": "lastName"}, "last_name"),
    ({"labels": "Email address"}, "email"),
    ({"labels": "Company name"}, None),
    ({"labels": "Address line 2"}, "skip"),
    ({"labels": "County"}, "skip"),
    ({"labels": "Supporting statement", "tag": "textarea", "type": "textarea"}, "cover_letter"),
    ({"placeholder": "Postcode"}, "postcode"),
    ({"labels": "National Insurance number"}, None),
    ({"labels": "Tell us about yourself", "tag": "textarea", "type": "textarea"}, "cover_letter"),
    ({"labels": "Attach resume", "type": "file"}, "cv"),
])
def test_rules(kw, key):
    rule = match_rule(field(**kw))
    assert (rule.key if rule else None) == key


def test_plan_never_ticks_consent_and_respects_existing(profile):
    values = profile_values(profile, JOB)
    fields = [
        field(id="a", type="checkbox", labels="I agree to the terms and privacy policy", required=True),
        field(id="b", labels="First name", value="Already typed"),
        field(id="c", type="checkbox", labels="I have the right to work in the UK"),
    ]
    plan = plan_fills(fields, values)
    assert [a.field_id for a in plan.actions] == ["c"]
    assert plan.unfilled_required[0][0] == "a"
    assert plan.skipped_existing == 1


def test_cover_letter_mentions_job_and_skills(profile):
    text = cover_letter(profile, JOB)
    assert "Warehouse Operative position at Northgate Logistics" in text
    assert "RF scanner" in text and "Jane Doe" in text
    assert "{" not in text


def test_fills_demo_form_without_submitting(browser, profile):
    page = browser.new_page()
    page.goto(DEMO_FORM)
    report = fill_page(page, profile_values(profile, JOB))
    assert page.input_value("#fname") == "Jane"
    assert page.input_value("#lname") == "Doe"
    assert page.input_value("#email") == "jane.doe@example.com"
    assert page.input_value("#pc") == "M32 0AB"
    assert page.input_value("#town") == "Manchester"
    assert page.input_value("#rtw") == "y"
    assert page.is_checked("#dl-no")  # profile says no licence
    assert page.input_value("#start") == "2026-12-01"
    assert page.input_value("#rate") == "13.50"
    assert "Northgate Logistics" in page.input_value("#cover")
    assert page.eval_on_selector("#cv", "el => el.files[0].name") == "sample_cv.txt"
    assert not page.is_checked("#terms")  # consent left to the person
    assert "I agree to the privacy policy *" in report.needs_you
    assert page.evaluate("document.body.dataset.submitted") is None
    assert page.inner_text("#result") == ""
    # The guard is removed afterwards, so the person can still submit themselves.
    page.check("#terms")
    invalid = page.evaluate("[...document.querySelectorAll('input,select,textarea')]"
                            ".filter(e => !e.checkValidity()).map(e => e.id)")
    assert invalid == []
    page.click("button[type=submit]")
    assert page.inner_text("#result") == "SUBMITTED"
    page.close()


def test_fills_labelless_and_iframe_forms(browser, profile):
    page = browser.new_page()
    inner = """
      <form><p>Full name</p><input name="applicant_full_name">
      <input type="email" placeholder="you@example.com" aria-label="Email">
      <select name="country"><option value="">Select</option><option value="FR">France</option>
        <option value="GB">United Kingdom</option></select>
      <textarea name="supporting_statement"></textarea></form>"""
    page.set_content(f"<h1>Outer</h1><iframe srcdoc='{inner}'></iframe>")
    page.wait_for_timeout(300)
    report = fill_page(page, profile_values(profile, JOB))
    frame = page.frames[1]
    assert frame.input_value("input[name=applicant_full_name]") == "Jane Doe"
    assert frame.input_value("input[type=email]") == "jane.doe@example.com"
    assert frame.input_value("select") == "GB"
    assert "Dear Hiring Manager" in frame.input_value("textarea")
    assert len(report.filled) == 4
    page.close()


def test_follows_apply_link(browser, tmp_path, profile):
    from jobbot.autofill import open_application

    advert = tmp_path / "advert.html"
    advert.write_text(f'<h1>Great job</h1><p>Lovely role.</p><a href="{DEMO_FORM}?job=z">Apply now</a>')
    page = browser.new_page()
    open_application(page, advert.as_uri())
    assert page.url.startswith(DEMO_FORM)
    page.close()


def test_past_start_date_becomes_today(profile):
    from datetime import date

    profile["availability"]["start_date"] = "2020-01-01"
    assert profile_values(profile, JOB)["start_date"] == date.today().isoformat()
    profile["availability"]["start_date"] = "2999-01-01"
    assert profile_values(profile, JOB)["start_date"] == "2999-01-01"
