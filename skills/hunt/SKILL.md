---
name: hunt
description: Pull job postings from Greenhouse, Lever, RemoteOK, and HN Who's-Hiring; score against the user's profile (multi-bucket fit model); write ranked candidates to Job Search/Daily Hunt/YYYY-MM-DD.md in the Obsidian vault. Markdown-only, no Google Docs/Sheets/Drive writes. Runs on demand from the desktop CLI; not scheduled. Use when the user asks "any new jobs", "run the hunt", "what's open today", or before opening /finder:walk.
version: 0.1.2
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
  - mcp__obsidian__list_directory
  - mcp__obsidian__search_notes
---

# Finder

Read the prompt at `${CLAUDE_PLUGIN_ROOT}/prompts/hunt.md` and execute the instructions inside.

The prompt covers: invoking `python ${CLAUDE_PLUGIN_ROOT}/scripts/hunt_aggregate.py` to pull postings into `tmp/hunt_raw.json`, snapshotting `Job Search/Tracker.md` via `mcp__obsidian__read_note` for dedup, running `python ${CLAUDE_PLUGIN_ROOT}/scripts/hunt_score.py` to categorize them, then writing the markdown output to `Job Search/Daily Hunt/YYYY-MM-DD.md` in the Obsidian vault via `mcp__obsidian__write_note`. If any Strong Fit candidates land, also append a one-line flag to today's daily note `## Flags & Alerts` section.

If arguments are passed after `/finder:hunt`, treat them as a debug flag (e.g. "test", "limit 3"): $ARGUMENTS

## Tool scope

- **Bash** is required for running the two Python scripts.
- **Obsidian MCP** is the only path to vault reads/writes (per global rule). Never raw-filesystem the vault.
- **No Google Docs / Sheets / Drive / Gmail / Calendar tools.** Hunt is awareness-only; gws is for the publish skill.
- **No `Tasks/Active.md` writes.** The user promotes items themselves in the walk.
- **No email/Discord/SMS push.** Vault-only output.
