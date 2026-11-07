"""The ``table`` command: a table of scanned jobs, each row with a toggle button.

By default it serves an HTML page on localhost; clicking a row's toggle saves
the choice straight to ``jobs.json``. ``--terminal`` gives the same table and
toggles in the terminal instead.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import textwrap
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import click

from . import storage

ASSET = Path(__file__).resolve().parent / "table_page.html"


def row_view(job: dict) -> dict:
    a = job.get("analysis", {})
    return {
        "id": job["id"],
        "selected": bool(job.get("selected")),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "distance": job.get("distance_miles"),
        "url": job.get("url", ""),
        "source": job.get("source", ""),
        "contract": a.get("contract", {}).get("display", "Not stated"),
        "shifts": a.get("shifts", {}).get("display", "Not stated"),
        "rate": a.get("pay", {}).get("display", "Not stated"),
        "rate_value": a.get("pay", {}).get("hourly_min"),
        "rate_estimated": a.get("pay", {}).get("estimated", False),
        "summary": a.get("summary", ""),
        "description": job.get("description", ""),
        "warnings": job.get("fit", {}).get("warnings", []),
        "matched_skills": job.get("fit", {}).get("matched_skills", []),
        "application": (job.get("application") or {}).get("status"),
    }


def set_selected(ids: dict[str, bool]) -> list[dict]:
    jobs = storage.load_jobs()
    for job in jobs:
        if job["id"] in ids:
            job["selected"] = bool(ids[job["id"]])
    storage.save_jobs(jobs)
    return jobs


# ---------------------------------------------------------------- HTML + server

def render_html(jobs: list[dict], token: str = "", live: bool = True) -> str:
    rows = [row_view(j) for j in jobs]
    payload = json.dumps({"rows": rows, "token": token, "live": live}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # keep it inside the <script> tag
    return ASSET.read_text(encoding="utf-8").replace("/*__JOBBOT_DATA__*/null", payload)


class _Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            html = render_html(storage.load_jobs(), self.token)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/jobs":
            data = json.dumps([row_view(j) for j in storage.load_jobs()]).encode()
            self._send(200, data, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        # The token is only in the served page, so other websites can't flip toggles.
        if self.headers.get("X-JobBot-Token") != self.token:
            self._send(403, b"bad token", "text/plain")
            return
        if self.path != "/api/select":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(min(length, 1_000_000)) or b"{}")
            ids = {str(k): bool(v) for k, v in body.get("selected", {}).items()}
        except (ValueError, AttributeError):
            self._send(400, b"bad request", "text/plain")
            return
        jobs = set_selected(ids)
        count = sum(1 for j in jobs if j.get("selected"))
        self._send(200, json.dumps({"ok": True, "selected": count}).encode(), "application/json")


def make_server(port: int = 8765) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(16)
    handler = type("Handler", (_Handler,), {"token": token})
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)  # port busy: take any free one
    return server, token


def serve(port: int = 8765, open_browser: bool = True) -> None:
    server, _ = make_server(port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    click.secho(f"Job table running at {url}", fg="green", bold=True)
    click.echo("Toggle the jobs you want, then press Ctrl+C here and run:  jobbot apply")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    selected = sum(1 for j in storage.load_jobs() if j.get("selected"))
    click.echo(f"\nStopped. {selected} job(s) toggled on for `jobbot apply`.")


def export_html(path: Path) -> Path:
    path.write_text(render_html(storage.load_jobs(), live=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------- terminal

def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text or "-", width=max(width, 6)) or ["-"]


def render_terminal(jobs: list[dict], width: int | None = None) -> str:
    width = width or shutil.get_terminal_size((140, 40)).columns
    fixed = 4 + 5 + 3 * 6  # number, toggle, separators
    usable = max(width - fixed, 80)
    cols = {"job": 0.22, "contract": 0.16, "shifts": 0.17, "rate": 0.13, "summary": 0.32}
    w = {k: max(8, int(usable * v)) for k, v in cols.items()}
    head = ["#", "On", "Job", "Contract length", "Advertised shifts", "Hourly rate", "Summary"]
    widths = [3, 3, w["job"], w["contract"], w["shifts"], w["rate"], w["summary"]]

    def line(cells):
        return " │ ".join(c.ljust(n) for c, n in zip(cells, widths))

    out = [line(head), "─┼─".join("─" * n for n in widths)]
    for i, job in enumerate(jobs, 1):
        r = row_view(job)
        dist = f" · {r['distance']:.1f} mi" if r["distance"] is not None else ""
        job_text = f"{r['title']} — {r['company']}{dist}"
        if r["application"]:
            job_text += f" [{r['application']}]"
        summary = r["summary"]
        if r["warnings"]:
            summary += "  ⚠ " + "; ".join(r["warnings"])
        cols_text = [
            _wrap(str(i), 3), _wrap("[x]" if r["selected"] else "[ ]", 3),
            _wrap(job_text, w["job"]), _wrap(r["contract"], w["contract"]),
            _wrap(r["shifts"], w["shifts"]), _wrap(r["rate"], w["rate"]),
            _wrap(summary, w["summary"]),
        ]
        for k in range(max(len(c) for c in cols_text)):
            out.append(line([c[k] if k < len(c) else "" for c in cols_text]))
        out.append("─┼─".join("─" * n for n in widths))
    return "\n".join(out)


def parse_selection(answer: str, count: int) -> set[int] | None:
    """Parse "1 3 5-7" into zero-based indexes. Returns None if invalid."""
    picked: set[int] = set()
    for part in re.split(r"[,\s]+", answer.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            return None
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if not (1 <= a <= count and 1 <= b <= count):
            return None
        picked.update(range(min(a, b) - 1, max(a, b)))
    return picked


def terminal_loop() -> None:
    while True:
        jobs = storage.load_jobs()
        click.echo(render_terminal(jobs))
        selected = sum(1 for j in jobs if j.get("selected"))
        click.echo(f"{selected} of {len(jobs)} toggled on.")
        answer = click.prompt(
            "Toggle rows (e.g. 1 3 5-7), 'a' = all on, 'n' = all off, Enter = done",
            default="", show_default=False,
        ).strip().lower()
        if not answer:
            return
        if answer in {"a", "all"}:
            set_selected({j["id"]: True for j in jobs})
            continue
        if answer in {"n", "none"}:
            set_selected({j["id"]: False for j in jobs})
            continue
        idx = parse_selection(answer, len(jobs))
        if idx is None:
            click.secho(f"Didn't understand '{answer}'. Use row numbers between 1 and {len(jobs)}.", fg="red")
            continue
        set_selected({jobs[i]["id"]: not jobs[i].get("selected") for i in idx})
