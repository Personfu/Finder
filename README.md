# Finder

Finder is an automated remote-job sourcing and resume-tailoring pipeline,
packaged as a [Claude Code](https://claude.com/claude-code) plugin. It takes a
job hunt from **find** → **refine** → **tailor** → **assist** without turning it
into a spreadsheet-and-copy-paste grind, and without ever auto-applying on your
behalf. Every judgment call stays yours.

It runs from the **desktop CLI** (not Cowork — see [Why not Cowork](#why-not-cowork)).

---

## The pipeline, end to end

Finder is four skills that hand off to each other. Resume tailoring is only one
stage; the value is the whole arc.

| Stage | Skill | What happens |
|---|---|---|
| **1. Find** | `/finder:hunt` | Pulls fresh postings from nine job-board sources and scores each one against *your* fit model (`config/job_search_targets.json`). Writes a ranked candidate list into your Obsidian vault. |
| **2. Refine against criteria** | `/finder:walk` | You walk the ranked candidates with the assistant and mark each **Apply / Skip / Defer**. Decisions persist to a local tracker (mirrored to the vault and to a tab-separated row for your Google Sheet). |
| **3. Tailor** | `/finder:apply <jd-url>` | Composes a JD-tailored resume + cover letter + ATS-overlap report. The resume master is built on the fly from your facts (`data/canon.json`) and a tagged bullet library, sliced by the role archetype you pick. |
| **4. Assist the application** | `/finder:publish <packet>` | Renders the packet to DOCX, uploads it to Google Drive (auto-converted to Docs), appends a tracker-sheet row, and links the Doc URLs back into your vault, ready to submit. |

It is **not** an autonomous "apply to a thousand jobs overnight" bot, a
scraper for the big job-board aggregators (ToS gray, anti-bot — deliberate omission), or a
replacement for your tracker sheet (that stays the authoritative ledger; Finder
just keeps it fed).

---

## Two independent knobs: the fit model and the resume canon

Finder has two things you fill in with your own data, and they are independent:

- **The fit model** (`config/job_search_targets.json`) decides *which jobs surface*.
  It ships tuned for a security / infrastructure-engineering search so a first run
  returns real results, but every bucket, comp floor, location tier, and target
  company is a knob you replace. See [Tuning the fit model](#tuning-the-fit-model).
- **The resume canon** (`data/canon.json` + `data/skill_library/`) decides *how
  your resume reads*. The repo ships a filled **example persona — James Bond** — to
  demonstrate the tailoring engine's range across very different target roles. You
  replace it with your own facts.

The shipped example deliberately pairs a broad fictional career (Bond) with a
realistic sourcing config so you can see both engines working before you put your
own data in.

---

## Try it: tailoring one career into very different roles

The example persona shows the payoff of the resume engine: **the same canon,
composed into genuinely different resumes** depending on the target role. This runs
offline against the committed example data — no setup, no network, no vault.

```bash
# A close-protection resume: leads with physical-security + firearms + driving
python scripts/resume_compose.py \
  --canon data/canon.example.json \
  --library data/skill_library/ \
  --role-archetype close-protection

# The SAME person, composed for a luxury-hospitality role: leads with
# connoisseurship + languages, surfaces entirely different bullets
python scripts/resume_compose.py \
  --canon data/canon.example.json \
  --library data/skill_library/ \
  --role-archetype luxury-hospitality
```

Same facts in; a sommelier resume and a bodyguard resume out. That is the
tailoring engine. Swap in your own `canon.json` and skill library and it does the
same for your career.

**See the output without running anything:** the full set of generated example
resumes — one `.md` + `.docx` per archetype, produced by
`scripts/generate_generic_resumes.py` from the example persona — is committed in
[`data/generic_resumes/`](data/generic_resumes/). Regenerate them any time with:

```bash
python scripts/generate_generic_resumes.py --canon data/canon.example.json
```

The example ships seven archetypes (see [docs/resume/README.md](docs/resume/README.md)):
`close-protection` (default), `corporate-security-director`, `field-investigations`,
`diplomatic-liaison`, `intelligence-analyst`, `protective-driver`, and
`luxury-hospitality`. **Archetypes are data, not code** — add your own (`data-scientist`,
`chef`, anything) by editing `config/archetypes.json`; no code change. See
[Adding your own archetype](#adding-your-own-archetype).

---

## Requirements

| Stage | Needs |
|---|---|
| **All stages** | [Claude Code](https://claude.com/claude-code) (this is a plugin) and **Python 3.11+** (the composer uses the stdlib `tomllib`, added in 3.11). The core pipeline is otherwise pure standard library. |
| **hunt / walk / publish** | **[Obsidian](https://obsidian.md) + the Obsidian MCP server.** Ranked hunt lists, the tracker mirror, and packet links are written to your vault through the MCP. |
| **apply → DOCX, and `generate_generic_resumes.py`** | `python-docx` (the only entry in `requirements.txt`). Optional — the markdown workflow works without it; you only need it to render DOCX. |
| **publish only** | **gws-personal** CLI aliases on your PATH (for Google Drive + Sheets). `/finder:publish` refuses to run without them. The other three skills do not need gws. |

```bash
pip install -r requirements.txt   # only needed for the DOCX renderer
```

---

## Plugin install

Finder is a Claude Code plugin. Clone the repo, then install it locally:

```bash
/plugin install file:///path/to/Finder
```

Reload Claude Code after editing `prompts/`, `skills/`, `.claude-plugin/`, or any
`scripts/` file a prompt references, so the plugin picks up the change. Bump the
version in `.claude-plugin/plugin.json` for substantial changes.

The four skills then run from the desktop CLI:

```
/finder:hunt                    # find + score + rank into the vault
/finder:walk                    # decide Apply / Skip / Defer
/finder:apply <jd-url>          # tailor a resume + cover letter packet
/finder:publish <packet-dir>    # render, upload to Drive, append the tracker row
```

### Per-machine setup

`config/local.json` is gitignored. Copy the example and fill it in:

```bash
cp config/local.example.json config/local.json
```

| Key | What | Notes |
|---|---|---|
| `vault_path` | Local Obsidian vault root | Used by the score script for tracker dedup; the vault itself is written via Obsidian MCP. |
| `python_cmd` | Python interpreter | `python` (Windows) / `python3` (mac/Linux). |
| `master_resume_path` | **Legacy fallback** master resume markdown | Only used if `data/canon.json` is missing or `--no-compose` is passed. The primary source is the canon + skill library. |
| `tracker_sheet_id` / `tracker_sheet_tab` | Google Sheet the publish step appends to | Publish-step only. |
| `adzuna_app_id` / `adzuna_app_key` | Adzuna free-tier API key ([developer.adzuna.com](https://developer.adzuna.com), no card) | Optional. If unset, the Adzuna source logs SKIP and the hunt continues on the other sources. |

Without the publish keys, `/finder:publish` skips those steps with warnings — you
can still draft packets and push them by hand.

---

## Why not Cowork

Finder is **CLI-only by design.** The original plan was to run `/finder:hunt` as a
scheduled Cowork task, but Cowork's network proxy blocks the public job-board APIs
the aggregator depends on (Greenhouse, Lever, Ashby, Workable, Adzuna, Workday,
iCIMS, RemoteOK, HN/Algolia), and the "Additional allowed domains" setting is
non-functional today (open Anthropic bugs
[#38984](https://github.com/anthropics/claude-code/issues/38984),
[#30112](https://github.com/anthropics/claude-code/issues/30112),
[#37970](https://github.com/anthropics/claude-code/issues/37970)). Scheduled
fetches die at the proxy. On top of that, hunt/walk/publish rely on the local
Obsidian MCP and gws-personal aliases, which a cloud session doesn't have. So the
hunt runs from the desktop, where the network and your local tools actually work.
This is a known limitation, not a to-do.

---

## OpSec

Finder fires HTTP requests at job boards (during the hunt) and at JD URLs (during
apply). A few habits keep that from looking like reconnaissance or leaking your own
data:

- **Source-IP hygiene.** Running many automated fetches across employer and ATS
  endpoints from your home connection — especially in bursts — can read as
  scraping or recon, and the source is your residential IP aimed at companies you
  may want to interview with. Consider running fetches behind a **VPN**, keep the
  cadence reasonable, and respect each board's terms of service and rate limits.
- **Never probe employer infrastructure to "verify" a source.** Finder enforces a
  hard rule: it never guesses ATS slugs/workspace names or fires a request at a
  company host to check if it works. The `_ALLOWED_HOSTS` allowlist in
  `scripts/hunt_aggregate.py` refuses requests to unlisted hosts at runtime. Add a
  new Workday/iCIMS source only from a URL you copied out of your **own browser** —
  see [Sources](#sources) and `CLAUDE.md`.
- **Keep your data out of git.** `data/canon.json` (your PII), `config/local.json`
  (your IDs/keys), `data/tracker.json` (your applications), and `applications/`
  (your packets) are all gitignored on purpose. Don't `git add -f` them.
- **Credentials.** gws aliases and any Doc/Sheet IDs live in gitignored local
  config. Don't paste tokens or remote URLs into committed files.

---

## Architecture: scripts emit, MCP writes

One data-flow pattern runs through the whole codebase. **Scripts never touch the
vault directly:**

```
script (deterministic work) → tmp/<artifact>.{md,json}
   → calling skill prompt reads tmp → mcp__obsidian__write_note → vault
```

Skills route data; scripts shape it. Reasoning steps (drafting the tailored
resume, writing the cover letter) happen in the live Claude Code session, so there
are no paid API calls — the session itself is the model. Scripts do only
deterministic work (HTTP, parsing, scoring, file I/O).

```
FIND  — /finder:hunt
  scripts/hunt_aggregate.py   fetch 9 sources (host-allowlist gated) → tmp/hunt_raw.json
  Obsidian MCP read           tracker snapshot for dedup
  scripts/hunt_score.py       multi-bucket fit scoring + comp tiering + dedup → ranked md
  Obsidian MCP write          → Job Search/Daily Hunt/YYYY-MM-DD.md

REFINE — /finder:walk
  Obsidian MCP read → walk_session.py parse → you mark Apply/Skip/Defer
  walk_session.py apply       → updated daily-hunt md
  tracker_sync.py append      → data/tracker.json + tmp/tracker.md (+ TSV rows for the Sheet)
  Obsidian MCP write          → updated daily hunt + Tracker.md

TAILOR — /finder:apply <jd-url>
  apply_pipeline.py           fetch JD (Greenhouse/Lever JSON API; HTML fallback)
    → resume_compose.py       canon + skill_library, sliced by --role-archetype → master_resume.md
    → ats_overlap.py          keyword report
    → applications/{slug}/    packet skeleton + manifest
  live session drafts         tailored_resume.md (CAR + anti-AI-tell rules) + cover_letter.md
  tracker_sync.py status      → drafted

ASSIST — /finder:publish <slug>          [needs gws-personal]
  publish_packet.py           validate + emit the gws plan (upload-and-convert)
  md_to_docx.py / cover_letter_to_docx.py   render packet → DOCX
  gws-personal drive          upload DOCX with mimeType=google-apps.document (auto-converts to Docs)
  gws-personal sheets append  → tracker row
  tracker_sync.py status      → drafted, with the new Doc URLs
  Obsidian MCP write          → regenerated Tracker.md

YOU                          open the Docs → final tweak → submit → python scripts/tracker_sync.py status <url> submitted
```

---

## Tuning the fit model

`config/job_search_targets.json` is meant to be edited — it is *your* criteria. The
shipped values are an example tuned for a security / infrastructure-engineering
search.

**The four skill buckets:**

- `proven` (weight 5) — terms tied to what you've demonstrably done at scale. Hits
  here count strongest.
- `adjacent` (weight 3) — wheelhouse but less title-bearing.
- `aspirational` (weight 2) — trajectory targets you're growing toward. Boosts but
  never wins on its own.
- `weak` (weight -1) — areas you're light on. Doesn't disqualify; just dampens fit
  confidence.

**Categorization** (in `hunt_score.py`, first match wins):

1. **Strong Fit** — high `proven` score AND comp at your preferred floor.
2. **Solid Fit** — high `proven + adjacent` AND comp preferred AND not Strong.
3. **Aspirational Fit** — meaningful `aspirational` score AND a real `proven + adjacent` base AND comp preferred.
4. **Low Hanging Fruit** — Strong/Solid skill score AND comp at the acceptable (fully-remote) floor.
5. **Stretch** — any positive score clearing a minimum signal threshold.
6. **Excluded** — hard-exclude regex hit, comp below the acceptable floor, on-site outside allowed metros, or thin signal.

**To tune:**

- A role you'd take that's getting filtered → add the JD's distinctive terms to
  `proven` or `adjacent`, or relax `comp_preferred_floor`.
- Too much noise in Stretch → tighten the thin-signal threshold in `hunt_score.py`
  or add the noise terms to `exclude_titles`.
- Sales / Product / Marketing roles slipping through because they describe products
  in your space → add their title patterns to `exclude_titles`.

Re-run scoring without re-fetching (the aggregate is the slow part; scoring is
instant):

```bash
python scripts/hunt_score.py tmp/hunt_raw.json \
  --targets config/job_search_targets.json \
  --tracker "$VAULT/Job Search/Tracker.md" \
  --today $(date +%Y-%m-%d) > tmp/daily_hunt.md
```

---

## Adding your own archetype

An archetype is a target-role lens: it picks which tagged bullets surface, the
one-line intro at the top of the resume, and the order competency domains appear in
the Skills block. They live in `config/archetypes.json` as data — **no code edit
needed** to add one:

1. Add a block under `archetypes` with an `intro` and a `domain_order`.
2. Add any new `domains` entries it needs (each is a `label` + a keyword-dense
   `skills` line).
3. Tag your `data/skill_library/*.md` bullets with `archetypes=<your-archetype>`.
4. Run `python scripts/resume_compose.py --role-archetype <your-archetype> ...`.

Undefined or partially-defined archetypes still compose: a missing intro falls back
to `intro_default`, a missing `domain_order` falls back to `domain_order_default`.
So you can point `--role-archetype` at a brand-new name and get a sensible resume
immediately, then refine the config.

---

## Sources

The aggregator pulls from nine sources, in three safety categories.

| Source | Mechanism | What to add | Config location |
|---|---|---|---|
| **Greenhouse** | Public boards JSON API, per-company slug | Slug from `job-boards.greenhouse.io/<slug>` | `seed_company_slugs.greenhouse` |
| **Lever** | Public postings JSON API, per-company slug | Slug from `jobs.lever.co/<slug>` | `seed_company_slugs.lever` |
| **Ashby** | Public posting-API job board, per-company slug | Slug from `jobs.ashbyhq.com/<slug>` | `seed_company_slugs.ashby` |
| **Workable** | Public widget JSON, per-customer shortcode | Shortcode from `apply.workable.com/<shortcode>/j/...` | `seed_company_slugs.workable` |
| **Adzuna** | Global aggregator, single host, **API key** + **weekly throttle** | Edit keyword queries / geo radius | `sources.adzuna.queries` |
| **Workday** | Public CXS POST API, per-customer tenant, **strictly allowlisted** | Verified `(host, tenant, workspace)` | `workday_targets[]` + `_ALLOWED_WORKDAY_HOSTS` in code |
| **iCIMS** | Public careers-page HTML scrape, per-customer host, **strictly allowlisted** | Verified `(host, shortcode)` | `icims_targets[]` + `_ALLOWED_ICIMS_HOSTS` in code |
| **RemoteOK** | Global JSON feed, tag-filtered | Edit `sources.remoteok.tag_filter` | `sources.remoteok` |
| **HN Who's Hiring** | Algolia-indexed current-month thread, remote-filtered | (nothing — automatic) | `sources.hn_hiring` |

These adapters are most common among tech employers; the shipped seed lists reflect
that. Replace them with the boards your target employers actually use.

### Adding a Greenhouse / Lever / Ashby / Workable company

Safe public-API sources: find the slug, drop it into the JSON config, done. A
per-slug `[a-z0-9-]+` regex validates shape before any URL is built. If a slug
404s, the source logs `FAILED` and the hunt continues.

### Adding a Workday tenant or iCIMS customer (allowlisted)

**Finder never probes** — that's a hard rule (see `CLAUDE.md` and [OpSec](#opsec)).
Every tenant Finder talks to has its host allowlisted in code, and the matching
config entry carries a `verified_url` showing where the evidence came from.

Discovery: either find a Google-indexed result (`site:myworkdayjobs.com "<Company>"`)
or visit the company's careers page in your **own browser** and copy the URL out of
the address bar. Extract `(host, tenant, workspace)` from that string. Finder does
**not** make a verification request before adding it to config.

To add (a deliberate two-file diff, so it shows up in review):

1. Append the host to `_ALLOWED_WORKDAY_HOSTS` (or `_ALLOWED_ICIMS_HOSTS`) in
   `scripts/hunt_aggregate.py`.
2. Append the matching entry to `workday_targets[]` (or `icims_targets[]`) in
   `config/job_search_targets.json`.

The runtime host check refuses unlisted hosts; per-field regex refuses malformed
entries before any URL is built.

### What Finder deliberately does NOT add

- **Scraping the big job-board aggregators** — ToS gray, anti-bot, IP-ban risk.
- **Search-engine `site:` dorking from the hunt** — same recon-signature problem as
  employer probing, just rerouted through the search engine.
- **Workday/iCIMS hosts discovered by trial-and-error** — every workspace name
  comes from public search results or a URL you pasted.

---

## File map

```
Finder/
├── .claude-plugin/
│   ├── plugin.json              # Plugin manifest (name, version, keywords)
│   └── marketplace.json         # Marketplace catalog entry
├── CLAUDE.md                    # Contributor orientation for Claude Code sessions
├── README.md                    # ← you are here
├── config/
│   ├── local.example.json       # Template — copy to local.json, fill in
│   ├── job_search_targets.json  # Fit model: skill buckets, comp tiers, exclude lists, seed companies
│   ├── archetypes.json          # Archetype definitions: intros, domain order, skills content
│   └── scheduled-tasks.json     # Empty (Cowork-blocked; kept for future use)
├── data/
│   ├── canon.example.json       # Filled example (fictional persona) + schema for canon.json
│   ├── canon.json               # Your resume facts of record (gitignored — PII)
│   ├── skill_library/           # Per-role tagged bullet libraries (committed)
│   │   ├── royal_navy.md
│   │   ├── mi6_field.md
│   │   ├── universal_exports.md
│   │   └── instructor.md
│   └── tracker.json             # Local tracker source of truth (gitignored)
├── docs/
│   └── resume/                  # Resume system docs (canon guide, format analysis, research, strategy)
├── prompts/                     # Skill prompt bodies (hunt, walk, apply, publish)
├── requirements.txt             # python-docx (only for the DOCX renderer)
├── scripts/
│   ├── hunt_aggregate.py        # Pulls 9 sources; hardcoded host allowlist + per-target validation
│   ├── hunt_score.py            # Multi-bucket fit scoring + categorization
│   ├── walk_session.py          # Daily-hunt parser + decision applicator
│   ├── tracker_sync.py          # data/tracker.json + Tracker.md mirror
│   ├── apply_pipeline.py        # JD fetch + composer + ATS analysis + packet skeleton
│   ├── resume_compose.py        # canon + skill_library → composed master (per archetype)
│   ├── _resume_style.py         # Static style rules (tier order, banned verbs/phrases/punctuation, CAR rule)
│   ├── generate_generic_resumes.py  # Convenience: regen every archetype (md + docx)
│   ├── md_to_docx.py            # Resume markdown → styled DOCX (needs python-docx)
│   ├── cover_letter_to_docx.py  # Cover-letter markdown → styled DOCX
│   ├── ats_overlap.py           # Keyword overlap helper (used by apply)
│   └── publish_packet.py        # Packet validation + gws command plan
├── tests/
│   ├── test_resume_compose.py   # Stdlib golden-file test for the composer
│   └── fixtures/                # Synthetic canon + library + archetypes + expected output
├── skills/                      # /finder:hunt, :walk, :apply, :publish (each wraps a prompt)
├── applications/                # Per-Apply packets (gitignored — durable but personal)
└── tmp/                         # Intermediate artifacts (gitignored)
```

---

## Common gotchas

**Vault writes blocked by hook.** All vault writes go through Obsidian MCP. If a
script writes directly to a vault path, the PreToolUse hook (if configured) blocks
it. The fix is always: script writes to `tmp/...md`, the skill prompt reads it and
calls `mcp__obsidian__write_note`.

**`Job Search/Daily Hunt/{today}.md` doesn't exist.** You haven't run `/finder:hunt`
yet today — it's manual, not scheduled. If you *did* run hunt and the file is
missing, check `tmp/hunt_raw.json` mtime: if it matches today, the aggregator ran
but the Obsidian write didn't land.

**Greenhouse company slug returns 404.** Some companies' slugs differ from their
domain. Check the public careers page URL `https://job-boards.greenhouse.io/<slug>/`
and update `seed_company_slugs.greenhouse`.

**Workday/iCIMS source logged `SKIP: host ... not in _ALLOWED_*_HOSTS`.** You added
a target in config without adding the host to the allowlist frozenset in
`scripts/hunt_aggregate.py`. Both files must change in lockstep — friction by
design.

**iCIMS customer returns 0 postings.** Likely the customer reskinned to a
JS-rendered job list, so the server-rendered HTML the scraper depends on no longer
has matching anchor tags. Swap the request URL in that target's config to the RSS
or iframe-embedded variant.

**Adzuna logged `SKIP`.** Either you're inside the weekly throttle window (working
as intended; state is in `data/adzuna_state.json`) or you haven't set
`adzuna_app_id`/`adzuna_app_key` in `config/local.json`. The other sources still run.

**`/finder:publish` says gws aliases not on PATH.** Publish needs the gws-personal
CLI. Source the aliases in your shell profile and re-open the Claude Code session.

**`Tracker.md` in the vault doesn't match `data/tracker.json`.** The mirror is
regenerated after every walk and publish. If they diverge, regenerate:

```bash
python scripts/tracker_sync.py regen
# then read tmp/tracker.md and mcp__obsidian__write_note it back to the vault
```

---

## License

[MIT](LICENSE). Use it, fork it, adapt it for your own job hunt.
