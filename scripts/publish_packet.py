#!/usr/bin/env python3
"""
Publish-step prep for /finder:apply packets.

Expects gws-personal aliases to be available in the caller's environment
(sourced from the user's shell profile).

What this script does:
  1. Validate the packet (placeholders are filled in, manifest readable,
     master snapshot present).
  2. Compute the canonical Google Doc names from the naming pattern:
        {Company}-{ShortRoleTitle}-{MonthYear}
        {Company}-{ShortRoleTitle}-CoverLetter-{MonthYear}
  3. Emit a structured plan (JSON to stdout) describing the gws commands
     the calling skill prompt will run, plus the post-publish tracker
     update args.

What this script does NOT do:
  - Actually invoke gws (the skill prompt does that — keeps the script
    side-effect-free and easy to dry-run).
  - Modify Google Docs content directly. The upload-and-convert flow uploads
    the locally-rendered DOCX with mimeType=application/vnd.google-apps.document
    so Drive materializes a native Doc that matches the DOCX byte-for-byte —
    no manual paste step.

Usage:
    python scripts/publish_packet.py <packet-dir> [--month-year May2026]
                                                  [--validate-only]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKER_JSON_PATH = REPO_ROOT / "data" / "tracker.json"

# Locale-safe month abbreviations. strftime("%b") respects the process
# locale; on a non-en machine the Doc name suffix would silently change.
_MONTHS_EN = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# Per-section heuristic: shorten role titles for the Doc name. Long titles
# like "Senior Security Engineer, Detection and Response" become
# "SeniorSecEngDetectionResponse" — keeps Doc filenames scannable in Drive.
_NOISE_TITLE_WORDS = frozenset({
    "the", "and", "or", "of", "for", "to", "in", "at",
    "a", "an", ",", "&", "/", "-",
})

# Words to abbreviate so the slug stays short.
_ABBREVIATIONS = {
    "engineer": "Eng",
    "engineering": "Eng",
    "analyst": "Analyst",
    "principal": "Principal",
    "senior": "Senior",
    "staff": "Staff",
    "lead": "Lead",
    "manager": "Mgr",
    "director": "Dir",
    "security": "Sec",
    "intelligence": "Intel",
    "operations": "Ops",
    "infrastructure": "Infra",
    "platform": "Platform",
    "detection": "Detection",
    "response": "Response",
    "incident": "IR",
    "threat": "Threat",
}


def _load_local_config() -> dict[str, Any]:
    cfg = REPO_ROOT / "config" / "local.json"
    if not cfg.exists():
        return {}
    return json.loads(cfg.read_text(encoding="utf-8"))


def shorten_role(title: str) -> str:
    """Turn 'Senior Security Engineer, Threat Intel' -> 'SeniorSecEngThreatIntel'."""
    if not title:
        return "Role"
    cleaned = re.sub(r"[^A-Za-z\s]+", " ", title)
    parts = []
    for word in cleaned.split():
        wl = word.lower()
        if wl in _NOISE_TITLE_WORDS:
            continue
        parts.append(_ABBREVIATIONS.get(wl, word.capitalize()))
    short = "".join(parts) or "Role"
    return short[:60]


def short_company(company: str) -> str:
    """Turn 'Acme, Inc.' / 'Acme (Inc.)' -> 'Acme'."""
    if not company:
        return "Company"
    cleaned = re.sub(r"[\(,;].*$", "", company).strip()
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", cleaned)
    return (cleaned or "Company")[:30]


def doc_name(company: str, role: str, month_year: str, suffix: str = "") -> str:
    base = f"{short_company(company)}-{shorten_role(role)}-{month_year}"
    if suffix:
        base = base.replace(f"-{month_year}", f"-{suffix}-{month_year}")
    return base


# ---------------------------------------------------------------------------
# Packet validation
# ---------------------------------------------------------------------------

PLACEHOLDER_MARKERS = (
    "<!-- The /finder:apply skill fills this in",
    "<!-- Placeholder. /finder:apply drafts",
)

# Two-page resume body is ~450-600 words for the short-intro style; a
# longer-prose intro style reliably lands at ~900-950 by this count.
# ≥1000 strongly suggests overflow.
_RESUME_WC_WARN = 1000

# MIME types used in the upload-and-convert flow. Sending these to
# drive.files.create with --upload uploads the DOCX bytes and tells Drive
# to materialize them as a native Google Doc (rather than store the DOCX
# as-is). This is the mechanism that makes the Drive Doc match the local
# DOCX byte-for-byte without a manual paste step.
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_GDOC_MIME = "application/vnd.google-apps.document"


def _doc_id_from_url(url: str | None) -> str | None:
    """Extract the Drive fileId from a docs.google.com/document/d/<ID>/edit URL."""
    if not url:
        return None
    m = re.search(r"/document/d/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None
# At minimum a real tailored resume has section headings for summary +
# experience + skills. Less than this is a half-drafted file.
_MIN_RESUME_HEADINGS = 3


def validate_packet(packet_dir: Path) -> tuple[bool, list[str]]:
    """Return (ok, problems). Problems are human-readable strings."""
    problems: list[str] = []
    if not packet_dir.exists():
        return False, [f"packet dir does not exist: {packet_dir}"]

    required = [
        "manifest.md", "jd.txt", "jd_meta.json", "master_resume.md",
        "ats_keyword_report.json", "tailored_resume.md", "cover_letter.md",
    ]
    for name in required:
        p = packet_dir / name
        if not p.exists():
            problems.append(f"missing {name}")
            continue
        if name in {"tailored_resume.md", "cover_letter.md"}:
            text = p.read_text(encoding="utf-8")
            if any(marker in text for marker in PLACEHOLDER_MARKERS):
                problems.append(f"{name} still contains the apply-skill placeholder — run /finder:apply first")
            elif len(text.strip()) < 200:
                problems.append(f"{name} is suspiciously short ({len(text.strip())} chars) — looks unfilled")

    tailored = packet_dir / "tailored_resume.md"
    if tailored.exists():
        body = tailored.read_text(encoding="utf-8")
        wc = len(body.split())
        if wc > _RESUME_WC_WARN:
            problems.append(
                f"tailored_resume.md word count is {wc} — target ≤700; "
                f"likely overflows 2 pages"
            )
        # Stronger "fully drafted" signal than just length. A real resume has
        # ## headings for summary + experience roles + skills.
        heading_count = sum(1 for ln in body.splitlines() if ln.lstrip().startswith("##"))
        if heading_count < _MIN_RESUME_HEADINGS:
            problems.append(
                f"tailored_resume.md has only {heading_count} ## section headings "
                f"(expected ≥{_MIN_RESUME_HEADINGS}) — looks half-drafted; "
                f"re-run /finder:apply"
            )

    return (not problems), problems


def _load_tracker_entry(jd_url: str) -> dict[str, Any] | None:
    """
    Look up an existing tracker entry by URL. Returns None if no match or
    no tracker file. Used by build_plan to (a) populate company name when
    the JD HTML is missing, and (b) detect already-drafted state so the
    publish skill can guard against duplicate Drive copies.
    """
    if not TRACKER_JSON_PATH.exists() or not jd_url:
        return None
    try:
        data = json.loads(TRACKER_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    decisions = data.get("decisions", []) if isinstance(data, dict) else []
    target = jd_url.strip().rstrip("/").lower()
    for entry in decisions:
        candidate = (entry.get("url") or "").strip().rstrip("/").lower()
        if candidate == target:
            return entry
    return None


# ---------------------------------------------------------------------------
# Emit publish plan
# ---------------------------------------------------------------------------

# Comp string parser. Tracker entries store comp as a human-readable string
# like "$400,000 - $680,000" or "$300,000". The Sheet wants min/max in $K.
# Returns ("400", "680") for "$400,000 - $680,000", ("300", "300") for "$300,000",
# ("", "") if unparseable.
_COMP_NUM_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*[Kk]?")


def parse_comp_to_k(comp: str | None) -> tuple[str, str]:
    if not comp:
        return ("", "")
    nums = _COMP_NUM_RE.findall(comp)
    if not nums:
        return ("", "")

    def _to_k(raw: str) -> str:
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            return ""
        if v >= 1000:  # raw is annual dollars, e.g. "400,000"
            v = v / 1000.0
        # Trim a trailing .0 for integer-valued comps.
        return f"{v:g}"

    mn = _to_k(nums[0])
    mx = _to_k(nums[1]) if len(nums) > 1 else mn
    return (mn, mx)


# Discovery vocabulary for the tracker Sheet (Direct, Referral, Recruiter,
# Inbound, Unknown, etc.). The publish step can't know the correct one
# without human input, so it leaves a placeholder.
_DISCOVERY_PLACEHOLDER = "<discovery>"


def detect_cover_letter_drafted(packet_dir: Path) -> bool:
    """True if cover_letter.md exists and isn't the placeholder stub."""
    p = packet_dir / "cover_letter.md"
    if not p.exists():
        return False
    body = p.read_text(encoding="utf-8").strip()
    # Pipeline's placeholder block starts with `<!-- Placeholder.` — anything
    # past that and non-empty counts as drafted content.
    return bool(body) and not body.startswith("<!--")


def detect_remote_type(jd_text: str) -> str:
    """Best-effort guess from JD text. Falls back to placeholder if unsure."""
    if not jd_text:
        return "<remote_type>"
    low = jd_text.lower()
    if re.search(r"\bhybrid\b", low):
        return "Hybrid"
    if re.search(r"\bonsite\b|\bon-site\b|\bin-office\b", low):
        return "Onsite"
    if re.search(r"\bremote\b", low):
        return "Remote"
    return "<remote_type>"


def today_us() -> str:
    """Today's date in M/D/YYYY (matches the tracker Sheet's date format)."""
    t = _dt.date.today()
    return f"{t.month}/{t.day}/{t.year}"


def build_plan(
    packet_dir: Path,
    month_year: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    meta = json.loads((packet_dir / "jd_meta.json").read_text(encoding="utf-8"))
    report = json.loads((packet_dir / "ats_keyword_report.json").read_text(encoding="utf-8"))
    jd_url = str(meta.get("url", ""))

    tracker_entry = _load_tracker_entry(jd_url)

    # Pull company/title from the JD content snapshot — the apply pipeline
    # writes it as the first <h1>...</h1> for Greenhouse/Lever. Fall back
    # progressively: tracker entry company (recorded by /finder:walk) >
    # source_hint (last resort, ugly).
    jd_html = (packet_dir / "jd.html").read_text(encoding="utf-8") if (packet_dir / "jd.html").exists() else ""
    title_match = re.search(r"<h1>(.*?)</h1>", jd_html, re.IGNORECASE | re.DOTALL)
    company_match = re.search(r"<strong>\s*Company:\s*</strong>\s*([^<]+)", jd_html, re.IGNORECASE)

    title = (title_match.group(1).strip() if title_match else "") or (
        (tracker_entry or {}).get("title") or ""
    ) or "Role"
    company = (company_match.group(1).strip() if company_match else "") or (
        (tracker_entry or {}).get("company") or ""
    ) or str(meta.get("source_hint") or "Company").title()

    resume_doc_name = doc_name(company, title, month_year)
    cover_letter_doc_name = doc_name(company, title, month_year, suffix="CoverLetter")

    # NOTE: cfg["resume_template_doc_id"] is deprecated by the upload-and-convert
    # flow (we no longer copy a template Doc; we upload the rendered DOCX with
    # mimeType=application/vnd.google-apps.document and Drive converts on the fly).
    # Kept in local.example.json as legacy until next config cleanup.
    sheet_id = str(cfg.get("tracker_sheet_id") or "")
    sheet_tab = str(cfg.get("tracker_sheet_tab") or "Sheet1")

    # shlex.quote the JD URL where it lands in shell-example strings. Doc-name
    # and DOCX-path strings are wrapped via json.dumps / shlex.quote inline where
    # they're emitted.
    qurl = shlex.quote(jd_url)
    ats_score = report.get("coverage_pct")

    # ---- Local DOCX paths the upload-and-convert flow refers to ----
    # The publish skill prompt generates these via md_to_docx.py +
    # cover_letter_to_docx.py before uploading.
    resume_md_rel = packet_dir / "tailored_resume.md"
    resume_docx_rel = packet_dir / f"{resume_doc_name}.docx"
    cover_md_rel = packet_dir / "cover_letter.md"
    cover_docx_rel = packet_dir / f"{cover_letter_doc_name}.docx"

    # ---- Sheet row construction (matches the tracker Sheet's 18-col schema) ----
    # Headers:
    #   A Applied Date | B Company | C Role Title | D Location |
    #   E Remote Type | F Comp Min ($K) | G Comp Max ($K) | H Source |
    #   I Source Detail | J Discovery | K Stage | L Stage Date |
    #   M Days | N Reject Reason | O Resume Variant | P Cover Letter |
    #   Q One-liner | R App Note
    comp = (tracker_entry or {}).get("comp") or ""
    comp_min_k, comp_max_k = parse_comp_to_k(comp)

    source_raw = (tracker_entry or {}).get("source") or str(meta.get("source_hint") or "")
    source_pretty = source_raw.replace("-", " ").replace("_", " ").title() if source_raw else ""

    jd_txt = (packet_dir / "jd.txt").read_text(encoding="utf-8") if (packet_dir / "jd.txt").exists() else ""
    remote_type = detect_remote_type(jd_txt)

    role_archetype = str(meta.get("role_archetype") or "")
    resume_variant = f"Tailored ({role_archetype})" if role_archetype else "Tailored"
    cover_letter_yn = "Y" if detect_cover_letter_drafted(packet_dir) else "N"

    # Sheet quoting: tab names containing spaces must be wrapped in single
    # quotes inside the Sheets API range string ("'Job Tracker'!A:R").
    sheet_range = f"'{sheet_tab}'!A:R" if " " in sheet_tab else f"{sheet_tab}!A:R"
    sheet_row_values = [
        "",                      # A Applied Date (blank until tracker_sync.py status <url> submitted bumps it)
        company,                 # B
        title,                   # C
        "<location>",            # D — placeholder; rarely present in machine-readable form
        remote_type,             # E
        comp_min_k,              # F
        comp_max_k,              # G
        source_pretty,           # H
        jd_url,                  # I Source Detail (full URL; refine to job ID by hand if desired)
        _DISCOVERY_PLACEHOLDER,  # J — needs human input (Direct / Referral / Recruiter / Inbound / etc.)
        "Drafted",               # K Stage
        today_us(),              # L Stage Date
        "0",                     # M Days
        "",                      # N Reject Reason
        resume_variant,          # O
        cover_letter_yn,         # P
        "<one_liner>",           # Q — short pitch tag; skill prompt fills in
        "<app_note_path>",       # R — vault relpath like Job Search/Applications/<X>.md; optional
    ]

    # gws CLI example for the skill prompt. The actual call uses
    # `spreadsheets values append` with structured JSON; the gws-personal
    # wrapper expects the same shape.
    sheet_append_example = (
        "gws-personal sheets spreadsheets values append "
        f"--params '{json.dumps({'spreadsheetId': sheet_id, 'range': sheet_range, 'valueInputOption': 'USER_ENTERED', 'insertDataOption': 'INSERT_ROWS'})}' "
        "--json '{\"values\":[<row_template-with-placeholders-substituted>]}'"
    )

    # Already-drafted guard: if the tracker says this URL is past 'decided',
    # publish would create duplicate Docs on re-run. Surface to the prompt.
    already_published = bool(
        tracker_entry and tracker_entry.get("status") in {"drafted", "submitted", "interviewing", "rejected", "offer", "withdrew"}
    )

    return {
        "packet_dir": str(packet_dir),
        "jd_url": jd_url,
        "company": company,
        "title": title,
        "month_year": month_year,
        "resume_doc_name": resume_doc_name,
        "cover_letter_doc_name": cover_letter_doc_name,
        "ats_score": ats_score,
        "already_published": already_published,
        "existing_tracker_entry": tracker_entry,  # null if not yet tracked
        "gws_commands": {
            "_doc": (
                "These commands are guidance for the publish skill prompt to run with gws-personal "
                "aliases. The Drive uploads (with mimeType=application/vnd.google-apps.document in "
                "the metadata) convert DOCX to native Google Docs on the fly — that's how the Doc "
                "ends up byte-identical to the locally-rendered DOCX instead of an empty template "
                "shell. Past iterations of this pipeline copied a template Doc and required manual "
                "markdown paste; that step is gone."
            ),
            "prep_resume_docx": {
                "intent": "Generate the styled resume DOCX from tailored_resume.md (idempotent — overwrites if exists).",
                "input_path": str(resume_md_rel),
                "output_path": str(resume_docx_rel),
                "example": (
                    f"python ${{CLAUDE_PLUGIN_ROOT}}/scripts/md_to_docx.py "
                    f"{shlex.quote(str(resume_md_rel))} "
                    f"-o {shlex.quote(str(resume_docx_rel))}"
                ),
                "skipped": False,
            },
            "prep_cover_letter_docx": {
                "intent": "Generate the styled cover letter DOCX from cover_letter.md (idempotent).",
                "input_path": str(cover_md_rel),
                "output_path": str(cover_docx_rel),
                "example": (
                    f"python ${{CLAUDE_PLUGIN_ROOT}}/scripts/cover_letter_to_docx.py "
                    f"{shlex.quote(str(cover_md_rel))} "
                    f"-o {shlex.quote(str(cover_docx_rel))}"
                ),
                "skipped": False,
            },
            "upload_resume_docx": {
                "intent": "Upload the resume DOCX to Drive with auto-conversion to Google Docs format. The created Doc renders identically to the local DOCX — no manual paste step.",
                "local_path": str(resume_docx_rel),
                "new_name": resume_doc_name,
                "example": (
                    "gws-personal drive files create "
                    f"--upload {shlex.quote(str(resume_docx_rel))} "
                    f"--upload-content-type {shlex.quote(_DOCX_MIME)} "
                    f"--json '{json.dumps({'name': resume_doc_name, 'mimeType': _GDOC_MIME})}'"
                ),
                "skipped": False,
            },
            "upload_cover_letter_docx": {
                "intent": "Upload the cover letter DOCX to Drive with auto-conversion.",
                "local_path": str(cover_docx_rel),
                "new_name": cover_letter_doc_name,
                "example": (
                    "gws-personal drive files create "
                    f"--upload {shlex.quote(str(cover_docx_rel))} "
                    f"--upload-content-type {shlex.quote(_DOCX_MIME)} "
                    f"--json '{json.dumps({'name': cover_letter_doc_name, 'mimeType': _GDOC_MIME})}'"
                ),
                "skipped": False,
            },
            "trash_stale_docs_on_republish": {
                "intent": "Only relevant when --allow-republish was passed. Move the previously-tracked Doc URLs (resume + cover letter) to Drive trash so the new uploads aren't duplicates. Skips silently if no prior URLs are tracked.",
                "prior_resume_doc_id": _doc_id_from_url((tracker_entry or {}).get("resume_doc_url")),
                "prior_cover_letter_doc_id": _doc_id_from_url((tracker_entry or {}).get("cover_letter_url")),
                "example_template": (
                    "gws-personal drive files update "
                    "--params '{\"fileId\":\"<FILE_ID>\"}' "
                    "--json '{\"trashed\":true}'"
                ),
                "skipped": True,  # the skill prompt flips this based on --allow-republish
            },
            "append_sheet_row": {
                "intent": "Append a row to the tracker Sheet matching the live 18-column layout (Applied Date / Company / Role / Location / Remote Type / Comp Min ($K) / Comp Max ($K) / Source / Source Detail / Discovery / Stage / Stage Date / Days / Reject Reason / Resume Variant / Cover Letter / One-liner / App Note). Doc URLs go in the tracker JSON, not the Sheet.",
                "sheet_id": sheet_id,
                "tab": sheet_tab,
                "range": sheet_range,
                "row_headers": [
                    "Applied Date", "Company", "Role Title", "Location",
                    "Remote Type", "Comp Min ($K)", "Comp Max ($K)", "Source",
                    "Source Detail", "Discovery", "Stage", "Stage Date",
                    "Days", "Reject Reason", "Resume Variant", "Cover Letter",
                    "One-liner", "App Note",
                ],
                "row_template": sheet_row_values,
                "placeholders_to_substitute": [
                    "<location>", "<remote_type>", "<discovery>",
                    "<one_liner>", "<app_note_path>",
                ],
                "example": sheet_append_example,
                "skipped": (not sheet_id),
            },
        },
        "tracker_update": {
            "_doc": "After Drive/Docs URLs are obtained, run this to bump the tracker entry to status=drafted.",
            "url": jd_url,
            "new_status": "drafted",
            "ats_score": ats_score,
            "example": (
                f"python ${{CLAUDE_PLUGIN_ROOT}}/scripts/tracker_sync.py status "
                f"{qurl} drafted "
                f"--resume-doc-url <RESUME_URL> "
                f"--cover-letter-url <COVER_URL> "
                f"--ats-score {ats_score or 0}"
            ),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_month_year() -> str:
    """Locale-safe — strftime('%b') would localize the month abbreviation."""
    today = _dt.date.today()
    return f"{_MONTHS_EN[today.month - 1]}{today.year}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate a packet + emit the gws-personal publish plan.")
    p.add_argument("packet_dir", help="Path to an applications/{slug} directory")
    p.add_argument("--month-year", default=None, help="Override the {MonthYear} suffix (default: current MMMYYYY)")
    p.add_argument("--validate-only", action="store_true", help="Skip plan emission; just verify the packet")
    p.add_argument("--allow-republish", action="store_true",
                   help="Skip the already-drafted guard. Use this only when intentionally re-publishing (e.g. you deleted the old Drive Docs).")
    args = p.parse_args(argv)

    packet_dir = Path(args.packet_dir).resolve()
    # Path-confinement: refuse paths outside applications/. Defense-in-depth
    # vs `--packet-dir /etc` or any other unexpected location.
    apps_root = (REPO_ROOT / "applications").resolve()
    try:
        packet_dir.relative_to(apps_root)
    except ValueError:
        print(f"ERROR: packet_dir {packet_dir} is outside {apps_root}; refusing to read.", file=sys.stderr)
        return 2

    ok, problems = validate_packet(packet_dir)
    if not ok:
        print("Packet validation FAILED:", file=sys.stderr)
        for prob in problems:
            print(f"  - {prob}", file=sys.stderr)
        return 1
    print(f"Packet OK: {packet_dir}", file=sys.stderr)
    if args.validate_only:
        return 0

    cfg = _load_local_config()
    month_year = args.month_year or _default_month_year()
    plan = build_plan(packet_dir, month_year, cfg)

    # Already-published guard: prevents duplicate Drive Docs on a second run
    # against the same JD URL when the previous publish left the tracker at
    # status=drafted/submitted/etc.
    if plan["already_published"] and not args.allow_republish:
        existing = plan.get("existing_tracker_entry") or {}
        print(
            f"ERROR: this JD URL is already at status={existing.get('status')!r} in the tracker. "
            f"Re-running publish would create duplicate Drive Docs. "
            f"Pass --allow-republish to override (e.g. after deleting the old Docs).",
            file=sys.stderr,
        )
        return 3

    # Warnings about missing config keys — not fatal because the prompt may
    # still want to run a partial publish. (resume_template_doc_id is deliberately
    # not warned on: it's a legacy key the upload-and-convert flow no longer uses.)
    if not cfg.get("tracker_sheet_id"):
        print("WARN: config['tracker_sheet_id'] not set — sheet append step will be skipped.", file=sys.stderr)

    print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
