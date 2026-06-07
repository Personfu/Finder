#!/usr/bin/env python3
"""Golden-file test for scripts/resume_compose.py.

Invokes the composer against the synthetic fixtures in tests/fixtures/ and
diffs the output against the committed expected_composed_master.md.

Pure stdlib — no pytest, no external test runner. Run directly:

    python tests/test_resume_compose.py

Exits 0 on pass, 1 on any failure. Failures print a unified diff to stderr so
you can see what changed.

Regenerating the golden file (when an intentional change to the composer
output is made):

    python scripts/resume_compose.py \\
      --canon tests/fixtures/sample_canon.json \\
      --library tests/fixtures/sample_library \\
      --role-archetype test-archetype \\
      --max-bullets-per-role 4 \\
      --body-word-budget 700 \\
      --out tests/fixtures/expected_composed_master.md

Then commit the regenerated golden alongside the composer change.
"""

from __future__ import annotations

import difflib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
COMPOSER = REPO_ROOT / "scripts" / "resume_compose.py"


def _run(args: list[str]) -> tuple[int, str, str]:
    """Run composer with given args. Returns (exit_code, stdout, stderr).

    Injects the synthetic fixture archetypes config so tests stay independent
    of the shipped config/archetypes.json persona (unless a test passes its own).
    """
    if "--archetypes-config" not in args:
        args = [*args, "--archetypes-config", str(FIXTURES / "sample_archetypes.json")]
    proc = subprocess.run(
        [sys.executable, str(COMPOSER), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _diff(actual: str, expected: str, label_actual: str, label_expected: str) -> str:
    return "".join(difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile=label_expected,
        tofile=label_actual,
    ))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_golden_match() -> bool:
    """Run composer against fixtures, compare stdout to expected_composed_master.md."""
    expected_path = FIXTURES / "expected_composed_master.md"
    expected = expected_path.read_text(encoding="utf-8")

    code, stdout, stderr = _run([
        "--canon", str(FIXTURES / "sample_canon.json"),
        "--library", str(FIXTURES / "sample_library"),
        "--role-archetype", "test-archetype",
        "--max-bullets-per-role", "4",
        "--body-word-budget", "700",
    ])

    if code != 0:
        print(f"FAIL test_golden_match: composer exited {code}", file=sys.stderr)
        print(f"  stderr:\n{stderr}", file=sys.stderr)
        return False

    if stdout != expected:
        print("FAIL test_golden_match: output differs from golden", file=sys.stderr)
        print(_diff(stdout, expected, "stdout (actual)", str(expected_path)), file=sys.stderr)
        return False

    return True


def test_missing_canon_returns_2() -> bool:
    """Validation errors return exit code 2 per repo convention."""
    code, _, _ = _run([
        "--canon", str(FIXTURES / "nonexistent_canon.json"),
        "--library", str(FIXTURES / "sample_library"),
        "--role-archetype", "test-archetype",
    ])
    if code != 2:
        print(f"FAIL test_missing_canon_returns_2: expected exit 2, got {code}", file=sys.stderr)
        return False
    return True


def test_missing_library_returns_2() -> bool:
    code, _, _ = _run([
        "--canon", str(FIXTURES / "sample_canon.json"),
        "--library", str(FIXTURES / "nonexistent_library"),
        "--role-archetype", "test-archetype",
    ])
    if code != 2:
        print(f"FAIL test_missing_library_returns_2: expected exit 2, got {code}", file=sys.stderr)
        return False
    return True


def test_unknown_archetype_falls_back() -> bool:
    """Archetype with zero matches falls back to tier-only sort. Output must be non-empty."""
    code, stdout, _ = _run([
        "--canon", str(FIXTURES / "sample_canon.json"),
        "--library", str(FIXTURES / "sample_library"),
        "--role-archetype", "totally-fake-archetype",
        "--max-bullets-per-role", "2",
    ])
    if code != 0:
        print(f"FAIL test_unknown_archetype_falls_back: exit {code}", file=sys.stderr)
        return False
    if "## Professional Experience" not in stdout or "Alpha Corp" not in stdout:
        print("FAIL test_unknown_archetype_falls_back: expected Professional Experience + roles in output", file=sys.stderr)
        return False
    return True


def test_budget_trim_drops_aspirational_first() -> bool:
    """Tight body word budget should drop aspirational bullets before proven."""
    code, stdout, stderr = _run([
        "--canon", str(FIXTURES / "sample_canon.json"),
        "--library", str(FIXTURES / "sample_library"),
        "--role-archetype", "test-archetype",
        "--max-bullets-per-role", "3",
        "--body-word-budget", "20",
    ])
    if code != 0:
        print(f"FAIL test_budget_trim_drops_aspirational_first: exit {code}", file=sys.stderr)
        return False
    # The aspirational bullet from role_alpha mentions "Aspirational bullet — gets dropped"
    if "Aspirational bullet" in stdout:
        print("FAIL: budget-trim should have dropped the aspirational bullet", file=sys.stderr)
        return False
    # Both proven bullets must survive (one per role)
    if "Built test endpoint processing 1M" not in stdout:
        print("FAIL: budget-trim dropped a proven bullet (should never happen)", file=sys.stderr)
        return False
    if "Wrote test framework adopted by four teams" not in stdout:
        print("FAIL: budget-trim dropped a proven bullet (should never happen)", file=sys.stderr)
        return False
    return True


def test_out_path_writes_file_and_prints_path() -> bool:
    """--out writes file at the resolved path and stdout prints that path."""
    import tempfile
    tmp_root = REPO_ROOT / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)  # gitignored, so absent on a fresh clone
    with tempfile.TemporaryDirectory(dir=str(tmp_root)) as td:
        # tmp/ is inside REPO_ROOT — passes the _resolve_inside guard
        out_file = Path(td) / "composed.md"
        code, stdout, stderr = _run([
            "--canon", str(FIXTURES / "sample_canon.json"),
            "--library", str(FIXTURES / "sample_library"),
            "--role-archetype", "test-archetype",
            "--out", str(out_file),
        ])
        if code != 0:
            print(f"FAIL test_out_path: exit {code}\nstderr: {stderr}", file=sys.stderr)
            return False
        if not out_file.exists():
            print(f"FAIL test_out_path: expected file {out_file} was not created", file=sys.stderr)
            return False
        # Stdout should print the resolved out path (one line)
        if str(out_file.resolve()) not in stdout:
            print(f"FAIL test_out_path: stdout missing out path. Got:\n{stdout}", file=sys.stderr)
            return False
    return True


def test_out_traversal_rejected() -> bool:
    """--out outside REPO_ROOT must be refused with exit 2."""
    # Use an absolute path that's clearly outside the repo
    import os
    bad_out = "/tmp/should_not_be_written.md" if os.name != "nt" else "C:\\Windows\\Temp\\should_not_be_written.md"
    code, _, stderr = _run([
        "--canon", str(FIXTURES / "sample_canon.json"),
        "--library", str(FIXTURES / "sample_library"),
        "--role-archetype", "test-archetype",
        "--out", bad_out,
    ])
    if code != 2:
        print(f"FAIL test_out_traversal_rejected: expected exit 2, got {code}\nstderr: {stderr}", file=sys.stderr)
        return False
    if "outside allowed parent" not in stderr.lower() and "outside" not in stderr.lower():
        print(f"FAIL test_out_traversal_rejected: stderr didn't mention traversal. Got:\n{stderr}", file=sys.stderr)
        return False
    return True


def test_misordered_tag_still_parses() -> bool:
    """[tier=X, domain=Y, archetypes=...] (misordered fields) must parse correctly,
    not silently drop. This is the order-independence fix."""
    import tempfile
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT / "tmp")) as td:
        lib = Path(td)
        # Build a one-file library with a misordered tag
        (lib / "role_x.md").write_text(
            "+++\nrole_id = \"role_x\"\n+++\n\n"
            "[tier=proven, domain=endpoint, archetypes=test-archetype]\n"
            "Bullet content with misordered tag fields should still parse.\n",
            encoding="utf-8",
        )
        # Canon needs role_x
        canon_text = (FIXTURES / "sample_canon.json").read_text(encoding="utf-8")
        canon_text = canon_text.replace(
            '"id": "role_alpha"',
            '"id": "role_x"',
        )
        canon_file = Path(td) / "canon.json"
        canon_file.write_text(canon_text, encoding="utf-8")

        code, stdout, stderr = _run([
            "--canon", str(canon_file),
            "--library", str(lib),
            "--role-archetype", "test-archetype",
        ])
        if code != 0:
            print(f"FAIL test_misordered_tag_still_parses: exit {code}\nstderr: {stderr}", file=sys.stderr)
            return False
        if "Bullet content with misordered tag fields" not in stdout:
            print("FAIL: misordered-tag bullet was silently dropped (the bug we just fixed)", file=sys.stderr)
            print(f"stdout:\n{stdout}", file=sys.stderr)
            return False
    return True


def test_malformed_tag_warns_but_continues() -> bool:
    """A bullet tag missing 'archetypes' should produce a WARN and skip that bullet,
    not fail the whole composition."""
    import tempfile
    with tempfile.TemporaryDirectory(dir=str(REPO_ROOT / "tmp")) as td:
        lib = Path(td)
        (lib / "role_y.md").write_text(
            "+++\nrole_id = \"role_y\"\n+++\n\n"
            "[domain=endpoint, tier=proven]\n"  # missing archetypes
            "Bullet with malformed tag — should be skipped with a warning.\n\n"
            "[domain=leadership, tier=proven, archetypes=test-archetype]\n"
            "Valid bullet that should still appear in output.\n",
            encoding="utf-8",
        )
        canon_text = (FIXTURES / "sample_canon.json").read_text(encoding="utf-8")
        canon_text = canon_text.replace('"id": "role_alpha"', '"id": "role_y"')
        canon_file = Path(td) / "canon.json"
        canon_file.write_text(canon_text, encoding="utf-8")

        code, stdout, stderr = _run([
            "--canon", str(canon_file),
            "--library", str(lib),
            "--role-archetype", "test-archetype",
        ])
        if code != 0:
            print(f"FAIL test_malformed_tag_warns_but_continues: exit {code}", file=sys.stderr)
            return False
        if "WARN" not in stderr or "malformed tag" not in stderr:
            print(f"FAIL: expected WARN about malformed tag. Got stderr:\n{stderr}", file=sys.stderr)
            return False
        if "Valid bullet that should still appear" not in stdout:
            print("FAIL: valid sibling bullet didn't survive the warning skip", file=sys.stderr)
            return False
        if "Bullet with malformed tag" in stdout:
            print("FAIL: malformed-tag bullet content leaked into output", file=sys.stderr)
            return False
    return True


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    ("golden_match", test_golden_match),
    ("missing_canon_returns_2", test_missing_canon_returns_2),
    ("missing_library_returns_2", test_missing_library_returns_2),
    ("unknown_archetype_falls_back", test_unknown_archetype_falls_back),
    ("budget_trim_drops_aspirational_first", test_budget_trim_drops_aspirational_first),
    ("out_path_writes_file_and_prints_path", test_out_path_writes_file_and_prints_path),
    ("out_traversal_rejected", test_out_traversal_rejected),
    ("misordered_tag_still_parses", test_misordered_tag_still_parses),
    ("malformed_tag_warns_but_continues", test_malformed_tag_warns_but_continues),
]


def main() -> int:
    passed = 0
    failed = 0
    for name, fn in TESTS:
        ok = fn()
        if ok:
            print(f"  PASS  {name}")
            passed += 1
        else:
            print(f"  FAIL  {name}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
