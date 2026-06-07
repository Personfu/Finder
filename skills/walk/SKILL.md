---
name: walk
description: Interactive CLI walk through today's Finder hunt; surface ranked candidates, walk them with the user, mark each as Apply / Skip / Defer, persist to the Finder tracker (data/tracker.json + Job Search/Tracker.md mirror), and update today's Daily Hunt note inline so dismissed items don't re-surface tomorrow. Use when the user asks "walk the jobs", "go through today's hunt", "let's prioritize the candidates", or invokes /finder:walk directly. CLI-only; relies on interactive prompting.
version: 0.1.0
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - mcp__obsidian__read_note
  - mcp__obsidian__write_note
  - mcp__obsidian__patch_note
---

# Walk Today's Finder

Read the prompt at `${CLAUDE_PLUGIN_ROOT}/prompts/walk.md` and execute as the user's collaborator on this session.

The prompt covers: locating today's Daily Hunt file, parsing it via `python ${CLAUDE_PLUGIN_ROOT}/scripts/walk_session.py parse`, walking candidates with the user interactively (Apply / Skip / Defer per candidate, with notes), then committing decisions via `walk_session.py apply` (which writes `**Decision:**` lines back into the Daily Hunt note) and `tracker_sync.py append` (which updates `data/tracker.json` and the vault `Job Search/Tracker.md` mirror, and prints copy-paste rows for the Google Sheet).

If arguments are passed, the first one may override the date (e.g. `/finder:walk 2026-05-05`): $ARGUMENTS

## Tool scope

- **Bash** required; orchestrates `walk_session.py` and `tracker_sync.py`.
- **Obsidian MCP** for reading the Daily Hunt note and writing back the updated version.
- **No gws**; the publish step (uploading a tailored resume Doc, appending to the Google Sheet) is a separate skill (`/finder:publish`) that runs only on desktop CLI sessions where gws is available. The walk session intentionally keeps the Sheet sync as a paste-this-row hint, not an automated push.
- **No `Tasks/Active.md` writes**; the user promotes things themselves.
- **No email or Discord push**; interactive only.
