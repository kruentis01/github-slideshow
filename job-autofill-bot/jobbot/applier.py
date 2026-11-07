"""The ``apply`` command: open each toggled job in a browser and fill the form."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import click

from . import storage
from .autofill import fill_page, open_application, profile_values


def _launch(playwright, headless: bool, channel: str | None):
    """Open Chromium with JobBot's own profile, so sign-ins to job sites persist."""
    kwargs = {"headless": headless, "viewport": None if not headless else {"width": 1280, "height": 900}}
    if channel:
        kwargs["channel"] = channel
    exe = os.environ.get("JOBBOT_CHROMIUM")
    if exe:
        kwargs["executable_path"] = exe
    if not headless:
        kwargs["args"] = ["--start-maximized"]
    try:
        return playwright.chromium.launch_persistent_context(str(storage.browser_profile_dir()), **kwargs)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "Executable doesn't exist" in msg or "browserType.launch" in msg:
            raise click.ClickException(
                "Playwright's Chromium isn't installed. Run:  python -m playwright install chromium\n"
                "(or use your installed Chrome with:  jobbot apply --chrome)"
            ) from exc
        raise


def _record(job_id: str, status: str, report=None) -> None:
    jobs = storage.load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            job["application"] = {
                "status": status,
                "at": datetime.now().isoformat(timespec="seconds"),
                "url": report.url if report else None,
                "filled": len(report.filled) if report else 0,
                "needs_you": report.needs_you if report else [],
            }
    storage.save_jobs(jobs)


def _print_report(report) -> None:
    click.echo(f"    Page: {report.url}")
    if not report.fields_seen:
        click.secho("    No form fields found. The site may need you to sign in or click through first.", fg="yellow")
    for line in report.filled:
        click.secho(f"    ✓ {line}", fg="green")
    for label in report.needs_you:
        click.secho(f"    • needs you: {label[:70]}", fg="yellow")
    for err in report.errors:
        click.secho(f"    ! {err}", fg="red")


def run_apply(jobs: list[dict], profile: dict, *, headless: bool = False, wait: bool = True,
              overwrite: bool = False, follow_apply_links: bool = True, channel: str | None = None,
              screenshot_dir: Path | None = None) -> list:
    from playwright.sync_api import sync_playwright

    cv_path = profile.get("cv_path")
    if cv_path and not Path(cv_path).is_file():
        click.secho(f"Warning: CV file {cv_path} is missing, so it can't be uploaded. Run `jobbot setup` again.",
                    fg="yellow")

    reports = []
    with sync_playwright() as p:
        context = _launch(p, headless, channel)
        try:
            # A persistent context opens with one blank tab; reuse it.
            spare = context.pages[0] if context.pages and context.pages[0].url in ("about:blank", "") else None
            for n, job in enumerate(jobs, 1):
                url = job.get("apply_url") or job.get("url")
                click.secho(f"\n[{n}/{len(jobs)}] {job['title']} — {job.get('company', '')}", bold=True)
                if not url:
                    click.secho("    No link for this job; skipped.", fg="yellow")
                    continue
                page, spare = (spare, None) if spare else (context.new_page(), None)
                try:
                    open_application(page, url, follow_apply_links=follow_apply_links)
                except Exception as exc:  # noqa: BLE001
                    click.secho(f"    Couldn't open {url}: {str(exc).splitlines()[0]}", fg="red")
                    _record(job["id"], "error")
                    continue

                values = profile_values(profile, job)
                report = fill_page(page, values, overwrite=overwrite)
                reports.append(report)
                _print_report(report)
                _record(job["id"], "filled — not submitted" if report.filled else "opened — nothing filled", report)
                if screenshot_dir:
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(screenshot_dir / f"{job['id']}.png"), full_page=True)

                # Multi-page forms: let the person move to the next step, then fill again.
                while wait:
                    choice = click.prompt(
                        "    [Enter] next job · [r] fill this tab again (after you click Next on the site) · [q] stop",
                        default="", show_default=False,
                    ).strip().lower()
                    if choice == "r":
                        report = fill_page(page, values, overwrite=overwrite)
                        reports.append(report)
                        _print_report(report)
                        _record(job["id"], "filled — not submitted", report)
                        continue
                    if choice == "q":
                        jobs = jobs[:n]
                    break
                if n >= len(jobs):
                    break

            if wait:
                click.secho(
                    "\nNothing has been submitted. Review each tab, finish any orange fields, tick consent boxes "
                    "yourself and press the site's submit button when you're happy.",
                    fg="cyan", bold=True,
                )
                click.prompt("Press Enter here when you're finished to close the browser "
                             "(unsent forms in it will be lost)", default="", show_default=False)
        finally:
            context.close()
    return reports
