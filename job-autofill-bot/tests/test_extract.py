import pytest

from jobbot.extract import NOT_STATED, analyse, parse_contract, parse_pay, parse_shifts, summarise


@pytest.mark.parametrize("text,lo,hi,estimated", [
    ("Pay: £12.50 per hour", 12.5, None, False),
    ("£11.44 - £13.00 p/h depending on experience", 11.44, 13.0, False),
    ("Earn £14.20ph on nights", 14.2, None, False),
    ("£15/hr plus holiday", 15.0, None, False),
    ("Salary £24,500 per annum", 24500 / (37.5 * 52), None, True),
    ("£25k - £28k a year", 25000 / (37.5 * 52), 28000 / (37.5 * 52), True),
    ("Day rate £120 per day", 16.0, None, True),
])
def test_pay(text, lo, hi, estimated):
    pay = parse_pay(text)
    assert pay["hourly_min"] == pytest.approx(lo, abs=0.01)
    assert (pay["hourly_max"] or None) == (pytest.approx(hi, abs=0.01) if hi else None)
    assert pay["estimated"] is estimated


def test_pay_prefers_hourly_over_annual():
    pay = parse_pay("£24,000 per annum (£12.31 per hour)")
    assert pay["hourly_min"] == 12.31 and not pay["estimated"]


def test_pay_ignores_bonuses_and_falls_back_to_api_fields():
    assert parse_pay("£500 welcome bonus")["display"] == NOT_STATED
    pay = parse_pay("No pay mentioned", {"salary_min": 12.6, "salary_type": "per hour"})
    assert pay["display"] == "£12.60/hr"


@pytest.mark.parametrize("text,ctype,length", [
    ("This is a 6 month fixed term contract", "Fixed-term", "6 months"),
    ("Temporary role for an initial 12 week contract", "Temporary", "12 weeks"),
    ("Temp to perm after 12 weeks for the right candidate", "Temp to perm", "12 weeks"),
    ("Permanent, full-time position", "Permanent", "permanent"),
    ("Maternity cover until March 2027", "Fixed-term (maternity cover)", "until March 2027"),
    ("Zero hours contract", "Zero hours", None),
])
def test_contract(text, ctype, length):
    c = parse_contract(text)
    assert c["type"] == ctype
    assert c["length"] == length


def test_contract_ignores_years_of_experience():
    c = parse_contract("Permanent role. You will need 2 years experience in a warehouse.")
    assert c["length"] == "permanent"


def test_shifts():
    s = parse_shifts("Night shifts, 4 on 4 off, 12-hour shifts 7pm - 7am. 42 hours per week.")
    assert {"Nights", "4 on / 4 off", "12-hour shifts", "7pm–7am", "42 hrs/wk"} <= set(s["items"])
    assert s["hours_per_week"] == 42


def test_shifts_negated_weekends():
    assert "No weekends" in parse_shifts("Monday to Friday, no weekends")["items"]
    assert parse_shifts("Monday to Friday, no weekends")["items"][0] == "Mon–Fri"


def test_summary_skips_boilerplate():
    text = ("Ref: 12345\nWe are an equal opportunities employer.\n"
            "We are looking for a Kitchen Porter to join our busy team. You will wash up and prep food.\n"
            "Click apply now!")
    out = summarise(text, "Kitchen Porter")
    assert out.startswith("We are looking for a Kitchen Porter")
    assert "equal opportunities" not in out and "apply now" not in out


def test_every_demo_listing_has_pay_contract_and_shifts():
    from conftest import demo_listings

    for job in demo_listings():
        a = analyse(job["description"], job["title"])
        assert a["pay"]["display"] != NOT_STATED, job["title"]
        assert a["shifts"]["display"] != NOT_STATED, job["title"]
        assert a["summary"], job["title"]


def test_part_time_salary_is_treated_as_full_time_equivalent():
    # "£30,000" for a 30-hour job is normally the FTE, so ~£15.38/hr, not £19.23.
    a = analyse("Part time administrator, 30 hours per week. Salary £30,000 per annum.", "Part Time Administrator")
    assert a["pay"]["hourly_min"] == pytest.approx(30000 / (37.5 * 52), abs=0.01)


def test_small_part_time_salary_is_treated_as_actual_pay():
    # £10,000 as FTE would be below minimum wage, so it is the real pay for 16 hours.
    a = analyse("Weekend team member, 16 hours per week, £10,000 per annum.", "Team Member")
    assert a["pay"]["hourly_min"] == pytest.approx(10000 / (16 * 52), abs=0.01)


def test_weekly_pay_uses_advert_hours():
    assert analyse("£400 per week for 32 hours per week")["pay"]["hourly_min"] == 12.5


def test_title_decides_part_time():
    c = parse_contract("Temporary role.", {"full_time": True}, title="Part Time Cleaner")
    assert c["hours"] == "Part-time"
