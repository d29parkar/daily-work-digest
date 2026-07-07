"""Optional Gmail / Google Calendar ingestion (later-stage integrations).

These are intentionally stubs in the first production version:

- Disabled by default (``integrations.gmail.enabled`` /
  ``integrations.calendar.enabled`` in config.yaml).
- When enabled, they are strictly READ-ONLY by design. The planned scopes are
  ``gmail.readonly`` and ``calendar.readonly`` only. Nothing is ever sent,
  deleted, archived, labeled, modified, or replied to.
- Until the OAuth flow is implemented, enabling them just adds a clear
  "enabled but not yet implemented" note to the digest's source coverage.

See docs/GOOGLE_INTEGRATIONS.md for the integration plan (including the MCP
option) and the exact scopes that will be requested.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import DigestConfig

logger = logging.getLogger(__name__)

GMAIL_PLANNED_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_PLANNED_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


@dataclass(frozen=True)
class GoogleSourceStatus:
    name: str
    enabled: bool
    available: bool
    detail: str


def gmail_status(config: DigestConfig) -> GoogleSourceStatus:
    if not config.integrations.gmail_enabled:
        return GoogleSourceStatus(
            name="gmail",
            enabled=False,
            available=False,
            detail="Disabled by config (integrations.gmail.enabled: false).",
        )
    return GoogleSourceStatus(
        name="gmail",
        enabled=True,
        available=False,
        detail=(
            "Enabled in config, but the read-only Gmail ingester is not implemented "
            f"yet (planned scope: {GMAIL_PLANNED_SCOPE}). See docs/GOOGLE_INTEGRATIONS.md."
        ),
    )


def calendar_status(config: DigestConfig) -> GoogleSourceStatus:
    if not config.integrations.calendar_enabled:
        return GoogleSourceStatus(
            name="calendar",
            enabled=False,
            available=False,
            detail="Disabled by config (integrations.calendar.enabled: false).",
        )
    return GoogleSourceStatus(
        name="calendar",
        enabled=True,
        available=False,
        detail=(
            "Enabled in config, but the read-only Calendar ingester is not implemented "
            f"yet (planned scope: {CALENDAR_PLANNED_SCOPE}). See docs/GOOGLE_INTEGRATIONS.md."
        ),
    )


def log_google_integration_status(config: DigestConfig) -> None:
    for status in (gmail_status(config), calendar_status(config)):
        if status.enabled and not status.available:
            logger.warning("%s: %s", status.name, status.detail)
