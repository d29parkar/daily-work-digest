"""Tiny .env loader so the CLI and scheduled tasks pick up secrets.

Values already present in the process environment always win; the .env file
only fills in blanks. This keeps user-level environment variables and Task
Scheduler environments authoritative.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(project_root: Path) -> list[str]:
    """Load KEY=VALUE pairs from <project_root>/.env into os.environ.

    Returns the list of keys that were loaded (for logging). Missing file is
    not an error. Lines starting with # and blank lines are skipped.
    """
    loaded: list[str] = []
    for candidate in (project_root / ".env", project_root / ".env.txt"):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key or key in os.environ or not value:
                continue
            os.environ[key] = value
            loaded.append(key)
    return loaded
