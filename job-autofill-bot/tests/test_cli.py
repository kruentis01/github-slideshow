import json

import pytest
from click.testing import CliRunner

from jobbot import storage
from jobbot.cli import main

SETUP_ANSWERS = "\n".join([
    "", "", "", "", "", "", "", "", "", "",  # accept everything read from the CV
    "warehouse", "", "10", "12", "13.5", "any",   # preferences
    "2 weeks", "2 weeks", "Mon-Fri", "37", "y", "n", "n", "n", "y", "n", "y",  # availability
]) + "\n"


def test_full_flow(sample_cv):
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "--cv", str(sample_cv)], input=SETUP_ANSWERS)
    assert result.exit_code == 0, result.output
    profile = storage.load_profile()
    assert profile["cv"]["email"] == "jane.doe@example.com"
    assert profile["preferences"]["radius_miles"] == 10
    assert profile["availability"]["days"] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert profile["availability"]["driving_licence"] is False

    result = runner.invoke(main, ["scan", "--keywords", ""])
    assert result.exit_code == 0, result.output
    assert "offline demo" in result.output
    jobs = storage.load_jobs()
    assert jobs and all(j["distance_miles"] <= 10 for j in jobs)

    result = runner.invoke(main, ["apply"])
    assert result.exit_code != 0 and "No toggled jobs" in result.output

    result = runner.invoke(main, ["table", "--terminal"], input="1\n\n")
    assert result.exit_code == 0, result.output
    assert "1 job(s) toggled on" in result.output

    pytest.importorskip("playwright.sync_api")
    result = runner.invoke(main, ["apply", "--headless"])
    assert result.exit_code == 0, result.output
    assert "First name *: Jane" in result.output
    status = next(j for j in storage.load_jobs() if j["selected"])["application"]["status"]
    assert status == "filled — not submitted"

    result = runner.invoke(main, ["apply", "--headless"])
    assert "No toggled jobs" in result.output  # already filled, skipped


def test_export(tmp_path, sample_cv):
    runner = CliRunner()
    runner.invoke(main, ["setup", "--cv", str(sample_cv)], input=SETUP_ANSWERS)
    runner.invoke(main, ["scan", "-k", ""])
    out = tmp_path / "t.html"
    result = runner.invoke(main, ["table", "--export", str(out)])
    assert result.exit_code == 0
    assert '"live": false' in out.read_text()
    data = json.loads(out.read_text().split('type="application/json">')[1].split("</script>")[0])
    assert len(data["rows"]) == len(storage.load_jobs())
