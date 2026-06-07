#!/usr/bin/env python3
"""
Aggregate job postings from public job-board APIs and feeds.

Sources:
- Greenhouse public boards API (per-company slug)
- Lever public postings API (per-company slug)
- Ashby public posting-API job-board (per-company slug)
- Workable public widget API (per-company shortcode)
- Adzuna search API (global aggregator, single endpoint, API-key required)
- Workday CXS public job-board API (per-tenant; STRICTLY allowlisted)
- iCIMS public careers HTML scrape (per-customer shortcode; STRICTLY allowlisted)
- RemoteOK JSON feed
- Hacker News "Who is hiring" current-month thread (via Algolia HN search)

Pure stdlib — no `pip install` required.

Usage:
    python scripts/hunt_aggregate.py [--targets config/job_search_targets.json] [--out tmp/hunt_raw.json]

Output: a JSON file with a list of normalized postings:
    {
        "id": "<source>:<unique-id>",
        "title": "...",
        "company": "...",
        "url": "...",
        "location": "...",
        "comp_text": "...",     # raw, may be empty; hunt_score.py parses
        "posted_at": "YYYY-MM-DD" | null,
        "description": "...",   # plain text, lowercase keyword scoring upstream
        "source": "greenhouse" | "lever" | "ashby" | "workable" | "adzuna" | "workday" | "icims" | "remoteok" | "hn_hiring"
    }

Failures on individual sources/companies are logged to stderr and do NOT abort the run —
we'd rather have partial coverage than no run.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

USER_AGENT = "Finder/0.2 (+https://github.com/your-org/finder) Python-urllib"

# HARDCODED HOST ALLOWLIST — DO NOT EXPAND CASUALLY.
#
# Every HTTP request hunt_aggregate makes goes through _fetch_json, which
# REFUSES to contact any host not in this set. The list is intentionally
# short and concrete (no wildcards beyond the necessary per-tenant Workday
# pattern).
#
# Why this exists, in plain English: an automated agent once ran dozens of
# speculative POST requests across many employers' Workday tenants from a
# residential IP, trying to discover workspace names by trial-and-error.
# Targets included security companies whose detection teams are explicitly
# tuned for that exact pattern, and where the user might one day interview.
# Even one of those requests is unacceptable. This guard exists so the
# mistake is impossible to repeat through this codebase.
#
# Adding a new source REQUIRES editing this list deliberately AND landing it
# in a code review. "Just probe once to see if it works" against a target
# employer is BANNED. See the project docs for the matching rule for ad-hoc
# shell scripts that bypass this function.
_ALLOWED_HOSTS: frozenset[str] = frozenset({
    "boards-api.greenhouse.io",
    "api.lever.co",
    "api.ashbyhq.com",
    "apply.workable.com",
    "api.adzuna.com",
    "remoteok.com",
    "hn.algolia.com",
    "hacker-news.firebaseio.com",
})

# Workday support uses an explicit per-tenant pattern (no wildcards that let
# you probe arbitrary subdomains). Each (host, workspace) must be in the
# config AND every host added below must be one the user has explicitly
# greenlit: either by pasting the URL from their own browser, OR by appearing
# in Google-indexed search results that we mined as evidence of an existing
# public careers page (i.e. the URL is already in the open web index — we are
# not discovering anything new by adding it).
#
# Discovery rule: BEFORE adding a host to this set, the evidence URL goes into
# the corresponding workday_targets[].verified_url field in config so future
# code review can re-check provenance. NEVER add a host because a search
# returned 0 results and we want to "see if it 200s."
_ALLOWED_WORKDAY_HOSTS: frozenset[str] = frozenset({
    "centene.wd5.myworkdayjobs.com",
    "mastercard.wd1.myworkdayjobs.com",
    "abinbev.wd1.myworkdayjobs.com",
    "cigna.wd5.myworkdayjobs.com",
    "ssmh.wd5.myworkdayjobs.com",
    "ameren.wd1.myworkdayjobs.com",
})

# iCIMS hosts are also per-customer (careers-{shortcode}.icims.com). Same rule
# as Workday: each entry below must be one the user has explicitly greenlit,
# with an evidence URL in the matching icims_targets[] config entry. HTML scrape
# only — iCIMS's official XML feed requires OAuth + vendor approval, which is
# not us.
_ALLOWED_ICIMS_HOSTS: frozenset[str] = frozenset({
    "careers-stifel.icims.com",
    "careers-bjc.icims.com",
})


def _host_allowed(host: str) -> bool:
    if not host:
        return False
    host = host.lower()
    if host in _ALLOWED_HOSTS:
        return True
    if host in _ALLOWED_WORKDAY_HOSTS:
        return True
    if host in _ALLOWED_ICIMS_HOSTS:
        return True
    return False


def _assert_host_allowed(url: str) -> None:
    """Raise if this URL's host isn't in the allowlist. Last line of defense
    against speculative probing of employer infrastructure."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not _host_allowed(host):
        raise RuntimeError(
            f"BLOCKED: hunt_aggregate refuses to contact host {host!r}. "
            f"This is the hardcoded employer-probing guard — see comment on "
            f"_ALLOWED_HOSTS in scripts/hunt_aggregate.py. To add a source, "
            f"the URL must come from a user-provided config entry AND the "
            f"host must be explicitly added to the allowlist as a deliberate "
            f"code edit. Never bypass to 'just check' a candidate URL."
        )


# Defense-in-depth against config-driven URL injection: only accept slugs that
# look like real Greenhouse/Lever org slugs. Anything else gets skipped before
# we build a URL from it.
_SLUG_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z", re.IGNORECASE)


def _valid_slug(slug: str) -> bool:
    return bool(slug) and bool(_SLUG_RE.match(slug))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MiB; job-board payloads sit well under this


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-run the host allowlist on every redirect hop. A bare urlopen would
    otherwise follow a 3xx from an allowlisted host to an arbitrary one without
    re-checking — see the _ALLOWED_HOSTS note for why that must never happen."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_host_allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_AllowlistRedirectHandler())


def _fetch_json(url: str, timeout: float = 15.0, body: dict[str, Any] | None = None) -> Any:
    """Fetch a URL and parse the response as JSON. GET by default; if `body`
    is supplied, sends POST with the body JSON-encoded. Raises on HTTP/parse
    errors.

    All requests go through _assert_host_allowed first — see the comment on
    _ALLOWED_HOSTS for why this is here and the incident that motivated it.
    """
    _assert_host_allowed(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with _OPENER.open(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise RuntimeError(f"response from {url} exceeded {_MAX_RESPONSE_BYTES} byte cap")
    return json.loads(raw.decode("utf-8", errors="replace"))


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(s: str | None) -> str:
    """
    Order matters: unescape entities FIRST so &lt;p&gt; becomes <p> before we
    strip tags. Greenhouse's `content` field is double-encoded — without this
    order, raw HTML leaks into the description.
    """
    if not s:
        return ""
    text = html.unescape(s)
    # Some sources double-encode (e.g. &amp;lt;) — unescape twice for safety.
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _iso_date(epoch_or_iso: Any) -> str | None:
    """Best-effort: accept seconds-since-epoch, ms-since-epoch, or ISO string."""
    if epoch_or_iso is None:
        return None
    try:
        if isinstance(epoch_or_iso, (int, float)):
            n = float(epoch_or_iso)
            if n > 1e11:  # likely milliseconds
                n = n / 1000.0
            return _dt.datetime.utcfromtimestamp(n).date().isoformat()
        s = str(epoch_or_iso)
        return _dt.date.fromisoformat(s[:10]).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

def fetch_greenhouse(slug: str, timeout: float) -> list[dict[str, Any]]:
    """
    https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    Returns list of normalized postings.
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    payload = _fetch_json(url, timeout=timeout)
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    for j in jobs:
        try:
            offices = j.get("offices") or []
            location_parts: list[str] = []
            loc = (j.get("location") or {}).get("name") or ""
            if loc:
                location_parts.append(loc)
            for o in offices:
                name = o.get("name")
                if name and name not in location_parts:
                    location_parts.append(name)
            location = " | ".join(location_parts)

            description = _strip_html(j.get("content"))
            comp_text = _extract_comp_signal(description)

            out.append({
                "id": f"greenhouse:{slug}:{j.get('id')}",
                "title": j.get("title", "").strip(),
                "company": (j.get("company_name") or slug).strip(),
                "url": j.get("absolute_url") or "",
                "location": location,
                "comp_text": comp_text,
                "posted_at": _iso_date(j.get("updated_at") or j.get("first_published")),
                "description": description,
                "source": "greenhouse",
            })
        except Exception as e:  # one bad posting shouldn't kill the slug
            print(f"  greenhouse[{slug}] skip job {j.get('id')}: {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

def fetch_lever(slug: str, timeout: float) -> list[dict[str, Any]]:
    """
    https://api.lever.co/v0/postings/{slug}?mode=json
    Returns list of normalized postings.
    """
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    payload = _fetch_json(url, timeout=timeout)
    if not isinstance(payload, list):
        return []
    out: list[dict[str, Any]] = []
    for p in payload:
        try:
            cats = p.get("categories") or {}
            location = cats.get("location") or ""
            description_html = (p.get("description") or "") + " " + " ".join(
                _strip_html((lst.get("text") or "") + " " + " ".join(lst.get("content", []) if isinstance(lst.get("content"), list) else []))
                for lst in p.get("lists") or []
            )
            description = _strip_html(description_html)
            comp_text = _extract_comp_signal(description)
            out.append({
                "id": f"lever:{slug}:{p.get('id')}",
                "title": (p.get("text") or "").strip(),
                "company": slug.replace("-", " ").title(),
                "url": p.get("hostedUrl") or p.get("applyUrl") or "",
                "location": location,
                "comp_text": comp_text,
                "posted_at": _iso_date(p.get("createdAt")),
                "description": description,
                "source": "lever",
            })
        except Exception as e:
            print(f"  lever[{slug}] skip posting {p.get('id')}: {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------

# Slug → preferred display name. .title() works for most ("vanta" → "Vanta",
# "1password" → "1Password") but misformats acronyms / mixed-case brands.
_ASHBY_DISPLAY_NAMES = {
    "openai": "OpenAI",
    "posthog": "PostHog",
    "character": "Character.AI",
    "1password": "1Password",
}


def fetch_ashby(slug: str, timeout: float) -> list[dict[str, Any]]:
    """
    https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

    Response shape: {"jobs": [...], "apiVersion": "..."}.
    Per-job: id, title, location (str), secondaryLocations (list of
    {location, address} dicts), workplaceType, isRemote, isListed,
    compensation (with scrapeableCompensationSalarySummary as the cleanest
    comp string), descriptionPlain, descriptionHtml, jobUrl, applyUrl,
    publishedAt, department, team.

    Unlike Greenhouse, every job has a unique jobUrl (UUID embedded), so
    the shared-URL dedup bug that some companies' Greenhouse boards exhibit
    does NOT apply here.
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    payload = _fetch_json(url, timeout=timeout)
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    company_display = _ASHBY_DISPLAY_NAMES.get(slug.lower()) or slug.replace("-", " ").title()
    out: list[dict[str, Any]] = []
    for j in jobs:
        try:
            # Skip unlisted (admin-flagged hidden) postings. Defensive default
            # = include, since most boards we tested had isListed=true on all.
            if j.get("isListed") is False:
                continue

            # Build location string: primary + secondaries, " | "-separated to
            # match the format hunt_score.py's location_tier() expects.
            loc_parts: list[str] = []
            primary = (j.get("location") or "").strip()
            if primary:
                loc_parts.append(primary)
            for sl in j.get("secondaryLocations") or []:
                if isinstance(sl, dict):
                    sl_loc = (sl.get("location") or "").strip()
                else:
                    sl_loc = str(sl).strip()
                if sl_loc and sl_loc not in loc_parts:
                    loc_parts.append(sl_loc)
            # workplaceType (Hybrid/Remote/OnSite) often empty; mention it as a
            # trailing tag when populated so remote_status() can pick it up.
            wt = j.get("workplaceType")
            if wt and isinstance(wt, str) and wt.lower() not in (primary or "").lower():
                loc_parts.append(wt)
            location = " | ".join(loc_parts)

            # Description: prefer the plain-text field, fall back to stripping HTML.
            description = (j.get("descriptionPlain") or "").strip()
            if not description:
                description = _strip_html(j.get("descriptionHtml"))

            # Comp: use the scrapeable form first (clean "$X - $Y"); fall back
            # to the tier summary which may contain bullets/equity text; finally
            # try to extract from description like other sources.
            c = j.get("compensation") or {}
            comp_text = (
                (c.get("scrapeableCompensationSalarySummary") or "").strip()
                or (c.get("compensationTierSummary") or "").strip()
            )
            if not comp_text:
                comp_text = _extract_comp_signal(description)

            out.append({
                "id": f"ashby:{slug}:{j.get('id')}",
                "title": (j.get("title") or "").strip(),
                "company": company_display,
                "url": j.get("jobUrl") or j.get("applyUrl") or "",
                "location": location,
                "comp_text": comp_text,
                "posted_at": _iso_date(j.get("publishedAt")),
                "description": description,
                "source": "ashby",
            })
        except Exception as e:
            print(f"  ashby[{slug}] skip job {j.get('id')}: {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# Workable
# ---------------------------------------------------------------------------

# Slug → preferred display name. .title() works for simple slugs but mangles
# compound names ("trailofbits" → "Trailofbits", "huggingface" → "Huggingface")
# and slugs that include "-dot-" to escape a dot in the original brand
# ("boostsecurity-dot-i-o" → "Boostsecurity Dot I O").
_WORKABLE_DISPLAY_NAMES = {
    "trailofbits": "Trail of Bits",
    "huggingface": "Hugging Face",
    "threatconnect": "ThreatConnect",
    "boostsecurity-dot-i-o": "BoostSecurity",
    "imachines": "Intuition Machines",
    "cloudlinux-1": "CloudLinux",
    "pathwaycom": "Pathway",
    "dispel": "Dispel",
}


def fetch_workable(slug: str, timeout: float) -> list[dict[str, Any]]:
    """
    https://apply.workable.com/api/v1/widget/accounts/{slug}

    Public widget endpoint, no auth required — designed to be embedded in
    each customer's careers-page widget, so arbitrary callers are expected.

    The v1 widget response shape is NOT formally documented (Workable's docs
    cover v3 SPI / authenticated endpoints). Field names below are inferred
    from the v3 schema (id, title, full_title, shortcode, department,
    location, url/shortlink, salary, created_at) with defensive .get()
    fallbacks. First-run output may need field tweaks once a real response
    is observed.
    """
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    payload = _fetch_json(url, timeout=timeout)
    jobs: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        jobs = payload.get("jobs") or []
    elif isinstance(payload, list):
        jobs = payload
    company_display = _WORKABLE_DISPLAY_NAMES.get(slug.lower()) or slug.replace("-", " ").title()
    out: list[dict[str, Any]] = []
    for j in jobs:
        try:
            # Title: prefer full_title (often includes seniority + team) over title.
            title = (j.get("full_title") or j.get("title") or j.get("name") or "").strip()

            # Location: v3 docs return a {city, region, country} object; widget may
            # surface a plain string instead. Handle both.
            location = ""
            loc_field = j.get("location")
            if isinstance(loc_field, dict):
                parts = [
                    loc_field.get("city") or loc_field.get("location_str"),
                    loc_field.get("region"),
                    loc_field.get("country"),
                ]
                location = ", ".join(p for p in parts if p)
            elif isinstance(loc_field, str):
                location = loc_field
            # Telecommuting flag — append "Remote" so remote_status() can pick it up.
            if j.get("telecommuting") or j.get("remote"):
                location = (location + " | Remote") if location else "Remote"

            # URL: prefer shortlink (clean public URL); fall back to a constructed
            # apply.workable.com path using the shortcode if needed.
            posting_url = (j.get("shortlink") or j.get("application_url") or "").strip()
            shortcode = j.get("shortcode") or j.get("code") or j.get("id")
            if not posting_url and shortcode:
                posting_url = f"https://apply.workable.com/{slug}/j/{shortcode}/"

            # Description may be present as plain text, HTML, or missing entirely
            # from the widget summary endpoint (some Workable boards only expose
            # description on the per-job detail endpoint).
            description = (j.get("description") or j.get("description_plain") or "").strip()
            if not description:
                description = _strip_html(j.get("description_html"))
            elif "<" in description and ">" in description:
                description = _strip_html(description)

            # Salary: v3 returns a {salary_from, salary_to, salary_currency} object
            # on jobs that opt in. Many jobs omit it.
            comp_text = ""
            salary = j.get("salary")
            if isinstance(salary, dict):
                lo = salary.get("salary_from") or salary.get("from")
                hi = salary.get("salary_to") or salary.get("to")
                cur = salary.get("salary_currency") or salary.get("currency") or "USD"
                if lo and hi:
                    comp_text = f"{cur} {lo} - {hi}"
                elif lo:
                    comp_text = f"{cur} {lo}+"
            if not comp_text:
                comp_text = _extract_comp_signal(description)

            out.append({
                "id": f"workable:{slug}:{shortcode}",
                "title": title,
                "company": company_display,
                "url": posting_url,
                "location": location,
                "comp_text": comp_text,
                "posted_at": _iso_date(
                    j.get("created_at") or j.get("published_on") or j.get("published_at")
                ),
                "description": description,
                "source": "workable",
            })
        except Exception as e:
            print(
                f"  workable[{slug}] skip job {j.get('shortcode') or j.get('id')}: {e}",
                file=sys.stderr,
            )
    return out


# ---------------------------------------------------------------------------
# Adzuna
# ---------------------------------------------------------------------------

def _repo_path(path: str) -> Path:
    """Resolve a possibly-relative config/state path against the repo root so
    these files are found regardless of the process's working directory."""
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _read_local_config(path: str = "config/local.json") -> dict[str, Any]:
    """Read gitignored local config (API keys, paths). Returns {} if missing or
    malformed — callers should treat missing keys as "feature disabled" and
    log a SKIP, not crash the whole aggregation run."""
    p = _repo_path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_adzuna_state(path: str = "data/adzuna_state.json") -> dict[str, Any]:
    """State file tracking when Adzuna last ran. Lives in data/ (gitignored)
    so it survives between hunt invocations. Schema:
        {"last_run": "YYYY-MM-DD"}
    Returns {} if missing — caller treats as "never run, proceed."""
    p = _repo_path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_adzuna_state(state: dict[str, Any], path: str = "data/adzuna_state.json") -> None:
    """Persist Adzuna last-run date. Best-effort — a failed write doesn't
    abort the aggregation run, just means next invocation may re-fetch
    earlier than configured."""
    try:
        p = _repo_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"  adzuna: WARN failed to write state file {path}: {e}", file=sys.stderr)


def fetch_adzuna(
    queries: list[dict[str, Any]],
    app_id: str,
    app_key: str,
    country: str,
    timeout: float,
    max_pages: int = 3,
) -> list[dict[str, Any]]:
    """
    https://api.adzuna.com/v1/api/jobs/{country}/search/{page}?...

    Global job-aggregator endpoint — Adzuna indexes postings across thousands
    of employers (including Workday/SuccessFactors/iCIMS-hosted Fortune-500
    roles that our per-ATS adapters can't safely reach by design). Single
    host, no per-employer URL construction, so this expands coverage WITHOUT
    expanding the employer-probing attack surface.

    Free tier: 1000 requests/month, 50 results/page. Each query[i] consumes
    up to max_pages requests; we short-circuit when a page returns <50
    results. With 9 queries × 3 pages worst case = 27 req/day → ~810/month.
    """
    if not app_id or not app_key:
        print(
            "adzuna SKIP: adzuna_app_id/adzuna_app_key not set in config/local.json. "
            "Register a free key at https://developer.adzuna.com and add both fields.",
            file=sys.stderr,
        )
        return []

    out: list[dict[str, Any]] = []
    # Same posting often surfaces under multiple keyword queries — dedup
    # by Adzuna's stable job id before emitting.
    seen_ids: set[str] = set()

    for q in queries:
        what = (q.get("what") or "").strip()
        where = (q.get("where") or "").strip()
        if not what:
            continue
        distance = q.get("distance")
        salary_min = q.get("salary_min")
        max_days_old = q.get("max_days_old", 14)

        for page in range(1, max_pages + 1):
            params: dict[str, Any] = {
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": 50,
                "what": what,
                "max_days_old": max_days_old,
                "content-type": "application/json",
            }
            if where:
                params["where"] = where
            if distance:
                params["distance"] = distance
            if salary_min:
                params["salary_min"] = salary_min

            qs = urllib.parse.urlencode(params)
            url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}?{qs}"

            try:
                payload = _fetch_json(url, timeout=timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
                print(f"  adzuna[{what!r} @ {where!r} p{page}] FAILED: {e}", file=sys.stderr)
                break

            results = (payload or {}).get("results") or []
            if not results:
                break

            for j in results:
                try:
                    job_id = str(j.get("id") or "")
                    if not job_id or job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)

                    company = ((j.get("company") or {}).get("display_name") or "").strip()
                    title = (j.get("title") or "").strip()
                    location = ((j.get("location") or {}).get("display_name") or "").strip()
                    description = (j.get("description") or "").strip()

                    # Comp: Adzuna marks predicted vs reported salaries via the
                    # salary_is_predicted flag ("0" = reported by employer, "1"
                    # = model estimate). Use reported only; predicted is too
                    # noisy and would lie to the fit scorer.
                    comp_text = ""
                    if str(j.get("salary_is_predicted") or "1") == "0":
                        lo = j.get("salary_min")
                        hi = j.get("salary_max")
                        if lo and hi:
                            comp_text = f"${int(lo):,} - ${int(hi):,}"
                        elif lo:
                            comp_text = f"${int(lo):,}+"
                    if not comp_text:
                        comp_text = _extract_comp_signal(description)

                    out.append({
                        "id": f"adzuna:{job_id}",
                        "title": title,
                        "company": company,
                        # redirect_url is Adzuna's tracker — 302s to the real
                        # employer posting. Good enough for our walk flow.
                        "url": j.get("redirect_url") or "",
                        "location": location,
                        "comp_text": comp_text,
                        "posted_at": _iso_date(j.get("created")),
                        "description": description,
                        "source": "adzuna",
                    })
                except Exception as e:
                    print(f"  adzuna skip {j.get('id')}: {e}", file=sys.stderr)

            # Short-circuit pagination when the page is partial — saves
            # quota on narrow queries that don't fill 50 results.
            if len(results) < 50:
                break

    return out


# ---------------------------------------------------------------------------
# Workday
# ---------------------------------------------------------------------------

# Defense against malformed config entries — refuse to send a request unless
# every component matches a sane shape. Subdomain part of the host must be
# lowercase alnum/hyphen; the {tenant}.wd{1-99}.myworkdayjobs.com pattern is
# rigidly enforced; workspace names are alnum + underscore (Workday convention).
_WORKDAY_HOST_RE = re.compile(
    r"\A[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?\.wd[1-9][0-9]?\.myworkdayjobs\.com\Z"
)
_WORKDAY_TENANT_RE = re.compile(r"\A[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?\Z")
_WORKDAY_WORKSPACE_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9_-]{0,62}\Z")


def _parse_workday_posted(s: str) -> str | None:
    """Workday returns postedOn as free text ('Posted Today', 'Posted Yesterday',
    'Posted 5 Days Ago', 'Posted 30+ Days Ago'). Map to an ISO date relative
    to today; return None if shape doesn't match (caller falls back to no date)."""
    if not s:
        return None
    s_low = s.lower().strip()
    today = _dt.date.today()
    if "today" in s_low or "just posted" in s_low:
        return today.isoformat()
    if "yesterday" in s_low:
        return (today - _dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*\+?\s*days?\s*ago", s_low)
    if m:
        try:
            days = int(m.group(1))
            if 0 <= days <= 365:
                return (today - _dt.timedelta(days=days)).isoformat()
        except ValueError:
            pass
    return None


def fetch_workday(
    target: dict[str, Any],
    search_texts: list[str],
    timeout: float,
    locale: str = "en-US",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    POST https://{host}/wday/cxs/{tenant}/{workspace}/jobs

    Body: {"appliedFacets": {}, "limit": N, "offset": 0, "searchText": "..."}

    Workday's CXS public career-site API. Each (host, tenant, workspace)
    triple in workday_targets[] points to one company's tenant. Multiple
    safety gates:

    1. _ALLOWED_WORKDAY_HOSTS membership (enforced by _assert_host_allowed
       in _fetch_json) — refuses requests to unlisted hosts at runtime.
    2. Per-field regex validation here — refuses target entries with
       malformed host/tenant/workspace before constructing a URL.
    3. Title-only output (description="") — we do NOT fetch per-job detail
       pages. The Workday list response carries title + location only; the
       JD body would require N extra requests per company per run. The user
       reads the JD when they click through during the morning walk.
    4. One POST per (target × search_text) — short, sequential, mimics a
       single careers-page visitor.

    Returns title-only postings; description is "" so hunt_score will rely
    on title_signals.strong_titles for these rows.
    """
    host = (target.get("host") or "").lower().strip()
    tenant = (target.get("tenant") or "").lower().strip()
    workspace = (target.get("workspace") or "").strip()
    display_name = target.get("display_name") or tenant

    if not _WORKDAY_HOST_RE.match(host):
        print(f"  workday SKIP: malformed host {host!r}", file=sys.stderr)
        return []
    if not _WORKDAY_TENANT_RE.match(tenant):
        print(f"  workday SKIP: malformed tenant {tenant!r}", file=sys.stderr)
        return []
    if not _WORKDAY_WORKSPACE_RE.match(workspace):
        print(f"  workday SKIP: malformed workspace {workspace!r}", file=sys.stderr)
        return []
    # Belt + suspenders: tenant must be the leading subdomain label of host.
    if not host.startswith(f"{tenant}."):
        print(f"  workday SKIP: tenant {tenant!r} does not prefix host {host!r}", file=sys.stderr)
        return []

    out: list[dict[str, Any]] = []
    # Dedup by externalPath since the same posting can match multiple searchText queries.
    seen_paths: set[str] = set()

    queries = search_texts or [""]
    for search_text in queries:
        url = f"https://{host}/wday/cxs/{tenant}/{workspace}/jobs"
        body = {
            "appliedFacets": {},
            "limit": limit,
            "offset": 0,
            "searchText": search_text,
        }
        try:
            payload = _fetch_json(url, timeout=timeout, body=body)
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            print(f"  workday[{display_name} q={search_text!r}] FAILED: {e}", file=sys.stderr)
            continue

        postings = (payload or {}).get("jobPostings") or []
        for p in postings:
            try:
                external_path = p.get("externalPath") or ""
                if not external_path or external_path in seen_paths:
                    continue
                seen_paths.add(external_path)

                title = (p.get("title") or "").strip()
                location = (p.get("locationsText") or "").strip()
                bullets = p.get("bulletFields") or []
                req_id = p.get("jobReqId") or (bullets[0] if bullets else None) or external_path

                # Canonical URL the user would land on if they clicked from
                # Google — same format the existing indexed URLs use.
                posting_url = f"https://{host}/{locale}/{workspace}{external_path}"

                out.append({
                    "id": f"workday:{tenant}:{req_id}",
                    "title": title,
                    "company": display_name,
                    "url": posting_url,
                    "location": location,
                    "comp_text": "",  # not surfaced in CXS list response
                    "posted_at": _parse_workday_posted(p.get("postedOn") or ""),
                    "description": "",  # title-only; user reads JD on click-through
                    "source": "workday",
                })
            except Exception as e:
                print(f"  workday[{display_name}] skip {p.get('externalPath')}: {e}", file=sys.stderr)

    return out


# ---------------------------------------------------------------------------
# iCIMS
# ---------------------------------------------------------------------------

# Validators for iCIMS config entries — refuse malformed config before
# constructing any URL.
_ICIMS_HOST_RE = re.compile(r"\Acareers-[a-z0-9][a-z0-9-]{0,62}\.icims\.com\Z")
_ICIMS_SHORTCODE_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")

# Matches an iCIMS public job anchor. Examples seen in Google's index:
#   <a href="/jobs/7509/sr-application-security-engineer/job">Sr Application Security Engineer</a>
#   <a href="/jobs/6949/security-engineer-ii---infrastrcture/job?in_iframe=1">Security Engineer II - Infrastructure</a>
# The (id, slug) pair uniquely identifies a posting; everything inside the
# anchor is the title (we strip nested tags). Querystring is dropped.
_ICIMS_ANCHOR_RE = re.compile(
    r'<a[^>]+href="(/jobs/(\d+)/([^/?"]+)/job[^"]*)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _parse_icims_search_html(html_text: str) -> list[dict[str, Any]]:
    """Extract (id, slug, title, url_path) tuples from an iCIMS search results
    HTML page. Intentionally minimal — class names and DOM structure vary
    across iCIMS reskins, so we anchor on the URL pattern (stable across
    every iCIMS install since 2018) rather than CSS selectors that drift.
    Returns deduped postings (by job id)."""
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for m in _ICIMS_ANCHOR_RE.finditer(html_text):
        href, job_id, slug, title_html = m.group(1), m.group(2), m.group(3), m.group(4)
        if job_id in seen_ids:
            continue
        title = _strip_html(title_html)
        if not title:
            continue
        seen_ids.add(job_id)
        out.append({
            "id": job_id,
            "slug": slug,
            "title": title,
            "url_path": href.split("?")[0],
        })
    return out


def _fetch_html(url: str, timeout: float = 20.0) -> str:
    """GET a URL and return the response body as text. Used for HTML-scraping
    sources (iCIMS). All requests go through _assert_host_allowed first —
    same host-allowlist gate as _fetch_json."""
    _assert_host_allowed(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*",
    })
    with _OPENER.open(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise RuntimeError(f"response from {url} exceeded {_MAX_RESPONSE_BYTES} byte cap")
    return raw.decode("utf-8", errors="replace")


def fetch_icims(
    target: dict[str, Any],
    search_texts: list[str],
    timeout: float,
    offer_limit: int = 100,
) -> list[dict[str, Any]]:
    """
    GET https://careers-{shortcode}.icims.com/jobs/search?offerLimit=N[&searchKeyword=X]

    Public careers-page HTML scrape. iCIMS's official XML feed is OAuth-gated
    (vendor-approval process, not us); the public HTML search results are
    server-rendered for human candidates and indexed by Google.

    Safety gates (mirror Workday):
    1. _ALLOWED_ICIMS_HOSTS membership (enforced by _assert_host_allowed)
    2. Per-field regex validation here
    3. Title-only output — list HTML doesn't carry compensation/description;
       the user reads JD on click-through (same compromise as Workday).
    4. One GET per (target × search_text), short and sequential.

    NOTE: if a hunt run returns 0 postings here when there should be some,
    iCIMS may have shifted to JS-rendered listings on that customer's
    careers page. The fix is usually to swap the request URL to the
    customer's RSS feed (where exposed) or the iframe-embedded variant.
    """
    host = (target.get("host") or "").lower().strip()
    shortcode = (target.get("shortcode") or "").lower().strip()
    display_name = target.get("display_name") or shortcode

    if not _ICIMS_HOST_RE.match(host):
        print(f"  icims SKIP: malformed host {host!r}", file=sys.stderr)
        return []
    if not _ICIMS_SHORTCODE_RE.match(shortcode):
        print(f"  icims SKIP: malformed shortcode {shortcode!r}", file=sys.stderr)
        return []
    if host != f"careers-{shortcode}.icims.com":
        print(
            f"  icims SKIP: shortcode {shortcode!r} does not match host {host!r}",
            file=sys.stderr,
        )
        return []

    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    queries = search_texts or [""]
    for search_text in queries:
        params: dict[str, Any] = {"offerLimit": offer_limit}
        if search_text:
            params["searchKeyword"] = search_text
        qs = urllib.parse.urlencode(params)
        url = f"https://{host}/jobs/search?{qs}"

        try:
            html_text = _fetch_html(url, timeout=timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            print(f"  icims[{display_name} q={search_text!r}] FAILED: {e}", file=sys.stderr)
            continue

        for job in _parse_icims_search_html(html_text):
            try:
                if job["id"] in seen_ids:
                    continue
                seen_ids.add(job["id"])
                posting_url = f"https://{host}{job['url_path']}"
                out.append({
                    "id": f"icims:{shortcode}:{job['id']}",
                    "title": job["title"],
                    "company": display_name,
                    "url": posting_url,
                    "location": "",  # not parsed; visible on click-through
                    "comp_text": "",
                    "posted_at": None,
                    "description": "",
                    "source": "icims",
                })
            except Exception as e:
                print(f"  icims[{display_name}] skip {job.get('id')}: {e}", file=sys.stderr)

    return out


# ---------------------------------------------------------------------------
# RemoteOK
# ---------------------------------------------------------------------------

def fetch_remoteok(timeout: float, tag_filter: list[str] | None = None) -> list[dict[str, Any]]:
    """
    https://remoteok.com/api
    First entry is metadata; rest are postings.
    """
    payload = _fetch_json("https://remoteok.com/api", timeout=timeout)
    if not isinstance(payload, list):
        return []
    out: list[dict[str, Any]] = []
    tag_set = {t.lower() for t in (tag_filter or [])}
    for j in payload:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        try:
            tags = [str(t).lower() for t in (j.get("tags") or [])]
            if tag_set and not any(t in tag_set for t in tags):
                continue
            description = _strip_html(j.get("description"))
            salary_min = j.get("salary_min")
            salary_max = j.get("salary_max")
            comp_text = ""
            if salary_min and salary_max:
                comp_text = f"${int(salary_min):,} - ${int(salary_max):,}"
            else:
                comp_text = _extract_comp_signal(description)
            out.append({
                "id": f"remoteok:{j.get('id') or j.get('slug')}",
                "title": str(j.get("position", "")).strip(),
                "company": str(j.get("company", "")).strip(),
                "url": j.get("url") or j.get("apply_url") or "",
                "location": j.get("location") or "Remote",
                "comp_text": comp_text,
                "posted_at": _iso_date(j.get("date") or j.get("epoch")),
                "description": description,
                "source": "remoteok",
            })
        except Exception as e:
            print(f"  remoteok skip {j.get('id')}: {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# Hacker News "Who is hiring"
# ---------------------------------------------------------------------------

def fetch_hn_hiring(timeout: float, remote_only: bool = True) -> list[dict[str, Any]]:
    """
    Find the most recent "Ask HN: Who is hiring?" story via Algolia, fetch its
    comments, and emit each top-level comment as a posting.

    Algolia search:
      https://hn.algolia.com/api/v1/search?tags=story,author_whoishiring&query=hiring
    Comments fetch (HN Firebase):
      https://hacker-news.firebaseio.com/v0/item/<id>.json
    """
    search = _fetch_json(
        "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&query=hiring",
        timeout=timeout,
    )
    hits = (search or {}).get("hits", [])
    if not hits:
        return []
    # Take the newest "Who is hiring?" thread
    story = None
    for h in hits:
        title = (h.get("title") or "").lower()
        if "who is hiring" in title or "who's hiring" in title:
            story = h
            break
    if story is None:
        story = hits[0]

    story_id = story.get("objectID") or story.get("story_id")
    if not story_id:
        return []

    # Use Algolia search to get top-level comments under this story (faster than walking Firebase)
    # max 1000 hits per page; hiring threads typically ~500-900 comments
    comments_payload = _fetch_json(
        f"https://hn.algolia.com/api/v1/search?tags=comment,story_{story_id}&hitsPerPage=1000",
        timeout=timeout,
    )
    comment_hits = (comments_payload or {}).get("hits", [])
    nb_total = (comments_payload or {}).get("nbHits", len(comment_hits))
    if len(comment_hits) >= 1000 and nb_total and nb_total > 1000:
        print(
            f"  hn_hiring: WARNING — story {story_id} has {nb_total} comments but Algolia "
            f"returned the first 1000 only (no pagination implemented)",
            file=sys.stderr,
        )

    out: list[dict[str, Any]] = []
    for c in comment_hits:
        try:
            text = _strip_html(c.get("comment_text") or "")
            if not text or len(text) < 80:
                continue
            if remote_only:
                low = text.lower()
                if "remote" not in low:
                    continue
                if re.search(r"\bonsite only\b|\bon-site only\b|\bin-office only\b", low):
                    continue
            # First line is usually "Company | Role | Location | Stack" — try to extract
            first = text.split(" | ")
            company = first[0][:80].strip() if first else "(HN posting)"
            title = first[1][:120].strip() if len(first) > 1 else "(see description)"
            location = first[2][:80].strip() if len(first) > 2 else "Remote"

            # Try to find URL in the comment — cap length so a hostile comment
            # can't dump a 2KB URL into the daily-hunt note and tracker.json.
            url_match = re.search(r"https?://[^\s)\]]+", text)
            url = (url_match.group(0)[:300] if url_match
                   else f"https://news.ycombinator.com/item?id={c.get('objectID')}")

            out.append({
                "id": f"hn_hiring:{c.get('objectID')}",
                "title": title,
                "company": company,
                "url": url,
                "location": location,
                "comp_text": _extract_comp_signal(text),
                "posted_at": _iso_date(c.get("created_at_i")),
                "description": text,
                "source": "hn_hiring",
            })
        except Exception as e:
            print(f"  hn_hiring skip comment {c.get('objectID')}: {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Catches "$140K", "$140,000", "$140k - $180k", "USD 150,000", "$140-180k", "140k-180k base"
_COMP_RE = re.compile(
    r"(?:USD\s*)?\$?\s*(\d{2,3}[,.]?\d{0,3})\s*(?:k|K|,000)?\s*(?:-|to|–|—)\s*\$?\s*(\d{2,3}[,.]?\d{0,3})\s*(?:k|K|,000)?",
)
_COMP_SINGLE_RE = re.compile(r"\$\s*(\d{2,3}[,.]?\d{0,3})\s*(?:k|K|,000)")


def _extract_comp_signal(text: str) -> str:
    """Return the first comp-looking substring, or '' if none. Cheap heuristic."""
    if not text:
        return ""
    m = _COMP_RE.search(text)
    if m:
        return m.group(0).strip()
    m2 = _COMP_SINGLE_RE.search(text)
    if m2:
        return m2.group(0).strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Aggregate job postings into a single normalized JSON.")
    p.add_argument("--targets", default="config/job_search_targets.json")
    p.add_argument("--out", default="tmp/hunt_raw.json")
    p.add_argument("--only-source", default=None, help="Restrict to one source (greenhouse|lever|ashby|workable|adzuna|workday|icims|remoteok|hn_hiring) — for debugging")
    p.add_argument("--limit-companies", type=int, default=None, help="Limit Greenhouse/Lever/Ashby/Workable to first N slugs — for debugging")
    args = p.parse_args(argv)

    targets_path = Path(args.targets)
    if not targets_path.exists():
        print(f"targets config not found: {targets_path}", file=sys.stderr)
        return 2
    targets = json.loads(targets_path.read_text(encoding="utf-8"))
    sources_cfg = targets.get("sources") or {}
    seed = targets.get("seed_company_slugs") or {}

    aggregated: list[dict[str, Any]] = []
    started = time.time()

    def _enabled(name: str) -> bool:
        if args.only_source and args.only_source != name:
            return False
        return (sources_cfg.get(name) or {}).get("enabled", True)

    if _enabled("greenhouse"):
        slugs = seed.get("greenhouse", [])
        if args.limit_companies:
            slugs = slugs[: args.limit_companies]
        timeout = (sources_cfg.get("greenhouse") or {}).get("timeout_seconds", 15)
        for slug in slugs:
            if not _valid_slug(slug):
                print(f"greenhouse[{slug!r}] SKIP: invalid slug shape (must match [a-z0-9-]+)", file=sys.stderr)
                continue
            try:
                rows = fetch_greenhouse(slug, timeout=timeout)
                aggregated.extend(rows)
                print(f"greenhouse[{slug}] -> {len(rows)} postings", file=sys.stderr)
            except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
                print(f"greenhouse[{slug}] FAILED: {e}", file=sys.stderr)

    if _enabled("lever"):
        slugs = seed.get("lever", [])
        if args.limit_companies:
            slugs = slugs[: args.limit_companies]
        timeout = (sources_cfg.get("lever") or {}).get("timeout_seconds", 15)
        for slug in slugs:
            if not _valid_slug(slug):
                print(f"lever[{slug!r}] SKIP: invalid slug shape (must match [a-z0-9-]+)", file=sys.stderr)
                continue
            try:
                rows = fetch_lever(slug, timeout=timeout)
                aggregated.extend(rows)
                print(f"lever[{slug}] -> {len(rows)} postings", file=sys.stderr)
            except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
                print(f"lever[{slug}] FAILED: {e}", file=sys.stderr)

    if _enabled("ashby"):
        slugs = seed.get("ashby", [])
        if args.limit_companies:
            slugs = slugs[: args.limit_companies]
        timeout = (sources_cfg.get("ashby") or {}).get("timeout_seconds", 30)
        for slug in slugs:
            if not _valid_slug(slug):
                print(f"ashby[{slug!r}] SKIP: invalid slug shape (must match [a-z0-9-]+)", file=sys.stderr)
                continue
            try:
                rows = fetch_ashby(slug, timeout=timeout)
                aggregated.extend(rows)
                print(f"ashby[{slug}] -> {len(rows)} postings", file=sys.stderr)
            except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
                print(f"ashby[{slug}] FAILED: {e}", file=sys.stderr)

    if _enabled("workable"):
        slugs = seed.get("workable", [])
        if args.limit_companies:
            slugs = slugs[: args.limit_companies]
        timeout = (sources_cfg.get("workable") or {}).get("timeout_seconds", 15)
        for slug in slugs:
            if not _valid_slug(slug):
                print(f"workable[{slug!r}] SKIP: invalid slug shape (must match [a-z0-9-]+)", file=sys.stderr)
                continue
            try:
                rows = fetch_workable(slug, timeout=timeout)
                aggregated.extend(rows)
                print(f"workable[{slug}] -> {len(rows)} postings", file=sys.stderr)
            except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
                print(f"workable[{slug}] FAILED: {e}", file=sys.stderr)

    if _enabled("adzuna"):
        cfg = sources_cfg.get("adzuna") or {}
        local = _read_local_config()
        # Free-tier guard: Adzuna allows 1000 reqs/month. At 9 queries × 3 pages
        # daily that's ~810/month — too close. frequency_days throttles to a
        # weekly cadence by default (~108/month, safely under the cap).
        frequency_days = int(cfg.get("frequency_days", 7))
        state = _read_adzuna_state()
        last_run = state.get("last_run")
        today = _dt.date.today()
        skip_reason = None
        if last_run:
            try:
                last_dt = _dt.date.fromisoformat(last_run)
                days_since = (today - last_dt).days
                if days_since < frequency_days:
                    skip_reason = (
                        f"last run {last_run} ({days_since}d ago); "
                        f"next run on {(last_dt + _dt.timedelta(days=frequency_days)).isoformat()} "
                        f"(frequency_days={frequency_days})"
                    )
            except ValueError:
                pass  # malformed state — treat as never run, proceed
        if skip_reason:
            print(f"adzuna SKIP: {skip_reason}", file=sys.stderr)
        else:
            try:
                rows = fetch_adzuna(
                    queries=cfg.get("queries") or [],
                    app_id=local.get("adzuna_app_id", ""),
                    app_key=local.get("adzuna_app_key", ""),
                    country=cfg.get("country", "us"),
                    timeout=cfg.get("timeout_seconds", 20),
                    max_pages=cfg.get("max_pages", 3),
                )
                aggregated.extend(rows)
                print(f"adzuna -> {len(rows)} postings", file=sys.stderr)
                # Only stamp last_run when keys were present (i.e. we actually
                # fired requests). fetch_adzuna returns [] on missing keys
                # without consuming quota — don't pretend we ran.
                if local.get("adzuna_app_id") and local.get("adzuna_app_key"):
                    _write_adzuna_state({"last_run": today.isoformat()})
            except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
                print(f"adzuna FAILED: {e}", file=sys.stderr)

    if _enabled("workday"):
        workday_targets = targets.get("workday_targets") or []
        if args.limit_companies:
            workday_targets = workday_targets[: args.limit_companies]
        cfg = sources_cfg.get("workday") or {}
        search_texts = cfg.get("search_texts") or [""]
        timeout = cfg.get("timeout_seconds", 20)
        limit = cfg.get("limit", 50)
        for t in workday_targets:
            host = (t.get("host") or "").lower()
            if host not in _ALLOWED_WORKDAY_HOSTS:
                print(
                    f"workday[{t.get('display_name', host)!r}] SKIP: host {host!r} not in "
                    f"_ALLOWED_WORKDAY_HOSTS — add to the frozenset in hunt_aggregate.py "
                    f"as a deliberate code edit to enable.",
                    file=sys.stderr,
                )
                continue
            try:
                rows = fetch_workday(t, search_texts=search_texts, timeout=timeout, limit=limit)
                aggregated.extend(rows)
                print(f"workday[{t.get('display_name', host)}] -> {len(rows)} postings", file=sys.stderr)
            except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
                print(f"workday[{t.get('display_name', host)}] FAILED: {e}", file=sys.stderr)

    if _enabled("icims"):
        icims_targets = targets.get("icims_targets") or []
        if args.limit_companies:
            icims_targets = icims_targets[: args.limit_companies]
        cfg = sources_cfg.get("icims") or {}
        search_texts = cfg.get("search_texts") or [""]
        timeout = cfg.get("timeout_seconds", 20)
        offer_limit = cfg.get("offer_limit", 100)
        for t in icims_targets:
            host = (t.get("host") or "").lower()
            if host not in _ALLOWED_ICIMS_HOSTS:
                print(
                    f"icims[{t.get('display_name', host)!r}] SKIP: host {host!r} not in "
                    f"_ALLOWED_ICIMS_HOSTS — add to the frozenset in hunt_aggregate.py "
                    f"as a deliberate code edit to enable.",
                    file=sys.stderr,
                )
                continue
            try:
                rows = fetch_icims(t, search_texts=search_texts, timeout=timeout, offer_limit=offer_limit)
                aggregated.extend(rows)
                print(f"icims[{t.get('display_name', host)}] -> {len(rows)} postings", file=sys.stderr)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                print(f"icims[{t.get('display_name', host)}] FAILED: {e}", file=sys.stderr)

    if _enabled("remoteok"):
        cfg = sources_cfg.get("remoteok") or {}
        try:
            rows = fetch_remoteok(timeout=cfg.get("timeout_seconds", 15), tag_filter=cfg.get("tag_filter"))
            aggregated.extend(rows)
            print(f"remoteok -> {len(rows)} postings", file=sys.stderr)
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            print(f"remoteok FAILED: {e}", file=sys.stderr)

    if _enabled("hn_hiring"):
        cfg = sources_cfg.get("hn_hiring") or {}
        try:
            rows = fetch_hn_hiring(timeout=cfg.get("timeout_seconds", 15), remote_only=cfg.get("remote_only", True))
            aggregated.extend(rows)
            print(f"hn_hiring -> {len(rows)} postings", file=sys.stderr)
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            print(f"hn_hiring FAILED: {e}", file=sys.stderr)

    elapsed = time.time() - started
    print(f"Aggregated {len(aggregated)} postings in {elapsed:.1f}s", file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(aggregated, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path}", file=sys.stderr)

    # Loud signal on zero-postings: a "successful" empty run is almost always a
    # config rot symptom (slugs went stale, all sources failed) and must not
    # look like a clean morning with nothing to apply to.
    if not aggregated:
        print(
            "ERROR: aggregate produced 0 postings. Likely causes: every source "
            "failed, all configured slugs 404'd, or the targets config is empty. "
            "Check stderr above for per-source FAILED lines.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
