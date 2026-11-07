"""JobBot command line.

    jobbot setup --cv my_cv.pdf    read your CV, then ask about preferences and availability
    jobbot scan                    find jobs near your postcode and read each description
    jobbot table                   table of jobs with a toggle button per job
    jobbot apply                   fill in applications for toggled jobs (never submits)
"""

from __future__ import annotations

from pathlib import Path

import click

from . import storage
from .geo import PostcodeError, normalise_postcode
from .sources import SOURCE_NAMES


def _need_profile(ctx_cv: str | None = None) -> dict:
    profile = storage.load_profile()
    if profile:
        return profile
    click.secho("No profile yet, so let's set one up first.", fg="yellow")
    cv = ctx_cv or click.prompt("Path to your CV (PDF, DOCX or TXT)", type=click.Path(exists=True, dir_okay=False))
    from .profile import run_setup

    return run_setup(cv)


@click.group(help=__doc__)
@click.version_option(package_name="jobbot", message="%(version)s")
def main():
    storage.load_env()


@main.command()
@click.option("--cv", "cv_path", type=click.Path(exists=True, dir_okay=False), help="Your CV: PDF, DOCX or TXT.")
def setup(cv_path):
    """Scan your CV, then answer questions about preferences and availability."""
    from .profile import run_setup

    existing = storage.load_profile()
    if not cv_path:
        default = existing.get("cv_path") if existing else None
        cv_path = click.prompt("Path to your CV (PDF, DOCX or TXT)", default=default,
                               type=click.Path(exists=True, dir_okay=False))
    run_setup(cv_path, existing)
    click.echo("\nNext: jobbot scan")


@main.command()
@click.option("--postcode", "-p", help="UK postcode to search around (default: from your profile).")
@click.option("--radius", "-r", type=click.FloatRange(1, 100), help="Search radius in miles.")
@click.option("--keywords", "-k", help="Job titles or keywords (default: from your profile).")
@click.option("--source", "-s", "sources", multiple=True, type=click.Choice([*SOURCE_NAMES, "all"]),
              help="Job board(s) to search. Repeat for several. Default: every board with an API key, "
                   "or the offline demo if none are set up.")
@click.option("--limit", "-n", type=click.IntRange(1, 500), default=50, show_default=True,
              help="Maximum listings per job board.")
@click.option("--fetch-pages", is_flag=True,
              help="Adzuna only: download each advert page to read the full description.")
@click.option("--fresh", is_flag=True, help="Forget earlier scan results instead of adding to them.")
def scan(postcode, radius, keywords, sources, limit, fetch_pages, fresh):
    """Search for jobs within a radius of a UK postcode and read each description."""
    from .scanner import build_sources, scan as do_scan
    from .sources import AdzunaSource, ReedSource

    profile = _need_profile()
    prefs = profile.get("preferences", {})
    try:
        postcode = normalise_postcode(postcode or prefs.get("postcode") or click.prompt("Postcode"))
    except PostcodeError as exc:
        raise click.BadParameter(str(exc), param_hint="--postcode") from exc
    radius = radius or prefs.get("radius_miles") or 10
    keywords = keywords if keywords is not None else prefs.get("keywords", "")

    names = list(sources)
    if not names:
        names = [n for n, src in (("reed", ReedSource()), ("adzuna", AdzunaSource())) if src.available()[0]]
        if not names:
            click.secho("No job board API keys found (REED_API_KEY, ADZUNA_APP_ID/ADZUNA_APP_KEY), "
                        "so using the offline demo listings. See README to add keys.", fg="yellow")
            names = ["demo"]
    elif "all" in names:
        names = ["reed", "adzuna"]

    click.secho(f"Scanning for '{keywords or 'any job'}' within {radius:g} miles of {postcode} "
                f"on {', '.join(names)}…", bold=True)
    found = do_scan(keywords, postcode, radius, build_sources(names, None, fetch_pages),
                    limit=limit, profile=profile, log=click.echo, replace=fresh)
    total = len(storage.load_jobs())
    click.secho(f"\nScanned {len(found)} job description(s). {total} job(s) saved.", fg="green")
    click.echo("Next: jobbot table")


@main.command()
@click.option("--terminal", "-t", is_flag=True, help="Show the table and toggles in the terminal instead of a browser.")
@click.option("--port", type=int, default=8765, show_default=True, help="Port for the local table page.")
@click.option("--no-open", is_flag=True, help="Don't open a browser tab automatically.")
@click.option("--export", "export_path", type=click.Path(dir_okay=False),
              help="Write a read-only HTML copy of the table to this file and exit.")
def table(terminal, port, no_open, export_path):
    """Table of contract length, shifts, hourly rate and summary, with a toggle per job."""
    from . import table as tbl

    jobs = storage.load_jobs()
    if not jobs:
        raise click.ClickException("No jobs saved yet. Run `jobbot scan` first.")
    if export_path:
        path = tbl.export_html(Path(export_path))
        click.echo(f"Wrote {path}")
        return
    if terminal:
        tbl.terminal_loop()
        selected = sum(1 for j in storage.load_jobs() if j.get("selected"))
        click.echo(f"{selected} job(s) toggled on. Next: jobbot apply")
        return
    tbl.serve(port=port, open_browser=not no_open)


@main.command()
@click.option("--job", "job_ids", multiple=True, help="Only these job ids (must also be toggled on).")
@click.option("--overwrite", is_flag=True, help="Replace values already in the form.")
@click.option("--no-follow", is_flag=True, help="Don't follow 'Apply' links from the advert to the form.")
@click.option("--chrome", is_flag=True, help="Use your installed Google Chrome instead of Playwright's Chromium.")
@click.option("--include-done", is_flag=True, help="Also redo jobs that were already filled.")
@click.option("--headless", is_flag=True, hidden=True)
@click.option("--screenshots", type=click.Path(file_okay=False), hidden=True)
@click.option("--no-wait", is_flag=True, hidden=True)
def apply(job_ids, overwrite, no_follow, chrome, include_done, headless, screenshots, no_wait):
    """Fill in applications for the toggled jobs. Never submits them."""
    from .applier import run_apply

    profile = _need_profile()
    jobs = [j for j in storage.load_jobs() if j.get("selected")]
    if job_ids:
        jobs = [j for j in jobs if j["id"] in job_ids]
    if not include_done:
        skipped = [j for j in jobs if (j.get("application") or {}).get("status", "").startswith("filled")]
        if skipped:
            click.echo(f"Skipping {len(skipped)} job(s) already filled (use --include-done to redo them).")
        jobs = [j for j in jobs if j not in skipped]
    if not jobs:
        raise click.ClickException("No toggled jobs to fill. Run `jobbot table` and toggle some on.")

    click.secho(f"Filling {len(jobs)} application(s). JobBot will NOT submit anything.", bold=True)
    run_apply(jobs, profile, headless=headless, wait=not (no_wait or headless), overwrite=overwrite,
              follow_apply_links=not no_follow, channel="chrome" if chrome else None,
              screenshot_dir=Path(screenshots) if screenshots else None)


@main.command(name="profile")
def show_profile():
    """Show the saved profile and where it's stored."""
    profile = storage.load_profile()
    if not profile:
        raise click.ClickException("No profile yet. Run `jobbot setup --cv <file>`.")
    cv, prefs, av = profile["cv"], profile["preferences"], profile["availability"]
    click.echo(f"Stored at: {storage.profile_path()}\nCV file:   {profile['cv_path']}\n")
    for key in ("full_name", "email", "phone", "address", "town", "postcode", "linkedin",
                "current_title", "current_employer"):
        click.echo(f"{key.replace('_', ' ').capitalize():<18} {cv.get(key) or '-'}")
    click.echo(f"{'Skills':<18} {', '.join(cv.get('skills', [])) or '-'}\n")
    for key, value in prefs.items():
        click.echo(f"{key.replace('_', ' ').capitalize():<22} {value}")
    click.echo()
    for key, value in av.items():
        click.echo(f"{key.replace('_', ' ').capitalize():<22} {value}")


if __name__ == "__main__":
    main()
