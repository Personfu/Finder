#!/usr/bin/env python3
"""Compose a tailored resume master from canon + skill_library.

Replaces the static `master_resume_path` snapshot step in apply_pipeline.py.
Reads two inputs:

  data/canon.json           — immutable facts (personal, employment, education,
                              references, certifications, professional_activities)
  data/skill_library/*.md   — one file per role; TOML frontmatter (delimited by
                              +++) declares role_id, plus bullets each preceded
                              by an inline tag line:
                                [domain=X, tier=proven|adjacent|aspirational,
                                 archetypes=A,B,C]

Selects bullets by `--role-archetype`, sorts by tier (proven > adjacent >
aspirational), optionally re-ranks by `--jd-keywords` overlap density, takes
top N per role, applies a body word-count budget, and emits a single composed
markdown file that the apply pipeline picks up unchanged.

The composer DOES NOT rewrite bullet voice — that's the apply prompt's job.
This script just picks the right ones and lays them out.

Usage:
    python scripts/resume_compose.py
        --canon data/canon.json
        --library data/skill_library/
        --role-archetype close-protection
        [--jd-keywords tmp/keywords.txt]
        [--max-bullets-per-role 4]
        [--body-word-budget 700]
        [--include-references]
        [--out tmp/composed_master.md]   (omit for stdout)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent

# Make sibling modules importable when invoked from outside scripts/
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _resume_style import (  # noqa: E402
    BANNED_PUNCTUATION,
    TIER_ORDER,
)

# Archetype definitions (intros, domain ordering, Skills-block content) are
# DATA, not code — they live in config/archetypes.json so anyone can add a
# target-role lens without touching Python. See load_archetype_style().
DEFAULT_ARCHETYPES_CONFIG = REPO_ROOT / "config" / "archetypes.json"


def load_archetype_style(config_path: Path) -> dict[str, Any]:
    """Read config/archetypes.json and derive the lookups the composer needs.

    Returns a dict with:
      skills_by_domain    {domain_id: (label, skills_line)}
      domain_order_default [domain_id, ...]   (fallback Skills ordering)
      arch_domain_order   {archetype: [domain_id, ...]}  (per-archetype override)
      intros              {archetype: intro_text}
      intro_default       str   (fallback intro for undefined archetypes)
      default_archetype   str   (used when --role-archetype is omitted)

    The graceful-fallback contract (unknown archetype -> intro_default +
    domain_order_default) is preserved by emit_resume's .get(...) calls.
    """
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"failed to load archetypes config {config_path}: {e}") from e

    domains = data.get("domains", {})
    skills_by_domain: dict[str, tuple[str, str]] = {}
    for dom_id, spec in domains.items():
        if isinstance(spec, dict) and "label" in spec and "skills" in spec:
            skills_by_domain[dom_id] = (spec["label"], spec["skills"])

    archetypes = data.get("archetypes", {})
    intros = {a: v["intro"] for a, v in archetypes.items() if isinstance(v, dict) and v.get("intro")}
    arch_domain_order = {
        a: v["domain_order"]
        for a, v in archetypes.items()
        if isinstance(v, dict) and v.get("domain_order")
    }

    return {
        "skills_by_domain": skills_by_domain,
        "domain_order_default": data.get("domain_order_default", []),
        "arch_domain_order": arch_domain_order,
        "intros": intros,
        "intro_default": data.get("intro_default", ""),
        "default_archetype": data.get("default_archetype", ""),
    }


# Locates tag-like lines [<fields>] sitting on their own line.
# Field parsing is order-independent (see _parse_tag_content) so library authors
# can write [tier=proven, domain=endpoint, archetypes=...] without silent drops.
_TAG_LINE_RE = re.compile(r"^\[([^\]]+)\]\s*$", re.MULTILINE)

# Recognizes the archetypes= field with a value that runs to the end of the tag
# (archetypes' value can contain commas — its sibling archetype names).
# Domain and tier values cannot contain commas.
_ARCHETYPES_FIELD_RE = re.compile(r"\barchetypes\s*=\s*(.+)$")

_MONTHS = [
    "",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _resolve_inside(path: Path, allowed_parent: Path) -> Path:
    """Resolve `path` and verify it lives under `allowed_parent`. Defense
    against `../../etc/something` traversal in user-supplied CLI args.
    """
    resolved = path.resolve()
    parent_resolved = allowed_parent.resolve()
    try:
        resolved.relative_to(parent_resolved)
    except ValueError:
        raise ValueError(
            f"path {resolved} resolves outside allowed parent {parent_resolved}"
        )
    return resolved


# ---------------------------------------------------------------------------
# Library parsing
# ---------------------------------------------------------------------------

def _parse_tag_content(tag_content: str) -> dict[str, str] | None:
    """Parse the inside of a bullet tag — `domain=X, tier=Y, archetypes=A,B,C`.

    Order-independent for domain/tier; archetypes MUST be the last field
    (its value can contain commas — those are archetype-list separators).
    Returns dict with keys domain/tier/archetypes, or None if any required
    field is missing.
    """
    am = _ARCHETYPES_FIELD_RE.search(tag_content)
    if not am:
        return None
    archetypes_value = am.group(1).strip().rstrip(",").strip()
    pre = tag_content[:am.start()].strip().rstrip(",").strip()

    fields: dict[str, str] = {"archetypes": archetypes_value}
    if pre:
        for chunk in pre.split(","):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            k, _, v = chunk.partition("=")
            fields[k.strip()] = v.strip()

    if not {"domain", "tier", "archetypes"}.issubset(fields):
        return None
    return fields


def parse_library_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse one skill_library/*.md file.

    Returns (frontmatter, bullets). Each bullet is a dict with keys: domain,
    tier, archetypes (list), content (str), source_file (str), file_order (int).

    Tag lines that look like bullet tags but don't parse cleanly produce a
    stderr WARN and the bullet is skipped — better than a silent drop.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("+++"):
        raise ValueError(f"{path.name}: missing +++ frontmatter delimiter")
    # Split on +++ — expect ['', frontmatter, body]
    parts = text.split("+++", 2)
    if len(parts) < 3:
        raise ValueError(f"{path.name}: malformed frontmatter (need opening + closing +++)")
    try:
        frontmatter = tomllib.loads(parts[1])
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path.name}: TOML frontmatter parse error: {e}") from e
    body = parts[2]

    bullets: list[dict[str, Any]] = []
    matches = list(_TAG_LINE_RE.finditer(body))
    for i, m in enumerate(matches):
        tag_content = m.group(1)
        fields = _parse_tag_content(tag_content)
        if fields is None:
            print(
                f"WARN: {path.name}: skipping bullet with malformed tag at offset "
                f"{m.start()}: [{tag_content}] (need domain=, tier=, archetypes= with "
                f"archetypes last)",
                file=sys.stderr,
            )
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content_block = body[start:end].strip()
        if not content_block:
            continue
        # Normalize whitespace (collapse newlines and multi-spaces) — bullets
        # are single-paragraph units even if hand-wrapped in the source file.
        content = " ".join(content_block.split())
        archetypes = [a.strip() for a in fields["archetypes"].split(",") if a.strip()]
        bullets.append({
            "domain": fields["domain"].strip(),
            "tier": fields["tier"].strip(),
            "archetypes": archetypes,
            "content": content,
            "source_file": path.name,
            "file_order": i,
        })
    return frontmatter, bullets


def load_library(library_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Walk library_dir for *.md files. Return {role_id: [bullets]}."""
    out: dict[str, list[dict[str, Any]]] = {}
    md_files = sorted(library_dir.glob("*.md"))
    if not md_files:
        raise ValueError(f"no *.md files found in {library_dir}")
    for md in md_files:
        fm, bullets = parse_library_file(md)
        role_id = fm.get("role_id")
        if not role_id:
            raise ValueError(f"{md.name}: missing role_id in frontmatter")
        if role_id in out:
            raise ValueError(f"duplicate role_id {role_id!r} (also in another library file)")
        out[role_id] = bullets
    return out


# ---------------------------------------------------------------------------
# Bullet selection
# ---------------------------------------------------------------------------

def _keyword_overlap(content: str, keywords: list[str]) -> int:
    """Count keyword matches in content (lowercase substring match)."""
    lc = content.lower()
    return sum(1 for k in keywords if k and k in lc)


def select_bullets(
    bullets: list[dict[str, Any]],
    archetype: str,
    keywords: list[str] | None,
    max_n: int,
) -> list[dict[str, Any]]:
    """Filter by archetype, sort, take top N — TOP UP from non-archetype if sparse.

    Sort key: (tier_rank, -keyword_overlap, file_order). Stable.

    Strategy:
      1. Take all bullets tagged with the target archetype, sort them.
      2. If fewer than max_n, top up with non-archetype bullets (sorted same way)
         to fill the slate. Better than emitting a sparse role section — the
         tier sort still surfaces the strongest non-archetype work, and the
         apply prompt can rephrase the surfaced bullets toward the archetype
         during tailoring.
      3. If zero archetype matches, the result is the top N bullets by tier
         (effectively the same as the original "fall back to all" behavior).
    """
    def sort_key(b: dict[str, Any]) -> tuple[int, int, int]:
        tier_rank = TIER_ORDER.get(b["tier"], 99)
        kw_score = -_keyword_overlap(b["content"], keywords) if keywords else 0
        return (tier_rank, kw_score, b["file_order"])

    matched = sorted(
        [b for b in bullets if archetype in b["archetypes"]],
        key=sort_key,
    )
    if len(matched) < max_n:
        # Top up with non-archetype bullets
        non_matched = sorted(
            [b for b in bullets if archetype not in b["archetypes"]],
            key=sort_key,
        )
        matched.extend(non_matched[: max_n - len(matched)])
    return matched[:max_n]


# ---------------------------------------------------------------------------
# Word-count budget
# ---------------------------------------------------------------------------

def _word_count(s: str) -> int:
    return len(s.split())


def _body_words(roles_bullets: dict[str, list[dict[str, Any]]]) -> int:
    return sum(_word_count(b["content"]) for bullets in roles_bullets.values() for b in bullets)


def trim_to_budget(
    roles_bullets: dict[str, list[dict[str, Any]]],
    budget: int,
    role_order: dict[str, int],
) -> int:
    """Drop bullets until body fits budget. Mutates roles_bullets.

    Drop strategy: highest tier rank (aspirational > adjacent > proven) first,
    then oldest role first (highest role_order index). Never drops a role
    entirely — leaves at least 1 bullet per role even if it pushes over budget.
    Returns final body word count.
    """
    while True:
        total = _body_words(roles_bullets)
        if total <= budget:
            return total

        # Collect candidates: (tier_rank, role_order, role_id, idx)
        candidates: list[tuple[int, int, str, int]] = []
        for rid, bullets in roles_bullets.items():
            if len(bullets) <= 1:
                # Don't trim the last bullet for a role
                continue
            for idx, b in enumerate(bullets):
                tier_rank = TIER_ORDER.get(b["tier"], 99)
                candidates.append((tier_rank, role_order[rid], rid, idx))

        if not candidates:
            return total  # can't trim further

        # Refuse to drop proven bullets (tier_rank=0) — accept overage instead
        non_proven = [c for c in candidates if c[0] > TIER_ORDER["proven"]]
        if not non_proven:
            return total

        # Drop the worst: highest tier_rank, then highest role_order (oldest role)
        non_proven.sort(key=lambda c: (c[0], c[1]), reverse=True)
        _, _, rid, idx = non_proven[0]
        del roles_bullets[rid][idx]


# ---------------------------------------------------------------------------
# Date formatting
# ---------------------------------------------------------------------------

def _format_date(d: str) -> str:
    if d == "present":
        return "Present"
    try:
        yr, mo = d.split("-")
        month_idx = int(mo)
        if not 1 <= month_idx <= 12:
            return d  # Malformed — pass through (zero or out-of-range month)
        return f"{_MONTHS[month_idx]} {yr}"
    except (ValueError, IndexError):
        return d  # Malformed — pass through; canon validation should catch this earlier


def _format_dates(start: str, end: str) -> str:
    return f"{_format_date(start)} – {_format_date(end)}"


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

def emit_resume(
    canon: dict[str, Any],
    roles_selected: dict[str, list[dict[str, Any]]],
    include_references: bool,
    archetype: str,
    style: dict[str, Any],
) -> str:
    """Build the composed markdown. `style` comes from load_archetype_style()."""
    lines: list[str] = []

    # Header
    personal = canon["personal"]
    contact_email = personal.get("email_security") or personal.get("email_personal") or ""
    lines.append(f"# {personal['name']}")
    lines.append("")
    lines.append(
        f"{personal.get('location', '')} | {personal.get('phone', '')} | {contact_email}"
    )
    lines.append("")

    # Punchy intro keyed to the archetype. Apply prompt may rewrite per-JD
    # during tailoring; this is the off-the-shelf default.
    intro = style["intros"].get(archetype, style["intro_default"])
    lines.append(intro)
    lines.append("")

    # Professional Experience
    lines.append("## Professional Experience")
    lines.append("")
    employment_by_id = {r["id"]: r for r in canon.get("employment", [])}
    for role_id, bullets in roles_selected.items():
        role = employment_by_id.get(role_id)
        if not role:
            continue
        date_str = _format_dates(role["start"], role["end"])
        # Format matches dev-quick/resume-converter expected input:
        #   ### Company | Location  (H3 with right-tabbed location)
        #   #### Title | Dates       (H4 with right-tabbed dates)
        lines.append(f"### {role['company']} | {role['location']}")
        lines.append(f"#### {role['title']} | {date_str}")
        lines.append("")
        for b in bullets:
            lines.append(f"- {b['content']}")
        lines.append("")

    # Technical Skills — union of domains hit
    domains_hit: set[str] = set()
    for bullets in roles_selected.values():
        for b in bullets:
            domains_hit.add(b["domain"])

    skills_by_domain = style["skills_by_domain"]
    domain_order = style["arch_domain_order"].get(archetype, style["domain_order_default"])
    skill_lines: list[str] = []
    for d in domain_order:
        if d in domains_hit and d in skills_by_domain:
            label, content = skills_by_domain[d]
            skill_lines.append(f"**{label}:** {content}")
    if skill_lines:
        lines.append("## Technical Skills")
        lines.append("")
        lines.extend(skill_lines)
        lines.append("")

    # Education & Certifications
    # Layout matches resume-converter expected format: bold degree on its own
    # line, then a plain paragraph with school | location | dates | extras,
    # then a `**Professional Certifications:** ...` skill-style line that the
    # converter renders with a bold label.
    education = canon.get("education", [])
    certs = [c for c in canon.get("certifications", []) if c.get("status") == "active"]
    if education or certs:
        lines.append("## Education & Certifications")
        lines.append("")
        for ed in education:
            degree_str = ed.get("degree", "")
            if ed.get("concentration"):
                degree_str += f" ({ed['concentration']})"
            lines.append(f"**{degree_str}**")
            lines.append("")
            detail_parts: list[str] = []
            if ed.get("school"):
                detail_parts.append(ed["school"])
            if ed.get("location"):
                detail_parts.append(ed["location"])
            if ed.get("dates"):
                detail_parts.append(ed["dates"])
            if ed.get("minor"):
                detail_parts.append(f"Minor in {ed['minor']}")
            if ed.get("gpa_major"):
                detail_parts.append(f"{ed['gpa_major']} GPA in Major")
            if ed.get("honors"):
                detail_parts.extend(ed["honors"])
            if detail_parts:
                lines.append(" | ".join(detail_parts))
                lines.append("")
        if certs:
            cert_names = [c["name"] for c in certs]
            lines.append(f"**Professional Certifications:** {' | '.join(cert_names)}")
            lines.append("")

    # Activities — single-line bullets (the resume-converter doesn't handle
    # nested bullets with leading whitespace, so descriptions get inlined).
    activities = canon.get("professional_activities", [])
    if activities:
        lines.append("## Professional Activities")
        lines.append("")
        for a in activities:
            name = a.get("name", "")
            role = a.get("role", "")
            dates = a.get("dates", "")
            desc = a.get("description", "")
            head = f"- **{name}**"
            if role:
                head += f", {role}"
            if dates:
                head += f" ({dates})"
            if desc:
                head += f"; {desc}"
            lines.append(head)
        lines.append("")

    # References (only if explicitly requested)
    if include_references and canon.get("references"):
        lines.append("## References")
        lines.append("")
        for r in canon["references"]:
            head_parts = [r.get("name", ""), r.get("title", ""), r.get("company", "")]
            lines.append("**" + " | ".join(p for p in head_parts if p) + "**")
            if r.get("email"):
                lines.append(f"- e: {r['email']}")
            if r.get("phone_work"):
                lines.append(f"- w: {r['phone_work']}")
            if r.get("phone_cell"):
                lines.append(f"- c: {r['phone_cell']}")
            if r.get("twitter"):
                lines.append(f"- t: {r['twitter']}")
            lines.append("")
    # No "references available on request" footer — that line goes in the cover
    # letter instead. The resume ends with the Activities section.

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compose a tailored resume master from canon + skill_library.")
    p.add_argument("--canon", required=True, help="Path to canon.json")
    p.add_argument("--library", required=True, help="Path to skill_library/ directory")
    p.add_argument("--role-archetype", default=None,
                   help="Target role archetype (default: the archetypes config's default_archetype)")
    p.add_argument("--archetypes-config", default=str(DEFAULT_ARCHETYPES_CONFIG),
                   help="Path to the archetype definitions JSON (default: config/archetypes.json)")
    p.add_argument("--jd-keywords", default=None,
                   help="Optional path to plain-text file of JD keywords (one per line or whitespace-separated)")
    p.add_argument("--max-bullets-per-role", type=int, default=3,
                   help="Max bullets surfaced per role (default: 3 — tight for 2-page total)")
    p.add_argument("--body-word-budget", type=int, default=500,
                   help="Body word cap; aspirational/adjacent bullets drop first if exceeded (default: 500 — tight for 2-page total)")
    p.add_argument("--include-references", action="store_true",
                   help="Emit the References section (default: omit)")
    p.add_argument("--out", default=None,
                   help="Output file path (default: stdout)")
    args = p.parse_args(argv)

    # ---- Validate + traversal-guard paths ----
    # canon and library must resolve under REPO_ROOT (defense in depth — the
    # only legitimate caller is apply_pipeline.py which passes repo-internal
    # paths). --out is restricted to REPO_ROOT/tmp by default; an explicit
    # absolute path under REPO_ROOT is also allowed.
    canon_path = Path(args.canon)
    if not canon_path.is_file():
        print(f"ERROR: canon file not found: {canon_path}", file=sys.stderr)
        return 2
    library_dir = Path(args.library)
    if not library_dir.is_dir():
        print(f"ERROR: library directory not found: {library_dir}", file=sys.stderr)
        return 2

    try:
        canon_resolved = _resolve_inside(canon_path, REPO_ROOT)
        library_resolved = _resolve_inside(library_dir, REPO_ROOT)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not canon_resolved.is_file():
        print(f"ERROR: canon path resolves to non-file: {canon_resolved}", file=sys.stderr)
        return 2
    if not library_resolved.is_dir():
        print(f"ERROR: library path resolves to non-directory: {library_resolved}", file=sys.stderr)
        return 2

    # ---- Load canon ----
    try:
        canon = json.loads(canon_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: failed to load canon: {e}", file=sys.stderr)
        return 2
    if not canon.get("employment"):
        print("ERROR: canon has no employment[] entries", file=sys.stderr)
        return 2

    # ---- Load skill library ----
    try:
        library = load_library(library_resolved)
    except ValueError as e:
        print(f"ERROR: skill library: {e}", file=sys.stderr)
        return 2

    # ---- Load archetype style (intros / domain ordering / Skills content) ----
    archetypes_path = Path(args.archetypes_config)
    if not archetypes_path.is_file():
        print(f"ERROR: archetypes config not found: {archetypes_path}", file=sys.stderr)
        return 2
    try:
        archetypes_resolved = _resolve_inside(archetypes_path, REPO_ROOT)
        style = load_archetype_style(archetypes_resolved)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # Resolve the target archetype: explicit flag wins, else the config default.
    archetype = args.role_archetype or style["default_archetype"]
    if not archetype:
        print("ERROR: no --role-archetype given and archetypes config has no default_archetype",
              file=sys.stderr)
        return 2

    # ---- Load JD keywords (optional) ----
    keywords: list[str] | None = None
    if args.jd_keywords:
        kw_path = Path(args.jd_keywords)
        if not kw_path.is_file():
            print(f"WARN: --jd-keywords file not found: {kw_path}; ignoring", file=sys.stderr)
        else:
            raw = kw_path.read_text(encoding="utf-8")
            keywords = [k.strip().lower() for k in raw.split() if k.strip()]

    # ---- Select bullets per role ----
    roles_selected: dict[str, list[dict[str, Any]]] = {}
    role_order: dict[str, int] = {}
    for i, role in enumerate(canon["employment"]):
        rid = role["id"]
        role_order[rid] = i
        if rid not in library:
            print(f"WARN: no skill library file for role_id={rid!r}; emitting role with no bullets",
                  file=sys.stderr)
            roles_selected[rid] = []
            continue
        roles_selected[rid] = select_bullets(
            library[rid],
            archetype,
            keywords,
            args.max_bullets_per_role,
        )

    # ---- Trim to budget ----
    before_words = _body_words(roles_selected)
    after_words = trim_to_budget(roles_selected, args.body_word_budget, role_order)
    if before_words > args.body_word_budget:
        print(f"  trimmed body: {before_words} → {after_words} words (budget {args.body_word_budget})",
              file=sys.stderr)

    # ---- Emit ----
    markdown = emit_resume(canon, roles_selected, args.include_references, archetype, style)

    # ---- Lint emitted output for banned punctuation (em-dash etc.) ----
    # Doesn't fail the run — just flags so library/intro drift is visible.
    for marker in BANNED_PUNCTUATION:
        count = markdown.count(marker)
        if count > 0:
            # Show the first offending line for quick diagnosis
            first_line = next(
                (line for line in markdown.splitlines() if marker in line),
                "",
            )
            print(
                f"WARN: composed output contains {count}× banned punctuation {marker!r}. "
                f"First occurrence: {first_line[:120]}{'…' if len(first_line) > 120 else ''}",
                file=sys.stderr,
            )

    if args.out:
        out_path = Path(args.out)
        # Restrict --out to inside REPO_ROOT (typically REPO_ROOT/tmp/...).
        # Defense-in-depth: blocks `--out ../../etc/cron.d/x` paths.
        try:
            out_resolved = _resolve_inside(out_path, REPO_ROOT)
        except ValueError as e:
            print(f"ERROR: --out: {e}", file=sys.stderr)
            return 2
        try:
            out_resolved.parent.mkdir(parents=True, exist_ok=True)
            out_resolved.write_text(markdown, encoding="utf-8")
        except OSError as e:
            print(f"ERROR: failed to write --out: {e}", file=sys.stderr)
            return 1
        out_path = out_resolved
        print(f"Composed: {out_path} ({after_words} body words, archetype={archetype})",
              file=sys.stderr)
        print(str(out_path))
    else:
        sys.stdout.write(markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
