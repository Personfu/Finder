---
name: publish
description: Publish a tailored application packet from /finder:apply to Google Drive (render resume + cover letter to DOCX, upload each as an auto-converted Google Doc) and append a row to the Finder Tracking Sheet. Requires gws-personal CLI aliases on PATH. Updates the Finder tracker entry to status=drafted with the new Doc URLs. Use when the user says "publish the packet for X", "push the apply to Drive", "create the docs for X", or invokes /finder:publish directly with a packet directory or company name.
version: 0.1.0
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - mcp__obsidian__write_note
---

# Publish

Read the prompt at `${CLAUDE_PLUGIN_ROOT}/prompts/publish.md` and execute as the user's collaborator on this CLI session.

The prompt covers: validating the packet via `python ${CLAUDE_PLUGIN_ROOT}/scripts/publish_packet.py <packet>`, then (1) rendering the tailored resume to DOCX via `scripts/md_to_docx.py` and uploading it to Drive with `mimeType=application/vnd.google-apps.document` so it auto-converts to a Google Doc, (2) rendering and uploading the cover letter the same way via `scripts/cover_letter_to_docx.py`, (3) appending a row to the tracker Sheet, (4) bumping the tracker entry to `status=drafted` with the new Doc URLs, (5) pushing the regenerated `Tracker.md` to the vault via `mcp__obsidian__write_note`. The uploaded Docs match the rendered DOCX, so there is no template-copy and no manual markdown paste.

If arguments are passed, the first one is the packet dir or a slug under `applications/`: $ARGUMENTS

## Tool scope

- **gws-personal aliases REQUIRED.** This skill orchestrates `gws-personal drive`, `gws-personal docs`, and `gws-personal sheets`. If those aren't on PATH (sourced from your shell profile), the publish plan will fail at the first command and you should stop and surface the missing-aliases error to the user.
- **Bash**: runs `publish_packet.py`, `tracker_sync.py`, `md_to_docx.py`, `cover_letter_to_docx.py`, and the gws-personal commands.
- **Read/Write/Edit**: packet files in `applications/<slug>/` (NOT the vault).
- **Obsidian MCP write**: pushes the regenerated `Tracker.md` from `tmp/` to the vault.
- **No `Tasks/Active.md` modifications.**
