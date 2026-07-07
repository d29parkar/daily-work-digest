from __future__ import annotations

import html
import re
from datetime import date

from .config import DigestConfig

MODE_SUBJECTS = {
    "morning": "Daily Work Brief",
    "night": "End-of-Day Work Digest",
    "trello": "Trello Update",
}


def render_email_text(
    *,
    config: DigestConfig,
    mode: str,
    digest_markdown: str,
    digest_date: date,
) -> tuple[str, str, str, str]:
    """Return (subject, body, preview, html_body).

    ``body`` is the plain-text part of the sent email (the digest markdown).
    ``html_body`` is the HTML alternative so mail clients show a clean brief
    instead of raw markdown. ``preview`` is the dry-run artifact on disk.
    """
    label = MODE_SUBJECTS.get(mode, "Work Digest")
    subject = f"{config.email.subject_prefix} - {label} - {digest_date.isoformat()}"
    body = digest_markdown
    preview = "\n".join(
        [
            f"To: {config.email.recipient}",
            f"Subject: {subject}",
            "",
            digest_markdown,
        ]
    )
    return subject, body, preview, markdown_to_html(digest_markdown)


def markdown_to_html(markdown: str) -> str:
    """Small, dependency-free renderer for the subset of markdown the digest
    emits: #/##/### headings, - and 1. lists, > blockquotes, --- rules,
    `code`, **bold**, and the _italic_ footer line."""
    out: list[str] = []
    open_list: str | None = None
    quote_lines: list[str] = []

    def close_list() -> None:
        nonlocal open_list
        if open_list:
            out.append(f"</{open_list}>")
            open_list = None

    def flush_quote() -> None:
        nonlocal quote_lines
        if quote_lines:
            out.append(
                "<blockquote>" + "<br>".join(quote_lines) + "</blockquote>"
            )
            quote_lines = []

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            close_list()
            flush_quote()
            continue
        if line.startswith(">"):
            close_list()
            quote_lines.append(_inline(line.lstrip("> ").strip()))
            continue
        flush_quote()
        if line == "---":
            close_list()
            out.append("<hr>")
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue
        if line.startswith("- "):
            if open_list != "ul":
                close_list()
                out.append("<ul>")
                open_list = "ul"
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        ordered = re.match(r"^\d+\.\s+(.*)$", line)
        if ordered:
            if open_list != "ol":
                close_list()
                out.append("<ol>")
                open_list = "ol"
            out.append(f"<li>{_inline(ordered.group(1))}</li>")
            continue
        close_list()
        if line.startswith("_") and line.endswith("_") and len(line) > 2:
            out.append(f"<p class='footnote'>{_inline(line[1:-1])}</p>")
        else:
            out.append(f"<p>{_inline(line)}</p>")

    close_list()
    flush_quote()

    return (
        "<html><head><meta charset='utf-8'><style>"
        "body{font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        "color:#1f2328;max-width:680px;margin:0 auto;padding:16px;line-height:1.45;}"
        "h1{font-size:20px;border-bottom:2px solid #d0d7de;padding-bottom:6px;}"
        "h2{font-size:15px;margin:18px 0 6px;border-bottom:1px solid #e3e8ee;"
        "padding-bottom:3px;}"
        "h3{font-size:13px;margin:12px 0 4px;}"
        "p{margin:6px 0;}ul,ol{margin:4px 0 10px;padding-left:22px;}li{margin:3px 0;}"
        "code{background:#f0f2f5;border-radius:3px;padding:1px 4px;"
        "font-family:Consolas,Menlo,monospace;font-size:12px;}"
        "blockquote{border-left:3px solid #d0d7de;margin:8px 0;padding:4px 12px;"
        "color:#57606a;background:#f8f9fb;}"
        "hr{border:none;border-top:1px solid #d0d7de;margin:16px 0;}"
        ".footnote{color:#6e7781;font-size:12px;}"
        "</style></head><body>" + "\n".join(out) + "</body></html>"
    )


def _inline(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped
