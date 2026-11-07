"""Where JobBot keeps its data: profile, scanned jobs, browser profile and API keys.

Everything lives in one folder, ``~/.jobbot`` by default, or wherever the
``JOBBOT_HOME`` environment variable points.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    path = Path(os.environ.get("JOBBOT_HOME", Path.home() / ".jobbot")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_path() -> Path:
    return data_dir() / "profile.json"


def jobs_path() -> Path:
    return data_dir() / "jobs.json"


def browser_profile_dir() -> Path:
    path = data_dir() / "browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    """Write atomically so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_profile() -> dict | None:
    return read_json(profile_path(), None)


def save_profile(profile: dict) -> None:
    write_json(profile_path(), profile)


def load_jobs() -> list[dict]:
    return read_json(jobs_path(), [])


def save_jobs(jobs: list[dict]) -> None:
    write_json(jobs_path(), jobs)


def load_env() -> None:
    """Load KEY=VALUE lines from ``<data dir>/.env`` and ``./.env`` into os.environ.

    Values already set in the environment win.
    """
    # ".env.txt" too: Windows Notepad adds .txt and Explorer hides it.
    for candidate in (data_dir() / ".env", data_dir() / ".env.txt", Path.cwd() / ".env", Path.cwd() / ".env.txt"):
        if not candidate.is_file():
            continue
        # utf-8-sig drops the invisible byte-order mark some editors put at the start.
        for line in candidate.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
