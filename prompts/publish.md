# Publish: Push a Packet to Google Drive + Tracker Sheet

You're publishing a tailored application packet that `/finder:apply` already drafted. The tailored resume markdown and cover letter markdown are sitting in `applications/<slug>/`. This step renders them to DOCX, uploads each to Google Drive as an auto-converted Google Doc, and appends a row to the Finder Tracking Sheet.

**This skill requires gws-personal aliases on PATH.** If `gws-personal` isn't found when you run the publish plan, stop and tell the user to source the aliases (Bash profile / PowerShell `$PROFILE`) and re-open Claude Code.

## Steps

1. **Resolve the packet directory.** From `$ARGUMENTS`:
   - If it looks like a path that exists (`applications/example-co-5195705008` or absolute), use it directly.
   - If it's a single token (e.g. a company name), grep for matching slugs under `${CLAUDE_PLUGIN_ROOT}/applications/` and pick the most recent. If multiple match, ask the user which one.
   - If `$ARGUMENTS` is empty, list recent packet dirs and ask which to publish.

2. **Validate + get the publish plan.**
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/publish_packet.py <packet-dir>
   ```
   This validates the packet (resume + cover letter aren't still placeholders, has the expected section headings), checks the tracker for an already-published guard, and emits a JSON plan on stdout describing the gws commands to run, the canonical Doc names, and the tracker update args.

   - If validation fails (exit code 1), surface the problems to the user and stop; they probably need to run `/finder:apply` first.
   - If the **already-published guard** fires (exit code 3), the JD URL has been published before. Show the user the existing tracker entry's `resume_doc_url` + `cover_letter_url` and ask whether they want to actually re-publish (which would create *duplicate* Drive Docs) or just open the existing ones. Only re-run `publish_packet.py --allow-republish` if they say yes.

3. **Render and upload the resume Doc.** Render the tailored resume markdown to DOCX, then upload it to Drive as an auto-converted Google Doc:
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/md_to_docx.py <packet-dir>/tailored_resume.md <packet-dir>/tailored_resume.docx
   ```
   Then upload with `mimeType=application/vnd.google-apps.document` so Drive auto-converts the DOCX into a native Google Doc (the resulting Doc matches the DOCX byte-for-byte). Name it `resume_doc_name` from the plan. Use the gws-drive skill or `gws-personal drive upload --help` for the current upload syntax. Capture the new Doc's URL (or ID; derive the URL as `https://docs.google.com/document/d/<id>/edit`). There is no template copy and no manual paste.

4. **Render and upload the cover letter Doc.** Render the cover letter markdown to DOCX, then upload it the same way (auto-convert to a Google Doc):
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/cover_letter_to_docx.py <packet-dir>/cover_letter.md <packet-dir>/cover_letter.docx
   ```
   Upload with `mimeType=application/vnd.google-apps.document`, name it `cover_letter_doc_name` from the plan, and capture the URL.

5. **Bump the tracker entry.**
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tracker_sync.py status \
     "<jd_url>" drafted \
     --resume-doc-url "<resume_url>" \
     --cover-letter-url "<cover_letter_url>" \
     --ats-score <ats_score>
   ```
   This updates `data/tracker.json` and re-renders `tmp/tracker.md`. Read `tmp/tracker.md` and use `mcp__obsidian__write_note path="Job Search/Tracker.md"` (mode=overwrite) to push to the vault.

6. **Append the Sheet row.** From `gws_commands.append_sheet_row`:
   - If `skipped: true` (no `tracker_sheet_id` in config), skip and tell the user.
   - Otherwise: pull the tracker entry (via `tracker_sync.py list` filtered to this URL) to get the canonical company/title/comp/source/notes, build the row by substituting the placeholders in `row_template`, and invoke `gws-personal sheets append` (or `gws-sheets-append` skill) targeting `sheet_id` + `tab`. **gws sheets append takes the most flag-heavy command of the three**; if the example errors, run `gws-personal sheets --help` and adapt before retrying.

7. **Wrap up.** Tell the user:
   - The two new Doc URLs (clickable); these are fully-formatted Google Docs, converted from the rendered DOCX, ready to submit as-is. No copy-paste step.
   - The Sheet row was appended
   - The tracker entry was bumped to `drafted`
   - Next step: download or share the Docs and submit wherever the JD lives.
   - Reminder: when you actually click submit, run `python scripts/tracker_sync.py status <url> submitted` to bump the status.

## Rules

- **CLI-only.** If gws aliases aren't on PATH (`which gws-personal` returns nothing), stop and tell the user; don't try to use the Anthropic API or any other workaround. The whole point of this skill is the gws integration.
- **One packet per invocation.** Don't try to batch publish; too easy for one Drive operation to corrupt across packets if it fails partway.
- **Non-destructive on failure.** If the Drive upload succeeds but Sheet append fails, leave the Drive state alone (the user can retry the Sheet step manually) and tell them exactly what landed and what didn't. Never delete a created Doc on a downstream failure. If `tracker_sync.py status` succeeded but the Obsidian MCP write of `Tracker.md` then failed (vault sync conflict or otherwise), the local + vault are now out of sync; surface this and tell the user they can run `tracker_sync.py regen` and re-push manually. Don't silently retry; they should know.
- **Upload-and-convert, not paste.** The resume and cover letter are rendered to DOCX (`md_to_docx.py`, `cover_letter_to_docx.py`) and uploaded with `mimeType=application/vnd.google-apps.document` so Drive converts each to a native Google Doc that matches the DOCX. There is no template-copy and no manual markdown paste.
- **Vault writes go through Obsidian MCP only.** `tracker_sync.py status` writes the new `Tracker.md` body to `tmp/tracker.md`; read that and `mcp__obsidian__write_note` it back to the vault. Never raw-filesystem the vault.
- **Don't write to `Tasks/Active.md`.** The user promotes things themselves.
