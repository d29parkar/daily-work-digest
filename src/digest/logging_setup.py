from __future__ import annotations

import logging
from datetime import date
from pathlib import Path


def setup_logging(project_root: Path, *, verbose: bool = False) -> Path:
    """Log INFO+ to outputs/logs/digest_YYYY-MM-DD.log and WARNING+ (or INFO+
    with --verbose) to the console. Returns the log file path."""
    log_dir = project_root / "outputs" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"digest_{date.today().isoformat()}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers when called twice in one process (e.g. tests).
    if not any(
        isinstance(h, logging.FileHandler)
        and getattr(h, "baseFilename", "") == str(log_path)
        for h in root.handlers
    ):
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(file_handler)

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setLevel(logging.INFO if verbose else logging.WARNING)
        console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root.addHandler(console)

    return log_path
