"""The ``apply`` command: open each toggled job in a browser and fill the form."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.request
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


DEFAULT_DEBUG_PORT = 9222


def chrome_profile_dir() -> Path:
    path = storage.data_dir() / "chrome-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_chrome() -> str | None:
    """Locate an installed Google Chrome (or Chromium / Edge as a fallback)."""
    if os.environ.get("JOBBOT_CHROME"):
        return os.environ["JOBBOT_CHROME"]
    candidates = []
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    candidates += [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if base and (Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe").is_file():
            return str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    return None


def debug_port_open(port: int) -> bool:
    try:
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy
        with direct.open(f"http://127.0.0.1:{port}/json/version", timeout=2) as resp:
            return resp.status == 200
    except (OSError, ValueError):
        return False


def open_own_chrome(port: int = DEFAULT_DEBUG_PORT, url: str = "https://www.reed.co.uk/account/signin") -> None:
    """Start Chrome normally (not driven by the bot) with a separate JobBot profile.

    You sign in to job sites there yourself; ``jobbot apply --connect`` then
    opens new tabs in that window. Chrome only allows this on a profile other
    than your everyday one, so your normal Chrome is untouched.
    """
    if debug_port_open(port):
        click.secho("The JobBot Chrome window is already open. Sign in there, then run:  jobbot apply --connect",
                    fg="green")
        return
    exe = find_chrome()
    if not exe:
        raise click.ClickException("Couldn't find Google Chrome. Install it, or set JOBBOT_CHROME to chrome.exe's path.")
    args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={chrome_profile_dir()}",
            "--no-first-run", "--no-default-browser-check", url]
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)  # noqa: S603 - fixed arguments, no shell
    for _ in range(40):
        if debug_port_open(port):
            break
        time.sleep(0.25)
    click.secho("Opened a separate JobBot Chrome window.", fg="green", bold=True)
    click.echo("1. Sign in to Reed (and any other job sites) in that window, like normal browsing.")
    click.echo("2. Leave the window open and run:  jobbot apply --connect")
    click.echo("Your sign-ins are kept for next time. Close that window when you're finished applying.")


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
              screenshot_dir: Path | None = None, connect_port: int | None = None) -> list:
    from playwright.sync_api import sync_playwright

    cv_path = profile.get("cv_path")
    if cv_path and not Path(cv_path).is_file():
        click.secho(f"Warning: CV file {cv_path} is missing, so it can't be uploaded. Run `jobbot setup` again.",
                    fg="yellow")

    reports = []
    with sync_playwright() as p:
        if connect_port:
            # Use the Chrome window the person opened (and signed in to) themselves.
            if not debug_port_open(connect_port):
                raise click.ClickException("The JobBot Chrome window isn't open. Run `jobbot chrome`, sign in to "
                                           "the job sites there, then run `jobbot apply --connect` again.")
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{connect_port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            spare = None  # never take over tabs the person already has open
        else:
            browser = None
            context = _launch(p, headless, channel)
            # A persistent context opens with one blank tab; reuse it.
            spare = context.pages[0] if context.pages and context.pages[0].url in ("about:blank", "") else None
        try:
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

            if wait or connect_port:
                click.secho(
                    "\nNothing has been submitted. Review each tab, finish any orange fields, tick consent boxes "
                    "yourself and press the site's submit button when you're happy.",
                    fg="cyan", bold=True,
                )
            if wait and not connect_port:
                click.prompt("Press Enter here when you're finished to close the browser "
                             "(unsent forms in it will be lost)", default="", show_default=False)
            if connect_port:
                click.echo("The tabs stay open in your JobBot Chrome window. JobBot has disconnected from it.")
        finally:
            if connect_port:
                pass  # leave the person's own window and tabs open; the connection ends with this process
            else:
                context.close()
    return reports
