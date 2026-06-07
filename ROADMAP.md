# Finder — Roadmap

Open ideas and refinements, roughly highest-leverage first. None of this is
required for the pipeline to work today; it's a backlog of polish and reach.

## Resume system

- **Auto re-mirror generics to the vault.** `python scripts/generate_generic_resumes.py`
  regenerates the per-archetype markdown/DOCX locally, but copying them into the vault
  is still manual. Either teach the script to route through the Obsidian MCP via a
  helper, or leave the mirror step explicit.
- **Promote-phrasings-back-to-library helper.** The apply prompt asks you to copy any
  good tailored phrasing back into `data/skill_library/<role>.md` by hand. A small
  `scripts/library_promote.py` could automate that once a few cycles show what's worth
  keeping. (Note: TOML frontmatter is read-only in the stdlib, so writing back needs a
  tiny custom emitter.)
- **Workday companion `application-fields.json`.** Workday re-parses uploaded resumes
  into a structured form with a high field-error rate. An optional
  `--out-workday-fields` flag on `resume_compose.py` could emit clean copy-paste values
  for the form. Generate only on request.
- **Fully retire `master_resume_path`.** Currently kept as a legacy fallback for
  `apply_pipeline.py` when `data/canon.json` is missing or `--no-compose` is passed.
  Remove once nothing depends on it.
- **Library bullet ordering.** Bullets are surfaced "as authored"; an optional
  proven-first re-sort within each file would make scan-the-file diagnosis cleaner.

## Hunt / source pipeline

- **iCIMS reskin resilience.** The HTML scrape anchors on a stable
  `/jobs/{id}/{slug}/job` pattern, but a customer that moves to a JS-rendered job list
  returns zero rows. Add an RSS / iframe-embedded fallback per target.
- **More source adapters.** The current nine cover the common public ATSes. BrassRing
  was deliberately skipped (single shared host, fragile hidden-form extraction); revisit
  only with a strong reason. Any new adapter must keep the host-allowlist guard intact.
- **Quarterly re-check of empty boards.** Some seed slugs are valid (200 OK) but return
  zero jobs at a given moment. A periodic re-scan could auto-surface them when they
  repopulate.

## Publish / tracker

- **Cover-letter template polish** and richer tracker-Sheet field mapping as the schema
  evolves.
- **Application-volume metrics** surfaced in a weekly review, if you want the cadence
  signal.

## Contributing

This is a personal-workflow plugin shared as a starting point. The two things you fill
in with your own data are `config/job_search_targets.json` (which jobs surface) and
`data/canon.json` + `data/skill_library/` (how your resume reads). See `README.md` and
`docs/resume/` for the design.
