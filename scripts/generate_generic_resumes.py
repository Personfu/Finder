#!/usr/bin/env python3
"""Generate one composed-master resume per role archetype.

Writes one markdown file per archetype to `data/generic_resumes/` — the
"off-the-shelf" versions you can grab if you need to submit a resume in a
hurry without running the full `/finder:apply` flow against a specific JD.

The archetype list is read from config/archetypes.json (not hardcoded), so
adding your own archetype there automatically gets a generic resume here.

These are derived artifacts (composed from `data/canon.json` +
`data/skill_library/*.md`) so the directory is gitignored — regenerate any
time canon or library bullets change.

Usage:
    python scripts/generate_generic_resumes.py
    # Generate from the shipped example persona instead of your real canon:
    python scripts/generate_generic_resumes.py --canon data/canon.example.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSER = REPO_ROOT / "scripts" / "resume_compose.py"
CANON = REPO_ROOT / "data" / "canon.json"
LIBRARY = REPO_ROOT / "data" / "skill_library"
OUT_DIR = REPO_ROOT / "data" / "generic_resumes"
ARCHETYPES_CONFIG = REPO_ROOT / "config" / "archetypes.json"

# Optional: chain md_to_docx.py to produce DOCX alongside the markdown. If
# python-docx isn't installed (the converter script's only dep) we skip DOCX
# generation silently and still emit the markdown — useful for quick review.
CONVERTER = REPO_ROOT / "scripts" / "md_to_docx.py"


def load_archetypes(config_path: Path) -> list[str]:
    """Archetype names, in the order they appear in the archetypes config.

    Reading from config (rather than a hardcoded list) means adding your own
    archetype there gives you a generic resume here for free.
    """
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return list(data.get("archetypes", {}).keys())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate one composed resume per archetype.")
    p.add_argument("--canon", default=str(CANON),
                   help="Path to canon JSON (default: data/canon.json; use data/canon.example.json for the example persona)")
    p.add_argument("--library", default=str(LIBRARY),
                   help="Path to skill_library/ directory (default: data/skill_library/)")
    p.add_argument("--archetypes-config", default=str(ARCHETYPES_CONFIG),
                   help="Path to the archetype definitions JSON (default: config/archetypes.json)")
    args = p.parse_args(argv)

    canon = Path(args.canon)
    library = Path(args.library)
    archetypes_config = Path(args.archetypes_config)

    if not canon.is_file():
        print(f"ERROR: canon not found at {canon}", file=sys.stderr)
        return 2
    if not library.is_dir():
        print(f"ERROR: library not found at {library}", file=sys.stderr)
        return 2
    if not archetypes_config.is_file():
        print(f"ERROR: archetypes config not found at {archetypes_config}", file=sys.stderr)
        return 2

    archetypes = load_archetypes(archetypes_config)
    if not archetypes:
        print(f"ERROR: no archetypes defined in {archetypes_config}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    converter_available = CONVERTER.is_file()
    print(f"Generating generic resumes → {OUT_DIR}", file=sys.stderr)
    if converter_available:
        print(f"  (+ DOCX via {CONVERTER})", file=sys.stderr)
    else:
        print(f"  (DOCX skipped — converter not at {CONVERTER})", file=sys.stderr)
    print(file=sys.stderr)
    for archetype in archetypes:
        out_md = OUT_DIR / f"{archetype}.md"
        try:
            proc = subprocess.run(
                [sys.executable, str(COMPOSER),
                 "--canon", str(canon),
                 "--library", str(library),
                 "--archetypes-config", str(archetypes_config),
                 "--role-archetype", archetype,
                 "--out", str(out_md)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{archetype}: timeout")
            continue
        if proc.returncode != 0:
            failures.append(f"{archetype}: exit {proc.returncode} — {proc.stderr.strip()}")
            continue
        # Word count for quick sanity
        words = len(out_md.read_text(encoding="utf-8").split())
        flag = " ⚠ over budget" if words > 1100 else ""

        # Optional DOCX via dev-quick/resume-converter
        docx_status = ""
        if converter_available:
            out_docx = OUT_DIR / f"{archetype}.docx"
            try:
                cproc = subprocess.run(
                    [sys.executable, str(CONVERTER), str(out_md), "-o", str(out_docx)],
                    capture_output=True, text=True, encoding="utf-8", timeout=30,
                )
                if cproc.returncode == 0:
                    docx_status = "  + DOCX"
                else:
                    docx_status = f"  ⚠ DOCX failed: {cproc.stderr.strip()[:80]}"
            except subprocess.TimeoutExpired:
                docx_status = "  ⚠ DOCX converter timeout"

        print(f"  {archetype:<22}  {words:>4} words{flag}{docx_status}", file=sys.stderr)

    print(file=sys.stderr)
    if failures:
        print("FAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print(f"All {len(archetypes)} archetype resumes generated. Edit data/canon.json, "
          f"data/skill_library/*.md, or config/archetypes.json and re-run to refresh.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
