#!/usr/bin/env python3
"""
Deterministic keyword-overlap analysis between a JD and a master resume.

Pure stdlib. No LLM calls — this script produces the structured signal that
the apply-skill prompt then reasons about (which gaps to address, which
strengths to lead with) inside the live session.

Output (stdout, JSON):
{
    "jd_keyword_counts":     {"python": 4, "kubernetes": 3, ...},   # top N JD-only token frequencies
    "resume_keyword_counts": {"python": 3, ...},
    "present":               ["python", "incident response", ...],   # in JD AND resume — leverage these
    "missing":               ["go", "kubernetes operator", ...],          # in JD, not in resume — gaps
    "coverage_pct":          63.5,                                        # % of top JD keywords present
    "jd_word_count":         1234,
    "resume_word_count":     652
}

Usage:
    python scripts/ats_overlap.py <jd-text-path> <resume-md-path> [--top 40]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


# Words too common to be signal — generic English + corporate boilerplate that
# shows up in every JD. Keep this lean; the goal is to surface concrete tech +
# skill terms, not strip every adjective.
_STOP_WORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because been before being
below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't
down during each few for from further had hadn't has hasn't have haven't having he he'd he'll
he's her here here's hers herself him himself his how how's i i'd i'll i'm i've if in into is
isn't it it's its itself let's me more most mustn't my myself no nor not of off on once only or
other ought our ours ourselves out over own same shan't she she'd she'll she's should shouldn't
so some such than that that's the their theirs them themselves then there there's these they
they'd they'll they're they've this those through to too under until up very was wasn't we we'd
we'll we're we've were weren't what what's when when's where where's which while who who's whom
why why's with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves
also will may must us new our get gets got make makes made take takes took use used uses using
work works worked working role roles team teams ability able help helps helped people person
strong solid great good well lead leads leading led plus across via since just like really
ensure ensures ensured experience experiences year years day days time times look looks looking
build builds built run runs ran something things one two three four five many much most least
include includes including required preferred ideal perfect candidate candidates seeking want
wants wanted apply applies applied join joining looking find finds found offer offers offered
position positions opportunity opportunities benefit benefits perks salary compensation comp
range pay paid range posting jobs job listing about company companies who what when where why how
research engineer engineering service services information system systems area areas part parts
example examples form forms select required preferred require requires required level levels
fast deep wide high low real long short next prior past current future hire hires hired hiring
within without around upon every either neither neighborhood
veteran veterans military disability disabilities disorder disorders disclose disclosure
voluntarily voluntary protected pursuant section regulation regulations equal eeo eeoc
applicant applicants employer employers employment authorize authorization eligibility
sponsor sponsorship visa h1b citizen citizenship lawful permanent resident
gender race ethnicity ethnic identify identification self-identification age aged ages
naval air army marine coast guard duty disabled wounded vietnam transgender lgbtq orientation
maternity paternity pregnancy religion religious convicted conviction arrest
asked ask answer answers question questions
first last middle name names email phone address city state zip postal country
linkedin website portfolio github
agree agreement consent permission privacy
total amount per hour annual annually monthly monthly bonus equity
location remote hybrid office onsite on-site in-office
united states usa u.s
""".split())

# Multi-word security/tech phrases worth detecting as a unit. Order matters —
# longer phrases first so "incident response engineer" doesn't get partially
# matched by "incident response".
_PHRASES = (
    "zero trust network access", "zero trust", "kubernetes security", "ai security",
    "incident response", "threat intelligence", "threat intel", "detection engineering",
    "endpoint security", "endpoint detection", "infrastructure security", "platform security",
    "cloud security", "application security", "appsec", "cyber threat intelligence",
    "security operations", "security engineering", "vulnerability management",
    "purple team", "red team", "blue team",
    "vendor management", "incident commander", "on-call rotation",
    "elastic stack", "elk stack", "elasticsearch", "splunk",
    "machine learning", "large language model", "llm", "ai-assisted",
    "container security", "kubernetes operator", "service mesh",
    "ci/cd", "ci-cd", "continuous integration",
    "iam policy", "least privilege", "policy as code",
    "ai safety", "model security", "agent security", "model context protocol",
    "frontier ai", "frontier model", "ai agent",
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+/.\-]{1,}")
# Hyphen-friendly so we keep "ci/cd", "k8s", "c++", "go-lang"


def tokenize(text: str) -> list[str]:
    """
    Return lowercase tokens. Multi-word phrases from _PHRASES are emitted as
    single tokens; their constituent words are stripped from the text first
    so they don't double-count as individual tokens.
    """
    text = text.lower()
    found_phrases = [p for p in _PHRASES if p in text]
    for p in found_phrases:
        text = text.replace(p, " ")
    raw_tokens = _TOKEN_RE.findall(text)
    return raw_tokens + found_phrases


# Contraction fragments left over after apostrophe-split tokenization
# ("we'll" -> "we" + "ll"; "we're" -> "we" + "re").
_CONTRACTION_FRAGMENTS = frozenset({"ll", "re", "ve", "d", "m", "s", "t"})

# Common location tokens — JDs always list cities/states; they're not skills.
_LOCATION_NOISE = frozenset({
    "san", "francisco", "york", "ny", "ca", "tx", "wa", "dc", "ma", "il", "mo",
    "boston", "seattle", "austin", "chicago", "denver", "atlanta", "dallas",
    "los", "angeles", "diego", "jose", "francisco", "portland",
    "remote", "hybrid", "office", "onsite",
})


def filter_signal(tokens: list[str]) -> list[str]:
    """Drop stop words, very short tokens, pure numbers, location noise."""
    out: list[str] = []
    for t in tokens:
        if t in _STOP_WORDS:
            continue
        if t in _CONTRACTION_FRAGMENTS:
            continue
        if t in _LOCATION_NOISE:
            continue
        if len(t) < 2:
            continue
        if t.isdigit():
            continue
        if re.fullmatch(r"\d+[+\-/.]?\d*", t):
            continue
        out.append(t)
    return out


def analyze(jd_text: str, resume_text: str, top_n: int = 40) -> dict[str, object]:
    jd_tokens_raw = tokenize(jd_text)
    res_tokens_raw = tokenize(resume_text)

    jd_tokens = filter_signal(jd_tokens_raw)
    res_tokens = filter_signal(res_tokens_raw)

    jd_counter = Counter(jd_tokens)
    res_counter = Counter(res_tokens)

    # Top N most frequent JD signal tokens — what the role values
    top_jd = jd_counter.most_common(top_n)

    res_set = set(res_tokens)
    present: list[str] = []
    missing: list[str] = []
    for term, _count in top_jd:
        if term in res_set:
            present.append(term)
        else:
            missing.append(term)

    coverage = (len(present) / len(top_jd) * 100.0) if top_jd else 0.0

    return {
        "jd_keyword_counts": dict(top_jd),
        "resume_keyword_counts": dict(res_counter.most_common(top_n)),
        "present": present,
        "missing": missing,
        "coverage_pct": round(coverage, 1),
        "jd_word_count": len(jd_tokens_raw),
        "resume_word_count": len(res_tokens_raw),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compute keyword overlap between a JD and resume.")
    p.add_argument("jd_path", help="Path to JD plain text (or '-' for stdin)")
    p.add_argument("resume_path", help="Path to resume markdown")
    p.add_argument("--top", type=int, default=40, help="How many top JD keywords to consider (default 40)")
    args = p.parse_args(argv)

    if args.jd_path == "-":
        jd_text = sys.stdin.read()
    else:
        jd_text = Path(args.jd_path).read_text(encoding="utf-8", errors="replace")

    resume_path = Path(args.resume_path)
    if not resume_path.exists():
        print(f"resume not found: {resume_path}", file=sys.stderr)
        return 2
    resume_text = resume_path.read_text(encoding="utf-8", errors="replace")

    result = analyze(jd_text, resume_text, top_n=args.top)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
