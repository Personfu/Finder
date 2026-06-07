#!/usr/bin/env python3
"""
Build an application packet for a single JD URL.

This script does the DETERMINISTIC prep work — fetch the JD, snapshot the
master resume, run the keyword-overlap analysis, and lay down a packet
directory with placeholder slots. The LLM-driven steps (drafting the
tailored resume, drafting the cover letter) happen in the apply skill's
prompt, in the live Claude Code session — no API key, no paid calls.
This matches a Claude Code Max-plan setup (no per-call API billing).

Output packet layout: applications/{slug}/
    jd.html              raw HTML
    jd.txt               cleaned plain text (~15KB cap)
    jd_meta.json         {url, fetched_at, http_status, source_hint}
    master_resume.md     snapshot copy at apply-time (reproducible later)
    ats_keyword_report.json
    manifest.md          packet readme + TODO checklist
    tailored_resume.md   placeholder — apply prompt fills this
    cover_letter.md      placeholder — apply prompt fills this
    sheet_row.txt        TAB-separated row for the Google Sheet (filled
                         after the assistant adds Doc URLs in publish)

Usage:
    python scripts/apply_pipeline.py --jd-url <URL>
                                      [--slug <override>]
                                      [--master-resume <path>]
                                      [--out-dir <dir>]

Slug is auto-derived from the JD URL when not passed:
    https://job-boards.greenhouse.io/anthropic/jobs/5195705008
        -> anthropic-5195705008
    Generic fallback: <hostname-without-tld>-<timestamp>.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import ipaddress
import json
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_BASE = REPO_ROOT / "applications"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Finder/0.1"

# SSRF / DoS guards on outbound JD fetches
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB cap on any single fetch

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SLUG_BAD_RE = re.compile(r"[^a-z0-9]+")


def _load_local_config() -> dict[str, Any]:
    cfg = REPO_ROOT / "config" / "local.json"
    if not cfg.exists():
        return {}
    return json.loads(cfg.read_text(encoding="utf-8"))


def _resolve_default_archetype() -> str:
    """Read default_archetype from config/archetypes.json so an undecorated run
    honors the user's configured default (matching resume_compose.py) rather
    than a hardcoded one. Falls back if the config is missing or silent."""
    cfg = REPO_ROOT / "config" / "archetypes.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return data.get("default_archetype") or "close-protection"
    except (OSError, json.JSONDecodeError):
        return "close-protection"


# ---------------------------------------------------------------------------
# Slug derivation
# ---------------------------------------------------------------------------

def derive_slug(jd_url: str) -> tuple[str, str]:
    """
    Returns (slug, source_hint). Slug is filesystem-safe; source_hint is a
    one-token guess at the source ("greenhouse"/"lever"/"remoteok"/"other").
    """
    parsed = urllib.parse.urlparse(jd_url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")
    parts = path.split("/")

    # Greenhouse: job-boards.greenhouse.io/<company>/jobs/<id>
    if "greenhouse" in host:
        if len(parts) >= 3 and parts[1] in {"jobs", "embed"}:
            company = parts[0]
            jid = parts[-1]
            return f"{_clean(company)}-{_clean(jid)}", "greenhouse"
        return f"greenhouse-{_clean(path) or _ts()}", "greenhouse"

    # Lever: jobs.lever.co/<company>/<uuid>  (and api.lever.co/v0/postings/<company>/<uuid>)
    if "lever.co" in host:
        if len(parts) >= 2:
            company = parts[0] if "lever.co" in host and not parts[0].startswith("v") else parts[2] if len(parts) > 2 else parts[0]
            jid = parts[-1]
            return f"{_clean(company)}-{_clean(jid)[:12]}", "lever"
        return f"lever-{_ts()}", "lever"

    if "remoteok" in host:
        return f"remoteok-{_clean(path) or _ts()}", "remoteok"

    # Generic: short host + timestamp
    short_host = host.split(".")[0] if host else "job"
    return f"{_clean(short_host)}-{_ts()}", "other"


def _clean(s: str) -> str:
    return _SLUG_BAD_RE.sub("-", s.lower()).strip("-") or "x"


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# HTTP fetch + HTML strip
# ---------------------------------------------------------------------------

def fetch_jd(url: str, timeout: float = 30.0) -> tuple[str, int, str]:
    """
    Fetch the JD content. Returns (content, http_status, fetch_method).

    For known job boards (Greenhouse, Lever) we use their JSON APIs to get
    the clean job content — bypassing the rendered application page (form
    widgets, EEO boilerplate, footer) which would otherwise pollute the
    keyword analysis. Falls back to direct HTML fetch for unknown sources.
    """
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "").lower()
    path_parts = (parsed.path or "").strip("/").split("/")

    # Greenhouse: job-boards.greenhouse.io/<company>/jobs/<id>
    # API:        boards-api.greenhouse.io/v1/boards/<company>/jobs/<id>
    if "greenhouse" in host and len(path_parts) >= 3 and path_parts[1] in {"jobs", "embed"}:
        company = path_parts[0]
        jid = path_parts[-1]
        api = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{jid}"
        try:
            content, status = _fetch_text(api, timeout)
            data = json.loads(content)
            html_body = data.get("content") or ""
            html_body = html.unescape(html_body)
            wrapped = (
                f"<h1>{data.get('title', '')}</h1>"
                f"<p><strong>Company:</strong> {data.get('company_name', company)}</p>"
                f"<p><strong>Location:</strong> {(data.get('location') or {}).get('name', '')}</p>"
                f"{html_body}"
            )
            return wrapped, status, "greenhouse-api"
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            print(f"  greenhouse API fetch failed ({e}); falling back to HTML scrape", file=sys.stderr)

    # Lever: jobs.lever.co/<company>/<uuid>
    # API:   api.lever.co/v0/postings/<company>/<uuid>?mode=json
    if "lever.co" in host and len(path_parts) >= 2:
        company = path_parts[0]
        uid = path_parts[-1]
        api = f"https://api.lever.co/v0/postings/{company}/{uid}?mode=json"
        try:
            content, status = _fetch_text(api, timeout)
            data = json.loads(content)
            descr = data.get("description") or ""
            lists_html = " ".join(
                f"<h3>{lst.get('text', '')}</h3>" + (lst.get('content') or '')
                for lst in (data.get("lists") or [])
            )
            wrapped = (
                f"<h1>{data.get('text', '')}</h1>"
                f"<p><strong>Location:</strong> {(data.get('categories') or {}).get('location', '')}</p>"
                f"{descr}{lists_html}"
            )
            return wrapped, status, "lever-api"
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            print(f"  lever API fetch failed ({e}); falling back to HTML scrape", file=sys.stderr)

    # Generic fallback — direct HTML
    content, status = _fetch_text(url, timeout)
    return content, status, "html-scrape"


def _is_safe_url(url: str) -> tuple[bool, str]:
    """
    Return (ok, reason). Rejects non-http(s) schemes and any URL whose
    hostname resolves to a private, loopback, link-local, or unspecified
    address. We never want this script to read file://, fetch from
    localhost services, or hit cloud metadata endpoints.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as e:
        return False, f"unparseable URL: {e}"
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, f"scheme {parsed.scheme!r} not allowed (http/https only)"
    host = parsed.hostname or ""
    if not host:
        return False, "missing hostname"
    # Resolve all addresses; reject if ANY of them are non-public.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return False, f"DNS resolution failed: {e}"
    for info in infos:
        addr_str = info[4][0]
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_reserved or addr.is_multicast:
            return False, f"hostname resolves to non-public address {addr_str}"
    return True, "ok"


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect target against the same scheme + IP allowlist."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, reason = _is_safe_url(newurl)
        if not ok:
            raise urllib.error.URLError(f"refused redirect to {newurl}: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def _fetch_text(url: str, timeout: float) -> tuple[str, int]:
    """
    Fetch URL with SSRF and size guards. Validates scheme + resolved IP
    BEFORE the request, re-validates each redirect hop, caps response at
    _MAX_RESPONSE_BYTES.
    """
    ok, reason = _is_safe_url(url)
    if not ok:
        raise urllib.error.URLError(f"refused fetch of {url}: {reason}")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,application/xhtml+xml,*/*",
    })
    with _OPENER.open(req, timeout=timeout) as resp:
        body = resp.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise urllib.error.URLError(
                f"response from {url} exceeded {_MAX_RESPONSE_BYTES} byte cap"
            )
        encoding = resp.headers.get_content_charset() or "utf-8"
        try:
            text = body.decode(encoding, errors="replace")
        except (LookupError, AttributeError):
            text = body.decode("utf-8", errors="replace")
        return text, resp.status


def html_to_text(s: str, cap: int = 15000) -> str:
    """Strip HTML, decode entities, collapse whitespace, cap to keep prompt-friendly."""
    if not s:
        return ""
    # Drop scripts/styles entirely before stripping tags so their bodies don't leak
    s = re.sub(r"<script\b[^>]*>.*?</script>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<style\b[^>]*>.*?</style>",  " ", s, flags=re.DOTALL | re.IGNORECASE)
    text = html.unescape(s)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > cap:
        text = text[:cap] + "\n\n[…truncated for prompt budget; full HTML in jd.html]"
    return text


# ---------------------------------------------------------------------------
# Manifest rendering
# ---------------------------------------------------------------------------

_MANIFEST_TEMPLATE = """# Application Packet — {slug}

**JD URL:** {url}
**Source hint:** {source_hint}
**Fetched:** {fetched_at}
**Master resume snapshot:** {master_resume_path}

## ATS Keyword Coverage

- **Coverage:** {coverage_pct}% of top {top_n} JD keywords appear in the master resume
- **JD word count:** {jd_word_count}
- **Resume word count:** {resume_word_count}

### Strengths to lead with (present in both)
{present_block}

### Gaps to address (in JD but missing from master resume — see if you can honestly add or reframe)
{missing_block}

## Files in this packet

- `jd.html` — raw HTML at fetch time
- `jd.txt` — cleaned plain text (≤15KB, prompt-friendly)
- `jd_meta.json` — fetch metadata
- `master_resume.md` — snapshot of the master resume at apply time
- `ats_keyword_report.json` — full keyword overlap report
- `tailored_resume.md` — TODO: drafted by `/finder:apply` in-session
- `cover_letter.md` — TODO: drafted by `/finder:apply` in-session
- `sheet_row.txt` — TODO: filled after `/finder:publish` adds Doc URLs

## Next steps

1. Run `/finder:apply` with this packet directory if not already in-session.
   The skill prompt will read `jd.txt` + `master_resume.md` + `ats_keyword_report.json`,
   then draft `tailored_resume.md` (≤2 pages worth, ATS-keyword-tuned, no fabricated experience)
   and `cover_letter.md` (the user's voice, ~300-400 words).
2. Review the drafts. Edit in place if needed.
3. Run `/finder:publish {slug}` (CLI-only — uses gws-personal) to copy a template
   Google Doc, paste in the tailored content, append a row to the tracker Sheet.
"""


def render_manifest(
    slug: str,
    url: str,
    source_hint: str,
    fetched_at: str,
    master_resume_path: Path,
    report: dict[str, Any],
    top_n: int,
) -> str:
    present = report.get("present") or []
    missing = report.get("missing") or []
    present_block = "- " + "\n- ".join(present) if present else "_(none — that's worth flagging in the apply step)_"
    missing_block = "- " + "\n- ".join(missing) if missing else "_(none — strong baseline coverage)_"
    return _MANIFEST_TEMPLATE.format(
        slug=slug,
        url=url,
        source_hint=source_hint,
        fetched_at=fetched_at,
        master_resume_path=str(master_resume_path),
        coverage_pct=report.get("coverage_pct", 0),
        top_n=top_n,
        jd_word_count=report.get("jd_word_count", 0),
        resume_word_count=report.get("resume_word_count", 0),
        present_block=present_block,
        missing_block=missing_block,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_TAILORED_PLACEHOLDER = (
    "<!-- The /finder:apply skill fills this in during the live session.\n"
    "     Until then, this file is intentionally empty.\n"
    "     Constraints: ≤2 pages worth of content, ATS-keyword-tuned (see\n"
    "     ats_keyword_report.json), preserve the user's actual experience\n"
    "     (no fabrication), match the master_resume.md voice/structure. -->\n"
)
_COVER_PLACEHOLDER = (
    "<!-- Placeholder. /finder:apply drafts ~300-400 words in the user's voice:\n"
    "     direct, confident, evidence-led, specifically tied to this role.\n"
    "     No filler, no 'I am writing to apply for' opener. -->\n"
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build an application packet for a single JD URL.")
    p.add_argument("--jd-url", required=True)
    p.add_argument("--slug", default=None)
    p.add_argument("--master-resume", default=None)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--top", type=int, default=40)
    p.add_argument(
        "--role-archetype", default=None,
        help="Target role archetype for the composer (default: the archetypes "
             "config's default_archetype). Shipped examples: close-protection, "
             "corporate-security-director, field-investigations, "
             "diplomatic-liaison, intelligence-analyst, protective-driver, "
             "luxury-hospitality.",
    )
    p.add_argument(
        "--no-compose", action="store_true",
        help="Disable the resume composer and force legacy master_resume_path snapshot mode.",
    )
    args = p.parse_args(argv)

    if args.role_archetype is None:
        args.role_archetype = _resolve_default_archetype()

    cfg = _load_local_config()
    master_resume_arg = args.master_resume or cfg.get("master_resume_path") or ""
    master_resume = Path(master_resume_arg) if master_resume_arg else None

    # Decide composer vs legacy mode. Composer is preferred if canon.json +
    # skill_library/ are both present. --no-compose forces legacy. If neither
    # composer data nor legacy master_resume_path is available, bail.
    canon_path = REPO_ROOT / "data" / "canon.json"
    library_dir = REPO_ROOT / "data" / "skill_library"
    canon_available = canon_path.is_file() and library_dir.is_dir()
    legacy_available = master_resume is not None and master_resume.exists()

    if args.no_compose:
        if not legacy_available:
            print(
                "ERROR: --no-compose requires a legacy master_resume. Set "
                "--master-resume or config/local.json['master_resume_path'].",
                file=sys.stderr,
            )
            return 2
        compose_mode = "legacy"
    elif canon_available:
        compose_mode = "composed"
    elif legacy_available:
        compose_mode = "legacy"
    else:
        print(
            "ERROR: no resume source available. Either populate data/canon.json + "
            "data/skill_library/ (composer mode) OR set --master-resume / "
            "config/local.json['master_resume_path'] (legacy mode).",
            file=sys.stderr,
        )
        return 2

    if args.slug:
        # Sanitize user-supplied slugs the same way derive_slug does — strips
        # path separators and other shenanigans. Defense against
        # `--slug ../../etc/something`.
        cleaned_slug = _clean(args.slug)
        if not cleaned_slug or cleaned_slug == "x":
            print(f"ERROR: --slug {args.slug!r} produced an empty/invalid slug after sanitization", file=sys.stderr)
            return 2
        slug, source_hint = cleaned_slug, "manual"
    else:
        slug, source_hint = derive_slug(args.jd_url)

    out_base = (Path(args.out_dir) if args.out_dir else DEFAULT_OUT_BASE).resolve()
    packet_dir = (out_base / slug).resolve()
    # Hard requirement: packet_dir MUST be inside out_base (defense against
    # any traversal that survives _clean). _clean already strips dots and
    # slashes, but double-checking the resolved path is cheap insurance.
    try:
        packet_dir.relative_to(out_base)
    except ValueError:
        print(f"ERROR: packet_dir {packet_dir} resolves outside out_base {out_base}", file=sys.stderr)
        return 2

    fetched_at = _dt.datetime.now().isoformat(timespec="seconds")

    # 1. Fetch JD (smart-routes to Greenhouse/Lever JSON APIs when possible).
    # Fetch BEFORE creating packet_dir so a failed fetch doesn't leave an
    # empty directory behind that publish_packet.py will later choke on.
    try:
        html_body, status, fetch_method = fetch_jd(args.jd_url)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"ERROR: failed to fetch JD ({e}). Packet not created.", file=sys.stderr)
        return 1
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "jd.html").write_text(html_body, encoding="utf-8")
    jd_text = html_to_text(html_body)
    (packet_dir / "jd.txt").write_text(jd_text, encoding="utf-8")

    # 2. Snapshot master resume into the packet (reproducibility).
    # Composer mode: invoke resume_compose.py to write packet_dir/master_resume.md
    # directly. On any failure, fall back to legacy master_resume_path snapshot
    # if available; otherwise abort.
    composer_script = Path(__file__).resolve().parent / "resume_compose.py"
    if compose_mode == "composed":
        try:
            cproc = subprocess.run(
                [sys.executable, str(composer_script),
                 "--canon", str(canon_path),
                 "--library", str(library_dir),
                 "--role-archetype", args.role_archetype,
                 "--out", str(packet_dir / "master_resume.md")],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            cproc_failed = cproc.returncode != 0
            cproc_err = cproc.stderr.strip()
        except subprocess.TimeoutExpired:
            cproc_failed = True
            cproc_err = "timeout after 30s"
        if cproc_failed:
            print(f"WARN: resume_compose.py failed: {cproc_err}", file=sys.stderr)
            if legacy_available:
                print("  falling back to legacy master_resume_path", file=sys.stderr)
                (packet_dir / "master_resume.md").write_text(
                    master_resume.read_text(encoding="utf-8"), encoding="utf-8",
                )
                compose_mode = "legacy"
            else:
                return 1
    elif compose_mode == "legacy":
        (packet_dir / "master_resume.md").write_text(
            master_resume.read_text(encoding="utf-8"), encoding="utf-8",
        )

    (packet_dir / "jd_meta.json").write_text(
        json.dumps({
            "url": args.jd_url,
            "fetched_at": fetched_at,
            "http_status": status,
            "source_hint": source_hint,
            "fetch_method": fetch_method,
            "slug": slug,
            "compose_mode": compose_mode,
            "role_archetype": args.role_archetype if compose_mode == "composed" else None,
        }, indent=2),
        encoding="utf-8",
    )

    # 3. Run ATS overlap (subprocess so the script can be reused standalone too)
    overlap_script = Path(__file__).resolve().parent / "ats_overlap.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(overlap_script), str(packet_dir / "jd.txt"),
             str(packet_dir / "master_resume.md"), "--top", str(args.top)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: ats_overlap.py timed out", file=sys.stderr)
        return 1
    if proc.returncode != 0:
        print(f"ERROR: ats_overlap.py failed: {proc.stderr.strip()}", file=sys.stderr)
        return 1
    report = json.loads(proc.stdout)
    (packet_dir / "ats_keyword_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    # 4. Lay down placeholders + manifest
    (packet_dir / "tailored_resume.md").write_text(_TAILORED_PLACEHOLDER, encoding="utf-8")
    (packet_dir / "cover_letter.md").write_text(_COVER_PLACEHOLDER, encoding="utf-8")
    # Manifest's master_resume_path field reflects either the composed source
    # (when in composer mode) or the legacy file path.
    if compose_mode == "composed":
        manifest_master = f"composed from data/canon.json + data/skill_library/ (archetype={args.role_archetype})"
    else:
        manifest_master = str(master_resume) if master_resume else "(unknown)"
    manifest = render_manifest(
        slug=slug, url=args.jd_url, source_hint=source_hint,
        fetched_at=fetched_at, master_resume_path=manifest_master,
        report=report, top_n=args.top,
    )
    (packet_dir / "manifest.md").write_text(manifest, encoding="utf-8")

    print(f"Packet ready: {packet_dir}", file=sys.stderr)
    print(f"  Coverage: {report['coverage_pct']}% of top {args.top} JD keywords present in master resume", file=sys.stderr)
    print(f"  Strengths: {len(report['present'])} present, {len(report['missing'])} gaps", file=sys.stderr)

    # Stdout = packet path for the skill to capture
    print(str(packet_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
