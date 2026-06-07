# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL — DO NOT PROBE EMPLOYER INFRASTRUCTURE

**No HTTP request is ever fired at a target employer's domain, careers page, ATS tenant, or any candidate-employer-owned infrastructure for the purpose of discovery, verification, or "just checking if it works."** This includes:

- No probing Workday tenants (`*.myworkdayjobs.com`) for workspace names
- No iterating slug or workspace-name variants against any ATS to find what 200s
- No `curl` / `urllib` / `WebFetch` against a company's careers page to "verify it's reachable"
- No HEAD requests, GET requests, POST requests — none — to any host not already in `scripts/hunt_aggregate.py:_ALLOWED_HOSTS`
- Bash one-offs (`python <<'PY' ... urllib.request.urlopen(...) ... PY`) are ALSO banned for this purpose. The Bash transport doesn't sanitize you. The rule is about the act, not the tool.

**Why this is here:** automated requests fired from a job seeker's home network across many employer or ATS endpoints — especially slug/workspace guessing, malformed paths, and bursts from a single source IP — are a textbook reconnaissance signature. Security-vendor SOCs are tuned to flag exactly that pattern, and the source here is a candidate's residential connection aimed at companies they may want to interview with. The downside (looking like an attacker to a future employer) is severe and the upside (saving one manual lookup) is trivial. So the tool never discovers sources by probing.

**The only acceptable way new sources get added:**
1. You visit the company's careers page in your own browser (your choice, your risk on your terms).
2. You copy the resulting URL from the address bar and paste it in.
3. The assistant extracts `(host, workspace)` from the string you handed it. **It does not send a request to verify it before adding it to config.** If it doesn't work when the hunt runs, the hunt fails for that one company. That's fine.
4. The host is added to `_ALLOWED_HOSTS` (or `_ALLOWED_WORKDAY_HOSTS` / `_ALLOWED_ICIMS_HOSTS`) AND to `config/job_search_targets.json` in the same commit. Code review surfaces the deliberate addition.

**The code-level guard** in `scripts/hunt_aggregate.py:_assert_host_allowed` refuses requests to unlisted hosts at runtime, but **this CLAUDE.md rule is the primary defense** — because the guard only protects `_fetch_json` callers, not ad-hoc Bash probes. If you are about to fire an HTTP request and the destination is not on the allowlist, stop. Surface the request to the user in plain English and ask. Do not "just try it."

---

Finder is an automated remote-job sourcing + resume-tailoring pipeline, packaged as a Claude Code plugin (skills wrapping prompts wrapping scripts). It is **CLI-only** — no scheduled tasks, no Cowork. `README.md` has the full architecture diagram, fit-model tuning guide, requirements, and the worked example; read it first if you need context beyond what's below.

## What this does

Four skills, all run from the desktop CLI:

- `/finder:hunt` — pulls postings from Greenhouse/Lever/Ashby/Workable/RemoteOK/HN Who's-Hiring, scores them against your fit model, writes ranked candidates to `Job Search/Daily Hunt/YYYY-MM-DD.md` in the Obsidian vault. Run any morning you want a fresh ranked list.
- `/finder:walk` — walks today's candidates with you, marks Apply/Skip/Defer decisions. Updates `data/tracker.json` (local source of truth) and pushes a regenerated `Tracker.md` to the vault.
- `/finder:apply <jd-url>` — drafts a markdown packet (tailored resume + cover letter + ATS report) at `applications/{Company}-{ShortRole}-{MonthYear}/`.
- `/finder:publish <packet-dir>` — uses gws-personal aliases to render the packet to DOCX, upload it to Google Drive (auto-converted to Docs), append a row to the tracker Sheet, and link Doc URLs back into the vault.

Why no scheduling: the job-board APIs aren't on Anthropic's curated Cowork allowlist (open bugs #38984/#30112/#37970), and "Additional allowed domains" in Cowork settings is a placebo today. Scheduled fetches die at the proxy. So `/finder:hunt` runs from the desktop where the network actually works. See README "Why not Cowork."

## Hard rules

- **Vault writes go through Obsidian MCP only.** Never raw-filesystem against the vault. The PreToolUse hook (if configured) blocks direct writes to anything under `vault_path` anyway.
- **Publish requires gws-personal.** Aliases must be on PATH; `/finder:publish` will refuse to run otherwise. The other three skills don't need gws.
- **Apply/walk decisions persist locally.** `data/tracker.json` is gitignored and machine-local; `Tracker.md` in the vault is just a regenerated mirror. Don't manually edit `Tracker.md` — the next walk/publish overwrites it.
- **Local time everywhere.** "Today" = your local date; filenames are `YYYY-MM-DD`.

## Architecture: scripts emit, MCP writes

The whole codebase follows one data-flow pattern. **Scripts never touch the vault directly:**

```
script (deterministic work) → tmp/<artifact>.{md,json} → calling skill prompt reads tmp → mcp__obsidian__write_note → vault
```

Skills route data; scripts shape it. When extending the pipeline, never call `Write` against a vault path from a script and never have a skill prompt do parsing/scoring inline that a script could do deterministically.

`scripts/`:
- `hunt_aggregate.py` — pulls Greenhouse/Lever/Ashby/Workable/RemoteOK/HN (plus Adzuna/Workday/iCIMS adapters); emits `tmp/hunt_raw.json`. Pure stdlib. Outbound hosts are gated by `_ALLOWED_HOSTS` (see the rule at the top of this file).
- `hunt_score.py` — reads raw aggregate + targets + tracker snapshot; categorizes via the multi-bucket fit model; ranked markdown to stdout.
- `walk_session.py` — parses today's daily-hunt note into JSON; applies decisions back to markdown.
- `tracker_sync.py` — owns `data/tracker.json` (source of truth) and regenerates `tmp/tracker.md` for the vault mirror. Also emits TSV rows for Sheet paste.
- `apply_pipeline.py` — fetches a JD (Greenhouse/Lever JSON API; HTML scrape fallback), invokes `resume_compose.py` to write the master, runs ATS overlap, scaffolds `applications/{slug}/`. Accepts `--role-archetype` (default `close-protection`) and `--no-compose` (legacy fallback).
- `resume_compose.py` — slices `data/canon.json` (facts) and `data/skill_library/*.md` (tagged bullets) by role archetype, sorts by tier, emits a composed master markdown. Loads archetype definitions from `config/archetypes.json`. Auto-scans its own output for banned punctuation (em-dash) and warns to stderr. Stdlib + tomllib. See `docs/resume/strategy.md`.
- `_resume_style.py` — pure data: the STATIC style rules only (tier order, banned verb/phrase/punctuation lists, CAR framework rule, sentence-length rule). Per-archetype intros, domain ordering, and Skills-block content now live in `config/archetypes.json`. Imported by the composer and referenced by `prompts/apply.md`.
- `md_to_docx.py` / `cover_letter_to_docx.py` — render the composer's markdown into a styled DOCX (horizontal rule under name, glyph bullets with hanging indent, blue section headers, right-tab-aligned dates/locations, 0.5" margins, 10.5pt body). Require `python-docx` (only dep in `requirements.txt`).
- `generate_generic_resumes.py` — convenience: runs the composer for every archetype in `config/archetypes.json`, chains `md_to_docx.py` for DOCX. Output to `data/generic_resumes/` (gitignored).
- `ats_overlap.py` — keyword overlap helper used by `apply_pipeline.py`.
- `publish_packet.py` — validates a packet directory and emits the gws command plan (upload-and-convert flow) for the publish skill to execute.

`config/`:
- `job_search_targets.json` — the fit model: comp floors, skill buckets, location tiers, exclude rules, and seed job-board targets. Committed; meant to be edited.
- `archetypes.json` — target-role lenses (intros, Skills-block domain ordering, domain content). Committed data — add your own archetype here, no code edit. See README.

`data/`:
- `canon.json` — gitignored facts of record (personal, employment, education, references, certifications, professional_activities). Your PII lives here. See `data/canon.example.json` for a filled example + schema, `docs/resume/canon.md` for the guide.
- `skill_library/<role_id>.md` — tagged bullet libraries, one file per role_id (matches `canon.employment[].id`). TOML frontmatter + inline `[domain=, tier=, archetypes=]` per bullet. Committed.
- `tracker.json` — gitignored application tracker.

## Common commands

All scripts run with `python`. From the repo root:

```powershell
# Re-run scoring without re-fetching (fast iteration on the fit model)
python scripts/hunt_score.py tmp/hunt_raw.json `
  --targets config/job_search_targets.json `
  --tracker "$VAULT/Job Search/Tracker.md" `
  --today (Get-Date -Format yyyy-MM-dd) > tmp/daily_hunt.md

# Full hunt aggregation (mirrors what /finder:hunt does, minus the vault write)
python scripts/hunt_aggregate.py --targets config/job_search_targets.json --out tmp/hunt_raw.json

# Regenerate Tracker.md from data/tracker.json (after manual edits or merge conflicts)
python scripts/tracker_sync.py regen   # writes tmp/tracker.md — then mcp__obsidian__write_note it

# Bump an application's tracker status
python scripts/tracker_sync.py status <jd-url> submitted

# Compose a tailored master resume in isolation (without running apply_pipeline)
python scripts/resume_compose.py `
  --canon data/canon.example.json `
  --library data/skill_library/ `
  --role-archetype close-protection `
  --out tmp/_resume_smoke.md

# Run the (stdlib-only) test suite
python tests/test_resume_compose.py
```

Stdlib-Python plugin with a tiny test suite under `tests/` (golden-file pattern, no pytest). Iterate by re-running `hunt_score.py` against a saved `tmp/hunt_raw.json` (the aggregate is the slow part; scoring is instant). Re-run `resume_compose.py` against `data/canon.example.json` directly when iterating on bullet selection or skill-block ordering.

## Plugin install

This repo IS a Claude Code plugin. Manifest at `.claude-plugin/plugin.json`, skills at `skills/<name>/SKILL.md` (each wraps a prompt in `prompts/`).

```
/plugin install file:///path/to/Finder
```

Reload Claude Code after edits to `prompts/*.md`, `skills/**/*`, `.claude-plugin/*`, or `scripts/*` referenced by prompts so the plugin picks up changes. Bump `plugin.json:version` for substantial changes.

## Local config

`config/local.json` is gitignored. Copy `config/local.example.json` and set:

- `vault_path`: local Obsidian vault root (used by the score script for tracker dedup; hunt itself reaches the vault via Obsidian MCP)
- `python_cmd`: `python` on this machine
- `master_resume_path`: **legacy fallback** for `apply_pipeline.py` — only used when `data/canon.json` is missing or `--no-compose` is passed. The primary resume source is the canon + skill library.
- `tracker_sheet_id`, `tracker_sheet_tab`: publish-step only (the Sheet a row is appended to). `resume_template_doc_id` is deprecated by the upload-and-convert publish flow and kept only as a legacy key.

Without the publish-step keys, `/finder:publish` skips those steps with warnings — you can still draft packets and push them by hand.

## Fit model

The heart of `hunt_score.py` is a multi-bucket weighted scoring model defined in `config/job_search_targets.json`:

- `proven` (weight 5) — terms tied to work you've demonstrably done at scale
- `adjacent` (weight 3) — wheelhouse but less title-bearing
- `aspirational` (weight 2) — trajectory targets you're growing toward
- `weak` (weight -1) — light areas; dampens but doesn't disqualify

Categorization order in `hunt_score.py` (first match wins): Strong Fit → Solid Fit → Aspirational Fit → Low Hanging Fruit → Stretch → Excluded. Comp tiering (`preferred_floor` / `acceptable_floor` / `acceptable_remote_only`) gates each category — see `README.md` "Tuning the fit model" for the full rule table and tuning recipes.

When a role you'd take is getting filtered or noise is leaking through, edit `config/job_search_targets.json` rather than the script — the buckets, comp floors, and `exclude_titles` regex live there. The shipped values are an editable example tuned for a security / infrastructure-engineering search.
