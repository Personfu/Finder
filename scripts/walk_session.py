#!/usr/bin/env python3
"""
Parse and update a Daily Hunt markdown file produced by `hunt_score.py`.

VAULT WRITE RULE: this script never writes to the Obsidian vault directly.
The `apply` mode reads the daily hunt content (passed as a path or via
--input-tmp), produces the updated content into tmp/daily_hunt_updated.md
and emits the enriched decision payload on stdout. The calling skill is
responsible for using mcp__obsidian__write_note to push the updated
content back to the vault.

Two modes:

    walk_session.py parse <daily-hunt.md|-(stdin)>
        Extracts candidates as a JSON array on stdout. Each candidate has:
        category, company, title, url, comp, comp_tier, source, posted_at,
        location, remote, score_total, hits_summary, excerpt, decision
        (None if not yet decided, else parsed from an existing Decision line).

    walk_session.py apply <daily-hunt.md|-(stdin)> <decisions.json>
                          [--out tmp/daily_hunt_updated.md]
        Reads the daily hunt content, applies decisions, writes the updated
        markdown to the --out tmp path (default: tmp/daily_hunt_updated.md),
        and prints the enriched decision payload to stdout for piping into
        `tracker_sync.py append`.

Decision JSON shape (one entry per posting being decided):
    {
        "url":         "<from the Link bullet>",
        "decision":    "apply" | "skip" | "defer",
        "notes":       "...",                 # optional
        "defer_until": "YYYY-MM-DD"           # optional, only for defer
    }
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_APPLY_OUT = REPO_ROOT / "tmp" / "daily_hunt_updated.md"


def _read_input(path_or_dash: str) -> str:
    """Read from a file path, or from stdin when path is '-'."""
    if path_or_dash == "-":
        return sys.stdin.read()
    p = Path(path_or_dash)
    if not p.exists():
        raise FileNotFoundError(f"input not found: {p}")
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Markdown parser (purpose-built for hunt_score.py output)
# ---------------------------------------------------------------------------

_CATEGORY_HEADER_RE = re.compile(r"^##\s+(.+?)\s+\((\d+)\)\s*(?:—.*)?$")
_POSTING_HEADER_RE = re.compile(r"^###\s+(.+?)\s+—\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^-\s+\*\*(.+?):\*\*\s*(.*)$")
# Comp bullet looks like: "- **Comp:** $320,000 (preferred)"
_COMP_TIER_RE = re.compile(r"\(([a-z\s]+)\)\s*$")
_SOURCE_LINE_RE = re.compile(
    r"\*\*Source:\*\*\s+(?P<source>\S+)\s+·\s+\*\*Posted:\*\*\s+(?P<posted>\S+)\s+·\s+\*\*Location:\*\*\s+(?P<location>.+?)\s+·\s+\*\*Remote:\*\*\s+(?P<remote>\S+)"
)
_FIT_TOTAL_RE = re.compile(r"\*\*Total:\*\*\s+(?P<total>-?\d+)")


def parse_daily_hunt(text: str) -> list[dict[str, Any]]:
    """
    Walk a Daily Hunt markdown body and yield structured candidates.

    Postings inside `## Excluded` are skipped — they aren't candidates for the
    walk session.
    """
    candidates: list[dict[str, Any]] = []
    current_category: str | None = None
    current: dict[str, Any] | None = None
    in_excluded = False

    def flush():
        if current is not None:
            candidates.append(current)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # Category header
        m_cat = _CATEGORY_HEADER_RE.match(line)
        if m_cat:
            flush()
            current = None
            current_category = m_cat.group(1).strip()
            in_excluded = current_category.lower() == "excluded"
            continue

        if in_excluded:
            continue

        # Posting header
        m_post = _POSTING_HEADER_RE.match(line)
        if m_post:
            flush()
            current = {
                "category": current_category,
                "company": m_post.group(1).strip(),
                "title": m_post.group(2).strip(),
                "url": "",
                "source": "",
                "posted_at": "",
                "location": "",
                "remote": "",
                "comp": "",
                "comp_tier": "",
                "score_total": 0,
                "hits_summary": "",
                "excerpt": "",
                "status": "",
                "decision": None,
                "decision_notes": "",
            }
            continue

        if current is None:
            continue

        m_bullet = _BULLET_RE.match(line)
        if not m_bullet:
            continue
        key = m_bullet.group(1).strip().lower()
        value = m_bullet.group(2).strip()

        if key == "source":
            # Composite line: "Source: greenhouse · Posted: ... · Location: ... · Remote: ..."
            full = m_bullet.group(0).lstrip("- ").strip()
            m_full = _SOURCE_LINE_RE.search(full)
            if m_full:
                current["source"] = m_full.group("source")
                current["posted_at"] = m_full.group("posted")
                current["location"] = m_full.group("location").strip()
                current["remote"] = m_full.group("remote")
            else:
                current["source"] = value
        elif key == "comp":
            current["comp"] = value
            m_tier = _COMP_TIER_RE.search(value)
            if m_tier:
                current["comp_tier"] = m_tier.group(1).strip()
        elif key == "fit":
            current["hits_summary"] = value
            m_total = _FIT_TOTAL_RE.search(value)
            if m_total:
                try:
                    current["score_total"] = int(m_total.group("total"))
                except ValueError:
                    pass
        elif key == "link":
            current["url"] = value
        elif key == "excerpt":
            current["excerpt"] = value
        elif key == "status":
            current["status"] = value
        elif key == "decision":
            # Format we write: "apply (drafted 2026-05-06) — note text"
            current["decision"] = _parse_decision_value(value)
            current["decision_notes"] = value

    flush()
    return candidates


_DECISION_PARSE_RE = re.compile(r"^\s*(apply|skip|defer)\b", re.IGNORECASE)


def _parse_decision_value(value: str) -> str | None:
    m = _DECISION_PARSE_RE.match(value)
    return m.group(1).lower() if m else None


# ---------------------------------------------------------------------------
# Apply decisions: rewrite the file in place, inserting **Decision:** bullets
# ---------------------------------------------------------------------------

def _matches(candidate_url: str, candidate_company: str, candidate_title: str, decision: dict[str, Any]) -> bool:
    if decision.get("url") and candidate_url:
        return decision["url"].rstrip("/") == candidate_url.rstrip("/")
    if decision.get("company") and decision.get("title"):
        return (
            decision["company"].lower() == candidate_company.lower()
            and decision["title"].lower() == candidate_title.lower()
        )
    return False


def _scrub_notes(notes: str | None) -> str:
    """
    Sanitize decision notes before they get interpolated into the daily-hunt
    markdown. Strips newlines (would break the bullet) and any leading
    markdown structural prefixes that could create phantom postings or
    sections when the next /finder:walk parses the file.
    """
    if not notes:
        return ""
    one_line = re.sub(r"[\r\n]+", " ", notes).strip()
    # Hard reject: notes starting with markdown headers / blockquotes / fences.
    # Replace, don't error — the walk skill collected these from the user and
    # we'd rather neutralize than crash.
    one_line = re.sub(r"^[#>`\-=*]{1,}\s*", "", one_line)
    # Inline header injection later in the string ("foo\n### fake posting") is
    # already handled by the newline strip above. Defense-in-depth: replace
    # any remaining backticks so they can't reopen code spans.
    one_line = one_line.replace("`", "ʼ")
    return one_line


def apply_decisions(text: str, decisions: list[dict[str, Any]], today: _dt.date) -> tuple[str, list[dict[str, Any]]]:
    """
    Walk the markdown, find each posting that matches one of the decisions,
    and insert/replace a `**Decision:**` bullet inside its block.

    Returns (new_text, enriched_decisions) where enriched_decisions is the
    same list but with company/title/comp/source/url filled in from the
    matched candidates — ready for tracker_sync.append.
    """
    lines = text.splitlines(keepends=False)
    out_lines: list[str] = []
    enriched: list[dict[str, Any]] = []
    posting_buffer: list[str] | None = None
    posting_meta: dict[str, Any] | None = None
    posting_excluded = False  # tracks whether the buffered posting is in Excluded
    in_excluded = False
    current_category: str | None = None

    def _decision_bullet(d: dict[str, Any]) -> str:
        parts = [d["decision"]]
        if d["decision"] == "defer" and d.get("defer_until"):
            until = re.sub(r"[^0-9\-]", "", d["defer_until"])  # keep YYYY-MM-DD shape only
            if until:
                parts.append(f"until {until}")
        parts.append(f"({today.isoformat()})")
        head = " ".join(parts)
        notes = _scrub_notes(d.get("notes"))
        if notes:
            return f"- **Decision:** {head} — {notes}"
        return f"- **Decision:** {head}"

    def flush_posting() -> None:
        nonlocal posting_buffer, posting_meta, posting_excluded
        if posting_buffer is None or posting_meta is None:
            return
        # NEVER write a Decision bullet onto an Excluded-section posting,
        # even if a decision URL accidentally matches. Excluded means the
        # hunt scorer dropped it; decisions are only meaningful for surfaced
        # candidates.
        if posting_excluded:
            out_lines.extend(posting_buffer)
            posting_buffer = None
            posting_meta = None
            posting_excluded = False
            return

        match: dict[str, Any] | None = None
        for d in decisions:
            if _matches(posting_meta["url"], posting_meta["company"], posting_meta["title"], d):
                match = d
                break
        if match is not None:
            # Strip any existing **Decision:** bullet, append a fresh one.
            new_block: list[str] = []
            for ln in posting_buffer:
                if _BULLET_RE.match(ln) and ln.lower().startswith("- **decision:**"):
                    continue
                new_block.append(ln)
            new_block.append(_decision_bullet(match))
            out_lines.extend(new_block)
            enriched.append({
                **match,
                "company": posting_meta["company"],
                "title": posting_meta["title"],
                "url": posting_meta["url"] or match.get("url", ""),
                "comp": posting_meta["comp"],
                "source": posting_meta["source"],
                "category": current_category,
                "decided_on": today.isoformat(),
            })
        else:
            out_lines.extend(posting_buffer)
        posting_buffer = None
        posting_meta = None
        posting_excluded = False

    for raw_line in lines:
        line = raw_line.rstrip("\n")

        m_cat = _CATEGORY_HEADER_RE.match(line)
        if m_cat:
            flush_posting()
            current_category = m_cat.group(1).strip()
            in_excluded = current_category.lower() == "excluded"
            out_lines.append(line)
            continue

        m_post = _POSTING_HEADER_RE.match(line)
        if m_post:
            flush_posting()
            posting_buffer = [line]
            posting_excluded = in_excluded
            posting_meta = {
                "company": m_post.group(1).strip(),
                "title": m_post.group(2).strip(),
                "url": "",
                "source": "",
                "comp": "",
            }
            continue

        if posting_buffer is not None and posting_meta is not None:
            # Excluded postings still need their lines preserved verbatim.
            posting_buffer.append(line)
            if not posting_excluded:
                m_bullet = _BULLET_RE.match(line)
                if m_bullet:
                    key = m_bullet.group(1).strip().lower()
                    value = m_bullet.group(2).strip()
                    if key == "link":
                        posting_meta["url"] = value
                    elif key == "comp":
                        posting_meta["comp"] = value
                    elif key == "source":
                        full = m_bullet.group(0).lstrip("- ").strip()
                        m_full = _SOURCE_LINE_RE.search(full)
                        if m_full:
                            posting_meta["source"] = m_full.group("source")
        else:
            out_lines.append(line)

    flush_posting()
    return ("\n".join(out_lines) + "\n", enriched)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_parse(args: argparse.Namespace) -> int:
    try:
        text = _read_input(args.daily_hunt)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    candidates = parse_daily_hunt(text)
    print(json.dumps(candidates, indent=2, ensure_ascii=False))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    try:
        text = _read_input(args.daily_hunt)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2

    decisions_path = Path(args.decisions_file)
    if not decisions_path.exists():
        print(f"decisions file not found: {decisions_path}", file=sys.stderr)
        return 2
    raw = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions = raw.get("decisions") if isinstance(raw, dict) and "decisions" in raw else raw
    if not isinstance(decisions, list):
        print("decisions payload must be a list (or {decisions: [...]})", file=sys.stderr)
        return 2

    today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
    new_text, enriched = apply_decisions(text, decisions, today)

    if args.dry_run:
        print(f"DRY RUN: would update {len(enriched)} of {len(decisions)} requested decisions", file=sys.stderr)
    else:
        # NEVER write to the vault directly — emit to a tmp path. The skill
        # prompt is responsible for routing this to the vault via
        # mcp__obsidian__write_note.
        out_path = Path(args.out) if args.out else DEFAULT_APPLY_OUT
        # Atomic write: a crash mid-write must not produce a half-baked file
        # that the skill then pushes to the vault via MCP.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_name(out_path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, out_path)
        print(f"wrote updated daily hunt to {out_path} ({len(enriched)} decision marker(s))", file=sys.stderr)
        print(f"  -> assistant should mcp__obsidian__write_note path='Job Search/Daily Hunt/<date>.md' content=<file contents>", file=sys.stderr)

    print(json.dumps({"decisions": enriched}, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Parse and apply decisions to a Daily Hunt note.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("parse", help="Extract candidates from a Daily Hunt md.")
    sp.add_argument("daily_hunt")
    sp.set_defaults(func=cmd_parse)

    sp = sub.add_parser("apply", help="Apply decisions to a Daily Hunt md, emit updated content to a tmp path (NOT the vault) plus enriched payload on stdout.")
    sp.add_argument("daily_hunt", help="Path to daily hunt md, or '-' to read from stdin")
    sp.add_argument("decisions_file")
    sp.add_argument("--today", default=None)
    sp.add_argument("--out", default=None, help=f"Tmp output path (default: {DEFAULT_APPLY_OUT})")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_apply)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
