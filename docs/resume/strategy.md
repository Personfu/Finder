# Resume System Architecture

How Finder turns a set of facts into a tailored resume. The system has one
canonical source of facts, a sliceable library of achievement bullets, and a
deterministic composer that the `/finder:apply` pipeline calls per application.
The research behind these choices lives in [Project FIND](FIND.md); the canon
schema is documented in [canon.md](canon.md).

## Architecture (3 layers)

```
data/canon.json                            ← immutable facts (PII; gitignored)
data/skill_library/*.md (frontmatter)      ← role-tagged bullets (content; committed)
config/archetypes.json                     ← target-role lenses (intros, domain order, skills)
scripts/resume_compose.py                  ← slices canon + library by archetype
    └─→ composed master markdown           ← header + skills + experience + education + activities
```

Canon and the skill library are static data you tend yourself. The composer is the
only moving part: it joins them on the role ID, slices by the target archetype, and
emits a markdown master that the rest of the apply pipeline consumes unchanged.

## Layer 1 — Canon (`data/canon.json`)

A single JSON file (stdlib `json.load`; no PyYAML, per the project's stdlib-only
constraint). It holds the structural facts: `personal`, `education`, `employment`,
`references`, `certifications`, `professional_activities`. It carries PII (phone,
address, references' contact info), so it is gitignored; `data/canon.example.json`
ships the schema filled with a fictional example persona. Full field reference in
[canon.md](canon.md).

## Layer 2 — Skill Library (`data/skill_library/`)

One markdown file per role. TOML frontmatter (Python 3.11+ stdlib `tomllib`) tags the
file with its `role_id`; each bullet is a heading-less block with its own inline tag.

```markdown
+++
role_id = "mi6_field"
+++

[domain=protective-ops, tier=proven, archetypes=close-protection,corporate-security-director,protective-driver]
Protected high-value principals and assets across more than 40 operations on five
continents, never losing a principal while under direct protection.
```

**Vocabulary contract:** `tier` values reuse `proven | adjacent | aspirational` exactly
as defined in `config/job_search_targets.json`. The same lexicon means the fit model
(which scores postings) and the tailoring layer (which surfaces bullets) share a
contract.

**Domain taxonomy is data, not code.** Domains live in `config/archetypes.json` (a
`domains` block, each with a `label` and a keyword-dense `skills` string). Pick a
taxonomy by competency, not by tool or tech stack: a competency taxonomy survives a
career that straddles several domains, where a tool-based one fights you. Each `domain`
tag on a bullet decides which Skills-section line that bullet contributes to.

**Sizing:** keep 6-10 bullets per major role on tap; tailoring picks the top 3-5 per
application.

## Layer 3 — Composer (`scripts/resume_compose.py`)

```
Inputs:
  --canon data/canon.json
  --library data/skill_library/
  --role-archetype close-protection     (one of the archetypes in config/archetypes.json)
  --jd-keywords tmp/<jd>_keywords.txt    (optional — passed from apply_pipeline)
  --max-bullets-per-role 4
  --out applications/<slug>/composed_master.md

Behavior:
  1. Load canon → emit header block + employment skeleton (company/location/title/dates).
  2. For each role, filter skill_library bullets by target archetype, sort by tier
     (proven > adjacent > aspirational), secondary-sort by JD-keyword overlap if supplied,
     take the top N.
  3. Apply a word-count budget; if exceeded, drop aspirational/adjacent bullets first
     (never proven).
  4. Emit the skills block from the union of domains the selected bullets hit.
  5. Emit education + activities + (optional) references.
```

**Archetypes are data, not code.** A role archetype (the target-role lens that picks
which bullets surface, the intro line, and the Skills-block domain order) lives in
`config/archetypes.json`. To add one: add an entry under `archetypes` with an `intro`
and a `domain_order`, add any new `domains` it needs, tag your skill-library bullets
with `archetypes=<name>`, and run the composer with `--role-archetype <name>`. No code
edit required, and an undefined archetype still composes (it falls back to the default
intro and domain order).

## Format spec: the universal-parseable layout

The composer targets one format that parses cleanly across the major ATSes (the
per-ATS research is in [Project FIND](FIND.md)). The principle: do not build per-ATS
variants; build one clean layout and obey a few rules.

| Element | Spec |
|---|---|
| Format | DOCX primary, text-based PDF secondary |
| Layout | Single column, no tables, no text boxes, no Word header/footer regions |
| Contact | Inline under the name (never in a Word header — iCIMS and others ignore those) |
| Section headings | "Experience" / "Professional Experience", "Skills" / "Technical Skills", "Education", "Certifications" — recognized verbatim |
| Dates | `Month YYYY – Month YYYY` or `Month YYYY – Present` |
| Bullets | `-` or `•` only, no decorative glyphs |
| Fonts | Calibri/Arial/Helvetica/Georgia, 10-12pt body |
| Skills | Comma/pipe-separated block under the headline, then embedded again in role bullets |
| Length | Two pages for a senior career, education at the bottom |

One genuine per-ATS wrinkle: **Workday** re-parses an uploaded resume into a structured
form with a high field-error rate, so you hand-correct the form regardless. Don't try to
defeat that parser; optimize the resume for the human reviewer and budget time for the
form.

## Anti-AI style rules (enforced in the apply prompt + composer)

Modern recruiter screens flag AI-generated resumes, so the pipeline enforces a house
style (the detection research is in [Project FIND](FIND.md)).

**Banned verbs/phrases** (in `scripts/_resume_style.py`): leveraged, utilized,
spearheaded, streamlined, enhanced, robust, comprehensive, dynamic, results-driven,
synergy, cutting-edge, seamless, holistic, ecosystem (as metaphor), team player, wealth
of experience.

**Required style:**
- CAR framework (Context → Action → Result), not STAR. Every bullet ends with a number
  or a named system/scale.
- Mix sentence lengths deliberately (4-word fragments alongside 18-word clauses) —
  uniform sentence length is the biggest stylometric tell.
- Concrete, tactile verbs: rebuilt, migrated, decommissioned, traced, rolled back,
  partnered, shipped, instrumented.

The composer scans its own output for banned punctuation (the em-dash, a heavy LLM
tell) and warns to stderr; the apply prompt enforces the verb/phrase rules during
tailoring.
