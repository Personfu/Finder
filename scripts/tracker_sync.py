#!/usr/bin/env python3
"""
Maintain the Finder tracker.

Source of truth lives in `data/tracker.json` (gitignored — local state per
machine, but synced via the Obsidian vault's Tracker.md mirror for visibility).

Schema (each decision):
    {
        "id":               "<sha1 of url|company-title>",
        "url":              "https://...",
        "company":          "Anthropic",
        "title":            "Security Engineer, Threat Intel",
        "comp":             "$300,000",            # human-readable
        "source":           "greenhouse",
        "category":         "Strong Fit",          # at decision time
        "decided_on":       "2026-05-06",
        "decision":         "apply" | "skip" | "defer",
        "defer_until":      "2026-06-15" | null,
        "notes":            "",
        "status":           "decided" | "drafted" | "submitted" | "rejected"
                            | "interviewing" | "offer" | "withdrew",
        "applied_on":       null | "YYYY-MM-DD",
        "resume_doc_url":   null,
        "cover_letter_url": null,
        "ats_score":        null
    }

CLI surface:
    tracker_sync.py append <decisions.json>            # append + regen MD + print sheet rows
    tracker_sync.py status <url> <new-status> [--notes ...]  # update one entry
    tracker_sync.py regen [--out <path>]               # regenerate Tracker.md only
    tracker_sync.py list [--decision apply|skip|defer] # dump JSON to stdout
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """
    Write text to ``path`` atomically: write to a sibling tempfile and
    ``os.replace`` it into position. A crash mid-write leaves the original
    file untouched instead of producing a truncated/corrupt result.
    Atomic on POSIX and Windows NTFS.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding=encoding)
    os.replace(tmp, path)

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKER_JSON = REPO_ROOT / "data" / "tracker.json"
TMP_TRACKER_MD = REPO_ROOT / "tmp" / "tracker.md"

# Vault rule: this script does NOT write to the vault directly. It emits the
# rendered Tracker.md to tmp/tracker.md and prints the path to stderr so the
# calling skill can read it and use mcp__obsidian__write_note to persist it.
# The vault path constant below is informational only — the skill prompt
# routes the actual write.
DEFAULT_VAULT_RELPATH = "Job Search/Tracker.md"

VALID_DECISIONS = {"apply", "skip", "defer"}
VALID_STATUSES = {
    "decided", "drafted", "submitted",
    "rejected", "interviewing", "offer", "withdrew",
}

# Columns in the order the Google Sheet expects them — keep in sync.
SHEET_COLUMNS = (
    "Company", "Role", "Link", "Applied", "Status",
    "Comp", "Source", "Resume Doc", "Cover Letter", "ATS Score", "Notes",
)


def _normalize_url(url: str) -> str:
    """Lowercase host, strip default ports + trailing slash, drop fragment."""
    u = (url or "").strip()
    if not u:
        return ""
    try:
        parsed = urllib.parse.urlsplit(u)
    except ValueError:
        return u
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.hostname or "").lower()
    if parsed.port and not (
        (scheme == "http" and parsed.port == 80) or (scheme == "https" and parsed.port == 443)
    ):
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def _stable_id(url: str, company: str, title: str) -> str:
    base = _normalize_url(url) or f"{company}|{title}".lower()
    # SHA-256 truncated to 16 hex chars — collision-safe at this scale and
    # avoids bandit/semgrep noise about SHA-1.
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def load_tracker() -> list[dict[str, Any]]:
    if not TRACKER_JSON.exists():
        return []
    try:
        data = json.loads(TRACKER_JSON.read_text(encoding="utf-8"))
        return data.get("decisions", []) if isinstance(data, dict) else []
    except json.JSONDecodeError as e:
        # Hard-fail rather than silently returning []. A silent reset would
        # cause the next save to clobber every prior decision. Back up the
        # corrupt file so the user can inspect it.
        backup = TRACKER_JSON.with_name(
            TRACKER_JSON.name + f".corrupt.{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        try:
            os.replace(TRACKER_JSON, backup)
            hint = f" — corrupt file moved to {backup.name}"
        except OSError:
            hint = ""
        raise RuntimeError(
            f"tracker.json is malformed: {e}{hint}. "
            "Refusing to continue — fix or restore the file before re-running."
        ) from e


def save_tracker(decisions: list[dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "decisions": decisions,
    }
    _atomic_write_text(TRACKER_JSON, json.dumps(payload, indent=2, ensure_ascii=False))


def upsert_decision(decisions: list[dict[str, Any]], new: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """
    Insert or update a decision. Returns (created, final_entry).
    Match key: id (preferred) or normalized url (fallback).
    """
    new = dict(new)
    if not new.get("id"):
        new["id"] = _stable_id(new.get("url", ""), new.get("company", ""), new.get("title", ""))
    new.setdefault("decided_on", _dt.date.today().isoformat())
    new.setdefault("decision", "apply")
    if new["decision"] not in VALID_DECISIONS:
        raise ValueError(f"invalid decision: {new['decision']!r}")
    new.setdefault("status", "decided")
    if new["status"] not in VALID_STATUSES:
        raise ValueError(f"invalid status: {new['status']!r}")
    new.setdefault("notes", "")
    new.setdefault("applied_on", None)
    new.setdefault("resume_doc_url", None)
    new.setdefault("cover_letter_url", None)
    new.setdefault("ats_score", None)
    new.setdefault("defer_until", None)

    new_url_norm = _normalize_url(new.get("url", ""))
    for i, existing in enumerate(decisions):
        same_id = existing.get("id") == new["id"]
        same_url = bool(new_url_norm) and _normalize_url(existing.get("url", "")) == new_url_norm
        if same_id or same_url:
            # Preserve fields the new entry didn't fill (e.g., a re-decision
            # later won't blank out resume_doc_url that /finder:apply set).
            merged = {**existing, **{k: v for k, v in new.items() if v not in (None, "")}}
            decisions[i] = merged
            return False, merged
    decisions.append(new)
    return True, new


def update_status(
    decisions: list[dict[str, Any]],
    url: str,
    new_status: str,
    notes: str | None = None,
    resume_doc_url: str | None = None,
    cover_letter_url: str | None = None,
    ats_score: float | None = None,
) -> dict[str, Any] | None:
    """
    Immutable update — returns the new dict and replaces the entry in-list.
    Notes are deduplicated: the same note string isn't appended twice in a row.
    Optional fields (resume_doc_url, cover_letter_url, ats_score) are
    populated by /finder:publish when the Drive Docs land.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {new_status!r}")
    target_url_norm = _normalize_url(url)
    for i, entry in enumerate(decisions):
        same_id = entry.get("id") == url
        same_url = bool(target_url_norm) and _normalize_url(entry.get("url", "")) == target_url_norm
        if not (same_id or same_url):
            continue

        existing_notes = entry.get("notes") or ""
        if notes:
            note_clean = notes.strip()
            if note_clean and note_clean not in existing_notes:
                new_notes = (existing_notes + "\n" + note_clean) if existing_notes else note_clean
            else:
                new_notes = existing_notes
        else:
            new_notes = existing_notes

        updated = {
            **entry,
            "status": new_status,
            "notes": new_notes,
        }
        if resume_doc_url:
            updated["resume_doc_url"] = resume_doc_url
        if cover_letter_url:
            updated["cover_letter_url"] = cover_letter_url
        if ats_score is not None:
            updated["ats_score"] = ats_score
        if new_status == "submitted" and not entry.get("applied_on"):
            updated["applied_on"] = _dt.date.today().isoformat()
        decisions[i] = updated
        return updated
    return None


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_STATUS_ORDER = (
    ("offer", "🎯 Offer"),
    ("interviewing", "🗣 Interviewing"),
    ("submitted", "📨 Submitted (awaiting response)"),
    ("drafted", "📝 Drafted (not yet submitted)"),
    ("decided", "✅ Decided to apply (no draft yet)"),
    ("rejected", "❌ Rejected"),
    ("withdrew", "↩️ Withdrew"),
)


def render_tracker_md(decisions: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# Finder Tracker")
    lines.append("")
    lines.append(f"_Auto-generated by `tracker_sync.py` — source of truth: `Finder/data/tracker.json`. Manual edits here are clobbered on next sync._")
    lines.append("")
    lines.append(f"_Last updated: {_dt.datetime.now().isoformat(timespec='minutes')}_")
    lines.append("")

    # Summary
    total = len(decisions)
    apply_count = sum(1 for d in decisions if d.get("decision") == "apply")
    submitted = sum(1 for d in decisions if d.get("status") == "submitted")
    interviewing = sum(1 for d in decisions if d.get("status") == "interviewing")
    rejected = sum(1 for d in decisions if d.get("status") == "rejected")
    skip = sum(1 for d in decisions if d.get("decision") == "skip")
    defer = sum(1 for d in decisions if d.get("decision") == "defer")
    lines.append(f"**Total tracked:** {total} · **Apply intent:** {apply_count} · **Submitted:** {submitted} · **Interviewing:** {interviewing} · **Rejected:** {rejected} · **Skipped:** {skip} · **Deferred:** {defer}")
    lines.append("")

    # Apply intents grouped by status
    apply_decisions = [d for d in decisions if d.get("decision") == "apply"]
    for status_key, status_label in _STATUS_ORDER:
        rows = [d for d in apply_decisions if d.get("status") == status_key]
        if not rows:
            continue
        lines.append(f"## {status_label} ({len(rows)})")
        lines.append("")
        lines.append("| Company | Role | Comp | Source | Decided | Applied | ATS | Resume | Notes |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        rows.sort(key=lambda d: (d.get("decided_on") or "", d.get("company") or ""))
        for d in rows:
            company = (d.get("company") or "").replace("|", "/")
            title = (d.get("title") or "").replace("|", "/")
            comp = (d.get("comp") or "").replace("|", "/")
            source = d.get("source") or ""
            decided = d.get("decided_on") or ""
            applied = d.get("applied_on") or ""
            ats = d.get("ats_score") or ""
            url = d.get("url") or ""
            resume_url = d.get("resume_doc_url") or ""
            resume_link = f"[doc]({resume_url})" if resume_url else ""
            link_title = f"[{title}]({url})" if url else title
            notes = (d.get("notes") or "").replace("\n", " · ").replace("|", "/")
            lines.append(f"| {company} | {link_title} | {comp} | {source} | {decided} | {applied} | {ats} | {resume_link} | {notes} |")
        lines.append("")

    # Skip / defer sections (compact)
    skip_rows = [d for d in decisions if d.get("decision") == "skip"]
    if skip_rows:
        lines.append(f"## ⏭ Skipped ({len(skip_rows)})")
        lines.append("")
        lines.append("_Surfaced in a daily hunt but consciously skipped. Won't re-surface (URLs are tracked here)._")
        lines.append("")
        lines.append("| Company | Role | Why |")
        lines.append("|---|---|---|")
        skip_rows.sort(key=lambda d: d.get("decided_on") or "", reverse=True)
        for d in skip_rows[:50]:
            company = (d.get("company") or "").replace("|", "/")
            title = (d.get("title") or "").replace("|", "/")
            url = d.get("url") or ""
            link_title = f"[{title}]({url})" if url else title
            why = (d.get("notes") or "").replace("\n", " · ").replace("|", "/") or "(no note)"
            lines.append(f"| {company} | {link_title} | {why} |")
        if len(skip_rows) > 50:
            lines.append(f"| _...{len(skip_rows) - 50} more — see tracker.json_ | | |")
        lines.append("")

    defer_rows = [d for d in decisions if d.get("decision") == "defer"]
    if defer_rows:
        lines.append(f"## ⏳ Deferred ({len(defer_rows)})")
        lines.append("")
        lines.append("_Worth revisiting later — the daily hunt won't re-surface these until `defer_until` passes._")
        lines.append("")
        lines.append("| Company | Role | Defer Until | Why |")
        lines.append("|---|---|---|---|")
        defer_rows.sort(key=lambda d: d.get("defer_until") or "9999")
        for d in defer_rows:
            company = (d.get("company") or "").replace("|", "/")
            title = (d.get("title") or "").replace("|", "/")
            url = d.get("url") or ""
            link_title = f"[{title}]({url})" if url else title
            until = d.get("defer_until") or "(no date)"
            why = (d.get("notes") or "").replace("\n", " · ").replace("|", "/") or "(no note)"
            lines.append(f"| {company} | {link_title} | {until} | {why} |")
        lines.append("")

    return "\n".join(lines)


def write_tracker_md_tmp(decisions: list[dict[str, Any]]) -> Path:
    """
    Render Tracker.md to tmp/tracker.md (NOT the vault — see module docstring).
    Returns the tmp path. The calling skill is responsible for routing this
    to the vault via mcp__obsidian__write_note.
    """
    _atomic_write_text(TMP_TRACKER_MD, render_tracker_md(decisions))
    return TMP_TRACKER_MD


# ---------------------------------------------------------------------------
# Sheet row formatting (for manual paste into the Google Sheet)
# ---------------------------------------------------------------------------

def _scrub_cell(v: Any) -> str:
    """
    Scrub a value for safe TAB-delimited paste into a Google Sheet.
    Replaces \\t / \\n / \\r so a single hostile field can't column-shift
    every cell to its right (silently corrupting the sheet on paste).
    """
    s = "" if v is None else str(v)
    return (
        s.replace("\t", " ")
         .replace("\r", "")
         .replace("\n", " · ")
    )


def sheet_row(d: dict[str, Any]) -> str:
    cells = [
        _scrub_cell(d.get("company")),
        _scrub_cell(d.get("title")),
        _scrub_cell(d.get("url")),
        _scrub_cell(d.get("applied_on")),
        _scrub_cell(d.get("status") or "decided"),
        _scrub_cell(d.get("comp")),
        _scrub_cell(d.get("source")),
        _scrub_cell(d.get("resume_doc_url")),
        _scrub_cell(d.get("cover_letter_url")),
        _scrub_cell(d.get("ats_score")),
        _scrub_cell(d.get("notes")),
    ]
    return "\t".join(cells)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_append(args: argparse.Namespace) -> int:
    payload_path = Path(args.decisions_file)
    if not payload_path.exists():
        print(f"decisions file not found: {payload_path}", file=sys.stderr)
        return 2
    new_decisions = json.loads(payload_path.read_text(encoding="utf-8"))
    if isinstance(new_decisions, dict) and "decisions" in new_decisions:
        new_decisions = new_decisions["decisions"]
    if not isinstance(new_decisions, list):
        print("decisions payload must be a list (or {decisions: [...]})", file=sys.stderr)
        return 2

    existing = load_tracker()
    new_count = 0
    updated_count = 0
    apply_rows: list[dict[str, Any]] = []
    for entry in new_decisions:
        try:
            created, final = upsert_decision(existing, entry)
        except ValueError as e:
            print(f"skipping {entry.get('url') or entry.get('title')}: {e}", file=sys.stderr)
            continue
        if created:
            new_count += 1
        else:
            updated_count += 1
        if final.get("decision") == "apply":
            apply_rows.append(final)
    save_tracker(existing)
    print(f"tracker: {new_count} new, {updated_count} updated, total {len(existing)}", file=sys.stderr)

    md_path = write_tracker_md_tmp(existing)
    print(f"rendered Tracker.md to {md_path}", file=sys.stderr)
    print(f"  -> assistant should mcp__obsidian__write_note path={DEFAULT_VAULT_RELPATH!r} content=<file contents>", file=sys.stderr)

    if apply_rows:
        print("# Sheet rows to paste (TAB-separated, Apply intents):", file=sys.stderr)
        print("# columns: " + " | ".join(SHEET_COLUMNS))
        for row in apply_rows:
            print(sheet_row(row))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    decisions = load_tracker()
    updated = update_status(
        decisions,
        args.url,
        args.new_status,
        notes=args.notes,
        resume_doc_url=args.resume_doc_url,
        cover_letter_url=args.cover_letter_url,
        ats_score=args.ats_score,
    )
    if not updated:
        print(f"no entry found for {args.url!r}", file=sys.stderr)
        return 1
    save_tracker(decisions)
    md_path = write_tracker_md_tmp(decisions)
    print(f"updated {updated.get('company')} — {updated.get('title')}: status={args.new_status}", file=sys.stderr)
    print(f"rendered Tracker.md to {md_path} — assistant should write via mcp__obsidian__write_note path={DEFAULT_VAULT_RELPATH!r}", file=sys.stderr)
    return 0


def cmd_regen(args: argparse.Namespace) -> int:
    decisions = load_tracker()
    if args.out:
        _atomic_write_text(Path(args.out), render_tracker_md(decisions))
        print(f"wrote {args.out}", file=sys.stderr)
        return 0
    md_path = write_tracker_md_tmp(decisions)
    print(f"rendered Tracker.md to {md_path}", file=sys.stderr)
    print(f"  -> assistant should mcp__obsidian__write_note path={DEFAULT_VAULT_RELPATH!r} content=<file contents>", file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    decisions = load_tracker()
    if args.decision:
        decisions = [d for d in decisions if d.get("decision") == args.decision]
    print(json.dumps(decisions, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Maintain Finder tracker JSON + vault Tracker.md mirror.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("append", help="Append decisions from a JSON file (list of decision dicts).")
    sp.add_argument("decisions_file")
    sp.set_defaults(func=cmd_append)

    sp = sub.add_parser("status", help="Update the status of an existing entry by URL.")
    sp.add_argument("url")
    sp.add_argument("new_status", choices=sorted(VALID_STATUSES))
    sp.add_argument("--notes", default=None)
    sp.add_argument("--resume-doc-url", default=None, help="Link to the published resume Google Doc")
    sp.add_argument("--cover-letter-url", default=None, help="Link to the published cover letter Google Doc")
    sp.add_argument("--ats-score", type=float, default=None, help="ATS coverage %% from the apply step")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("regen", help="Regenerate Tracker.md from tracker.json.")
    sp.add_argument("--out", default=None, help="Write to a specific path instead of vault.")
    sp.set_defaults(func=cmd_regen)

    sp = sub.add_parser("list", help="Print tracker entries as JSON.")
    sp.add_argument("--decision", default=None, choices=sorted(VALID_DECISIONS))
    sp.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
