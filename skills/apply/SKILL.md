---
name: apply
description: Build a tailored application packet for a single JD URL; fetches the JD, snapshots the master resume, runs deterministic ATS keyword overlap, then drafts a tailored resume.md (≤2 pages, keyword-tuned, no fabrication) and cover_letter.md (the user's voice, 300-400 words). Outputs a packet at applications/{slug}/ ready to publish via /finder:publish. Use when the user marks a candidate as Apply during /finder:walk, or asks to "tailor a resume for", "draft an application for", or invokes /finder:apply directly with a JD URL.
version: 0.1.0
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - WebFetch
  - mcp__obsidian__read_note
  - mcp__obsidian__write_note
---

# Apply

Read the prompt at `${CLAUDE_PLUGIN_ROOT}/prompts/apply.md` and execute as the user's collaborator on this session.

The prompt covers: invoking `python ${CLAUDE_PLUGIN_ROOT}/scripts/apply_pipeline.py --jd-url <URL>` to build a packet skeleton, reading the resulting `manifest.md` + `jd.txt` + `master_resume.md` + `ats_keyword_report.json`, then drafting the tailored resume and cover letter directly in the session (no API call; your own context is the LLM here, designed for a Claude Code Max plan) and writing them to the packet directory.

If arguments are passed, the first one is the JD URL: $ARGUMENTS

## Tool scope

- **Bash**: runs `apply_pipeline.py` and `tracker_sync.py status`.
- **Read/Write/Edit**: reads packet files (in `applications/<slug>/`, NOT in the vault) and writes the drafted `tailored_resume.md` + `cover_letter.md` back into the packet directory.
- **WebFetch**: fallback when `urllib` in `apply_pipeline.py` can't reach a JD (anti-bot pages, JS-rendered content). For Greenhouse/Lever the script's direct fetch is usually fine.
- **Obsidian MCP read**: for cross-checking facts against `Job Search/Overview.md` (the user's verified work history). Never write to the vault from this skill; the publish step handles vault/Doc/Sheet writes.
- **No gws**: the Google Doc creation + Sheet append happen in `/finder:publish`, not here. This skill produces markdown only.
- **No `Tasks/Active.md` modifications.**
