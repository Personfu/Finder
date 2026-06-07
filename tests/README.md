# Finder tests

Stdlib-only test pattern. No pytest, no pip, no test framework — just `python tests/*.py`.

## Running

```
python tests/test_resume_compose.py
```

Exit 0 on pass, 1 on any failure. Each test prints `PASS` or `FAIL` per case; on
failure, a unified diff is printed to stderr for golden-file mismatches.

## Pattern

Each test file is a self-contained script with:

- Module-level helpers for invoking the script under test (subprocess, stdlib only)
- One function per test case, returning `True` / `False`
- A `TESTS` list of `(name, fn)` pairs
- A `main()` that iterates, counts pass/fail, and returns 0 or 1

This is deliberately minimal because the Finder pipeline is stdlib-only and we
don't want to gate test runs on a pip install. If the suite grows past 20 tests,
revisit and consider stdlib `unittest`.

## Golden files

`tests/fixtures/expected_*.md` are golden files — the expected output of the
script under test. They are committed alongside the test runner. When a script
change intentionally alters its output, regenerate the golden file with the
exact command documented at the top of the relevant test file, eyeball the diff
in git, and commit the golden alongside the code change.

Fixture data (canon, library files) uses fake names and contact info. Never
commit real PII to `tests/fixtures/`.
