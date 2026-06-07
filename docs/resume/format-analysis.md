# Resume Format Analysis: Comparing Resume Versions

A worked example of the analysis you run when you have several versions of your own resume and want
to extract a single canonical format from them. The illustration uses three versions of one
fictional persona's resume (James Bond), spanning an early-career version, a mid-career version,
and a recent achievement-first version. The same comparison applies to your own resume history:
line up your old versions, note what drifted, and pick the modern layout as the canonical spec.

## Header Block: keep it consistent across versions

```
James Bond
London, United Kingdom   |   Mobile: +44 20 7946 0007   |   Email: <email>
```

Single line, pipe-delimited, generous whitespace. The email may vary (a personal address vs. a
brand/work address); the composer defaults to one and keeps the other as a fallback.

## Section Order: watch for drift across versions

| Section | Early-career | Mid-career | Recent (achievement-first) |
|---|---|---|---|
| Summary / Intro | 1 | 1 | 1 |
| Professional Experience | 2 | 2 | 2 |
| **Education** | **3** | **3** | **near-bottom (5)** |
| Skills | 4 (flat sub-buckets) | (omitted) | 3 (named, role-aligned categories) |
| Certifications | 5 | 4 | merged with Education |
| Volunteer / Activities | 6 | 5 | renamed "Activities", last |
| References | 7 | 6 | (not in body; moved to canon) |

**Takeaway:** education migrates from early to late as experience accumulates. For a senior (10+
year) career, education at the bottom is deliberate; it keeps the reviewer's eye on recent impact
and dampens age-signaling. The early-career section order is the entry-level version; the
achievement-first order is the seniors' version. The canonical reusable layout should follow the
achievement-first section order.

## Skills Section Evolution

- **Early-career:** Three flat sub-buckets, e.g. Applications / Concepts / Languages. Generic.
- **Mid-career:** No dedicated skills section (skills implied through bullets).
- **Achievement-first:** Several role-aligned buckets ordered so the most relevant competency
  leads (for the Bond example: Close Protection / Surveillance / Firearms & Tactical / Crisis &
  Negotiation, etc.).

**Takeaway:** the achievement-first categorization is keyword-density-optimized for an ATS scanning
for the role's core terms. For a reusable system the skill buckets need to flex per-role: an
investigations role pulls one slice, a corporate-security-director role pulls another. This is the
skill-bucket library the composer drives off `config/archetypes.json`.

## Density / Page Budget

Illustrative line counts (plaintext extract, excludes header bytes):

| Version | Lines | Body words (est.) | Page budget |
|---|---|---|---|
| Early-career | 88 | ~700 | Fits 2 pages WITH references |
| Mid-career | 68 | ~550 | Fits 2 pages WITH references |
| Achievement-first | 49 | ~900 | 2 pages, no references in body |

The achievement-first version is denser per line: fewer items, more meat per bullet. That's the
modern density target.

## Voice / Tone Observations

- **Early-career:** Functional, descriptive. Reads like a list of duties, not achievements.
- **Mid-career:** Adds AI-tells ("I bring a wealth of experience", "robust solutions") and weak
  framing ("Hired to own and support everything related to...").
- **Achievement-first:** Achievement-first, quantified, specific actors named. For example:
  "Protected high-value principals across more than 40 operations on five continents, never losing
  a principal while under direct protection." This is the modern target voice.

**AI-tell forbidden list** (enforced in `prompts/apply.md`, reinforced here): leverage, passionate,
ecosystem, robust solutions, cutting-edge, wealth of experience, bring a unique perspective.

## Format Carriers: Word/Doc vs Markdown

- **Tab-stop right-alignment of locations/dates:** a Word/Doc feature that will NOT survive a
  markdown master. It has to live in the rendering step (the styled DOCX produced by
  `md_to_docx.py`, or a Google Doc template if you publish that way).
- **Bold company names, italics for titles:** optional in markdown (asterisk syntax), but the
  rendered document is what matters. The DOCX renderer is where the visual format lives.
- **References block with title/company/contact lines:** plain text; easy to carry in markdown.

## Implications for Reusable System

1. **Canonical employment history + dates** lives in a structured file (`data/canon.json`); the
   immutable facts layer.
2. **Skill bucket library** is separate (`data/skill_library/*.md`, one file per role), pulled
   selectively per JD.
3. **Achievement library per role** is what tailoring selects from: every role has ~5–10
   candidate bullets, only 3–5 make any given application.
4. **Visual format** stays in the rendering step; the markdown master just feeds content into it.
5. **Section order is fixed for senior presentation:** Header → Summary → Experience → Skills →
   Education & Certs → Activities → (References on request).

Open questions worth confirming for your own target ATSes:
- Do the systems you hit (Greenhouse, Lever, Ashby, Workable, Workday, iCIMS) parse PDF or DOCX
  better? Does the choice change tactics?
- Is there per-ATS keyword optimization worth doing, or is it broadly universal?
- Best-practice patterns for the skill-bucket library (taxonomy, sizing, refresh cadence).

(These are answered in [FIND.md](FIND.md).)
