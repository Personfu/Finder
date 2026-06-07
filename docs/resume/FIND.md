# Project FIND

**FIND** — *Fit-Informed Niche Discovery*. The premise: the way out of the modern
application black hole is not sending more resumes into it. It is surfacing the roles
that genuinely fit and tailoring hard for those.

The mainstream job hunt has drifted into a volume game. Postings are easy to fire an
application at and easier to ignore; resumes get filtered by keyword before a human
ever reads them; tailoring by hand is slow enough that most people stop doing it and
spray a generic version instead. The result is a lot of motion and not much signal on
either side. It is hard to believe that, with the tooling available today, finding work
that fits should still feel like shouting into a void.

FIND is a small bet that there is a better way: fit-first sourcing, ruthless tailoring,
and a human in the loop on every decision that matters. This doc is the research behind
that bet, what the modern hiring funnel actually rewards, and how each finding shaped a
concrete choice in the tool. If you're wondering *why* the resume composer enforces a
particular format or bans particular words, the answer is here.

## TL;DR — five findings that drove the design

- **Build for one universal-parseable spec; do not tailor per-ATS.** Variance across
  Greenhouse/Lever/Ashby/Workable/Workday/iCIMS is small *if* you obey four rules:
  single column, contact inline (not in a header), DOCX primary, standard section
  headings. The remaining ~5% gain isn't worth maintaining six output variants. →
  *Shaped the composer's single fixed format.*
- **Ship DOCX primary, text-based PDF secondary.** DOCX parses universally; PDF is
  roughly equivalent on modern parsers *only* when exported text-based. Workday throws
  ~34% of fields into the wrong column on autofill regardless. → *Shaped `md_to_docx.py`
  as the primary renderer.*
- **Two pages is the senior norm.** With 10+ years and several substantive roles, one
  page actively hurts you. Education goes at the bottom (recruiter eye flow,
  anti-age-bias). → *Shaped the composer's word budget and section ordering.*
- **Avoid LLM-tell verbs** (leveraged, utilized, spearheaded, streamlined, enhanced,
  robust, comprehensive, dynamic). 62% of resumes flagged as AI-generated in 2025 were
  rejected; recruiters run perplexity/stylometry scans, and uniform sentence length is
  the biggest tell. → *Shaped the `BANNED_VERBS`/`BANNED_PHRASES` lists and the
  sentence-variance rule in `_resume_style.py`.*
- **Bucket the bullet library by competency-domain, tagged `proven|adjacent|aspirational`**
  — the same vocabulary as the fit model in `job_search_targets.json`. → *Shaped the
  skill-library tagging scheme so scoring and tailoring share a lexicon.*

## ATS parsing behavior (by system)

### Greenhouse
Accepts PDF/DOC/DOCX/RTF/TXT (100 MB cap). Text-based PDF and DOCX both parse cleanly.
No re-entry form — the resume *is* the candidate record, so formatting hygiene matters.
Recognizes standard headings ("Experience," "Professional Experience," "Education,"
"Skills"); no creative renames.
[Supported formats](https://support.greenhouse.io/hc/en-us/articles/360052218132-Supported-formats-for-resumes-cover-letters-and-other-candidate-uploads)

### Lever
Tag-based + full-text recruiter search; no weighted keyword scoring. A dense
comma/pipe-separated skills line near the top helps you surface in recruiter queries
because Lever ranks proximity. Same format tolerance as Greenhouse.
[How parsers work](https://resumeoptimizerpro.com/blog/how-resume-parsers-actually-work)

### Ashby
Modern parser; handles well-formatted documents cleanly. **Exact-string matching** in
search — a product name and its generic category are not interchangeable. Recruiters
filter on years-of-experience custom fields (don't leave them blank). AI-Assisted
Application Review scores your resume against a rubric the recruiter wrote, so
quantified bullets help.
[Candidate search](https://docs.ashbyhq.com/candidate-search) · [Ashby guide](https://www.sira.now/blog/ashby-ats-guide)

### Workable
Accepts most formats (5 MB cap); image-based PDFs fail entirely. Two-column layouts
fail in 7/8 ATSes tested, Workable among them. Standard headings only; tables get
partially extracted or dropped.
[Supported file types](https://help.workable.com/hc/en-us/articles/115012238108-What-types-of-files-can-be-uploaded-on-the-application-form)

### Workday
The hostile one. Resume upload triggers autofill into a structured form with a **~34%
field error rate** — you *will* hand-correct, and the form data (not the file) is the
canonical record. Right-aligned dates and decorative symbols break field mapping; DOCX
is safer than PDF. Design implication: optimize for the human reviewer and budget 20+
minutes per Workday application for form re-entry. The parser can't be defeated.
[Workday format](https://resumeoptimizerpro.com/blog/workday-resume-format) · [Tailoring for Workday](https://www.resumly.ai/blog/how-to-tailor-resumes-for-workday-ats-specifically)

### iCIMS
Measured parse accuracy: **89% single-col DOCX, 84% single-col PDF, 67% two-col PDF.**
Reads strictly left-to-right and ignores Word header/footer regions entirely — contact
info in a header silently disappears. Decorative symbols get flagged for manual review;
keep bullets to `-` or `•`.
[iCIMS format](https://resumeoptimizerpro.com/blog/icims-resume-format-guide)

## Modern senior-resume best practices

**Section order (2025-2026 consensus, 10+ YOE):** Contact → one-line headline (not a
"summary paragraph" — those read AI-stiff) → Skills block → Experience → Education +
Certs. Education at the bottom keeps the reviewer's eye on recent impact.
([example](https://www.tealhq.com/resume-example/engineering))

**Page budget:** two pages. "Always one page" is entry-level advice. The test: does page
2 reach ~60% fill with substantive content, not padding?
([one vs two pages](https://www.techinterview.org/post/3233474607/engineering-resume-one-page-vs-two/))

**Quantification — use CAR, not STAR.** STAR's "Situation + Task" duplicates context in
resume-length bullets; CAR (Context-Action-Result) compresses cleaner. The framework
matters less than the rule: **every bullet ends with a number or a named system/scale.**
"Protected principals across 40+ operations on five continents" beats "Spearheaded global
protective-security initiatives."
([CAR/STAR/PAR](https://www.vitaeexpress.com/new-blog/2025/4/21/transforming-resume-bullets-with-car-star-and-par-models))

**Anti-AI-tells (2025 recruiter blocklist):** *leveraged, utilized, spearheaded,
streamlined, robust, comprehensive, dynamic, results-driven, synergy, cutting-edge,
seamless, holistic, ecosystem (metaphorical), team player.* 77% of employers screen for
AI-generated content; 62% of flagged resumes are rejected. Detection uses perplexity
scoring and stylometry — mix 4-word fragments with 18-word clauses.
([buzzword detection](https://www.resumly.ai/blog/how-to-detect-ai-generated-buzzwords-filler-phrases) · [how employers detect](https://resumegeni.com/blog/how-employers-detect-ai-generated-resumes-2026))

**Skills section: a dedicated block at the top.** Hard skills 70-80% of it,
comma/pipe-separated (no tables). Greenhouse/Ashby/Lever exact-string match against this
block. Then *also* embed the same skills inside role bullets — ML-based parsers score
contextual substantiation, so a skill named only in the block scores lower than one cited
in both block and bullet.
([skills guide](https://resumeoptimizerpro.com/blog/skills-for-resume))

## Why the bullet library looks the way it does

Practitioners converge on three shapes for resume-as-data:

1. **JSON Resume / YAMLResume** — full resume-as-code with multiple render targets, but a
   flat taxonomy (`skills: [...]`), not bucketed by competency.
   ([yamlresume](https://yamlresume.dev) · [jsonresume](https://jsonresume.org/getting-started))
2. **Markdown master profile + visibility flags** — one master, each bullet tagged with
   which variants it appears in.
   ([resume-tailor-plugin](https://github.com/olegvg/resume-tailor-plugin) · [resume-as-code](https://github.com/zhiweio/resume-as-code))
3. **Bucket-tagged bullet library** — what Finder uses: each bullet carries `domain`
   (competency) + `tier` (proven/adjacent/aspirational) + `archetypes`. Slice = filter by
   archetype, sort by tier.

Finder takes shape 3 with the visibility-flag idea from shape 2: markdown with
frontmatter, one file per role, bucketed by competency-domain (not tech stack, which
fights you whenever one competency straddles several buckets), reusing the
`proven|adjacent|aspirational` vocabulary already in the fit model so the two layers share
a lexicon.

## Sources

- [Greenhouse — supported formats](https://support.greenhouse.io/hc/en-us/articles/360052218132-Supported-formats-for-resumes-cover-letters-and-other-candidate-uploads)
- [Ashby — candidate search](https://docs.ashbyhq.com/candidate-search) · [Sira — Ashby guide](https://www.sira.now/blog/ashby-ats-guide)
- [Workable — supported file types](https://help.workable.com/hc/en-us/articles/115012238108-What-types-of-files-can-be-uploaded-on-the-application-form)
- [Resume Optimizer Pro — how parsers work](https://resumeoptimizerpro.com/blog/how-resume-parsers-actually-work) · [Workday format](https://resumeoptimizerpro.com/blog/workday-resume-format) · [iCIMS format](https://resumeoptimizerpro.com/blog/icims-resume-format-guide) · [skills](https://resumeoptimizerpro.com/blog/skills-for-resume)
- [Resumly — Workday tailoring](https://www.resumly.ai/blog/how-to-tailor-resumes-for-workday-ats-specifically) · [AI buzzword detection](https://www.resumly.ai/blog/how-to-detect-ai-generated-buzzwords-filler-phrases)
- [Resume Geni — how employers detect AI resumes](https://resumegeni.com/blog/how-employers-detect-ai-generated-resumes-2026)
- [Vitae Express — CAR/STAR/PAR](https://www.vitaeexpress.com/new-blog/2025/4/21/transforming-resume-bullets-with-car-star-and-par-models)
- [techinterview.org — one page vs two](https://www.techinterview.org/post/3233474607/engineering-resume-one-page-vs-two/) · [Tealhq — engineering example](https://www.tealhq.com/resume-example/engineering)
- [Jobscan — ATS-friendly templates](https://www.jobscan.co/blog/20-ats-friendly-resume-templates/)
- [YAMLResume](https://yamlresume.dev) · [JSON Resume](https://jsonresume.org/getting-started) · [resume-tailor-plugin](https://github.com/olegvg/resume-tailor-plugin) · [resume-as-code](https://github.com/zhiweio/resume-as-code)
