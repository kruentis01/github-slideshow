"""The Alt+N keybind: press Apply / Continue / Next, never Submit."""
import pytest

from jobbot.autofill import fill_page, profile_values
from jobbot.hotkey import Hotkey, advance, find_next_button, parse_hotkey

sync_api = pytest.importorskip("playwright.sync_api")
JOB = {"id": "h", "title": "Sales Assistant", "company": "Jollyes", "source": "reed", "fit": {}}

FLOW = """<!doctype html><html><body>
<header><nav><a href="#">Next</a></nav><form role="search"><input name="q"><button>Search</button></form></header>
<main id="advert"><h1>Sales Assistant</h1><p>Lovely job.</p><button id="apply" type="button">Apply now</button>
  <button type="button" onclick="document.body.dataset.quick='yes'">Quick apply</button></main>
<div id="dlg" role="dialog" aria-modal="true" hidden>
  <section id="s1"><label for="emp">Do you currently work for us? *</label>
    <select id="emp"><option value=""></option><option>Yes</option><option>No</option></select>
    <button type="button" id="cont">Continue</button></section>
  <section id="s2" hidden><label for="cl">Cover letter</label><textarea id="cl"></textarea>
    <button type="button" onclick="document.body.dataset.submitted='yes'">Submit application</button></section>
</div>
<script>
  apply.onclick = () => { dlg.hidden = false; };
  cont.onclick = () => { s1.hidden = true; s2.hidden = false; };
</script></body></html>"""


@pytest.fixture
def context(tmp_path):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        yield ctx
        browser.close()


def press(page, hotkey):
    page.keyboard.press("Alt+N")
    page.wait_for_timeout(150)  # let the binding call arrive
    return hotkey.take()


def test_hotkey_walks_through_application_but_never_submits(context, tmp_path, profile):
    flow = tmp_path / "flow.html"
    flow.write_text(FLOW)
    hotkey = Hotkey("alt+n")
    hotkey.install(context)
    page = context.new_page()
    page.goto(flow.as_uri())
    values = profile_values(profile, JOB)

    pressed = press(page, hotkey)
    assert pressed is not None
    status, text, page = advance(pressed, context, settle_ms=100)
    assert (status, text) == ("clicked", "Apply now")  # not the nav "Next", not "Quick apply"
    fill_page(page, values)
    assert page.input_value("#emp") == "No"

    status, text, page = advance(press(page, hotkey), context, settle_ms=100)
    assert (status, text) == ("clicked", "Continue")
    fill_page(page, values)
    assert "Dear Hiring Manager" in page.input_value("#cl")

    status, text, page = advance(press(page, hotkey), context, settle_ms=100)
    assert status == "final" and text == "Submit application"
    assert page.evaluate("document.body.dataset.submitted") is None
    assert page.evaluate("document.body.dataset.quick") is None


def test_apply_button_inside_a_form_counts_as_final(context, tmp_path):
    form = tmp_path / "form.html"
    form.write_text("<form><label>Name<input></label><label>Email<input></label><label>Phone<input></label>"
                    "<button type='button'>Apply</button></form>")
    page = context.new_page()
    page.goto(form.as_uri())
    assert find_next_button(page)[0] == "final"


def test_apply_opening_new_tab_switches_page(context, tmp_path):
    target = tmp_path / "form.html"
    target.write_text("<label>Email<input id='e'></label>")
    advert = tmp_path / "advert.html"
    advert.write_text(f"<h1>Job</h1><a href='{target.as_uri()}' target='_blank'>Apply now</a>")
    page = context.new_page()
    page.goto(advert.as_uri())
    status, text, new_page = advance(page, context, settle_ms=200)
    assert status == "clicked" and new_page is not page and new_page.url.endswith("form.html")


def test_parse_hotkey():
    assert parse_hotkey("Alt+N") == {"alt": True, "ctrl": False, "shift": False, "key": "n"}
    assert parse_hotkey("ctrl+shift+f2")["key"] == "f2"
    with pytest.raises(ValueError):
        parse_hotkey("hyper+x")
