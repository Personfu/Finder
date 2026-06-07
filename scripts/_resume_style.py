"""Static style rules for the resume composer (scripts/resume_compose.py) and
the apply prompt (prompts/apply.md).

Pure data — no logic, no file I/O. This module holds the rules that DON'T vary
by persona or target role: tier ordering, the AI-tell deny-lists, and the
authoring guidance the apply prompt pulls in.

Archetype-specific data (per-role intros, Skills-block content, domain
ordering) is NOT here — it lives in config/archetypes.json so anyone can add a
target-role lens without editing Python. The composer loads it via
resume_compose.load_archetype_style().

Vocabulary contract: tier values (`proven`, `adjacent`, `aspirational`) match
the bucket names in config/job_search_targets.json. Don't drift.
"""

from __future__ import annotations

# Tier sort order — lower number = higher priority.
# Bullets sort proven before adjacent before aspirational. Anything unrecognized
# sorts after all of these (99) so the composer doesn't silently include
# typo-tier bullets.
TIER_ORDER: dict[str, int] = {
    "proven": 0,
    "adjacent": 1,
    "aspirational": 2,
}

# Verbs that read as AI-generated to 2025/2026-era recruiter screens.
# Per docs/resume/FIND.md: 62% of resumes flagged as AI-generated in 2025 were
# rejected. Detection uses perplexity scoring + stylometry; the listed verbs are
# the highest-signal triggers.
BANNED_VERBS: list[str] = [
    "leveraged",
    "leveraging",
    "utilized",
    "utilizing",
    "spearheaded",
    "spearheading",
    "streamlined",
    "streamlining",
    "enhanced",
    "enhancing",
]

# Phrases that pattern-match as AI-stiff. Same source.
BANNED_PHRASES: list[str] = [
    "robust",
    "comprehensive",
    "dynamic",  # only as adjective; "dynamic analysis" is fine (apply prompt judges context)
    "results-driven",
    "synergy",
    "cutting-edge",
    "seamless",
    "seamlessly",
    "holistic",
    "team player",
    "wealth of experience",
    "passionate about",
    "bring a unique perspective",
    "ecosystem",  # only as metaphor; a named platform ecosystem is debatable (apply prompt judges)
]

# Punctuation marks that read as AI-generated. Em-dash specifically — LLMs
# (especially ChatGPT) overuse it as a sentence-connector. Recruiters have
# noted this pattern. Rewrite as period, semicolon, colon, comma, or parens
# depending on context.
#
# Note: en-dash (–, U+2013) is NOT banned — it's correct usage for date ranges
# ("2020–2024") and number ranges. Only em-dash (—, U+2014) is the AI tell.
BANNED_PUNCTUATION: list[str] = [
    "—",  # em-dash (—)
]

# CAR framework prose — referenced by the apply prompt's Voice section.
# Composer doesn't enforce this; it's authoring guidance that lives near the
# composer so updates stay in sync.
CAR_FRAMEWORK_RULE = """\
Every bullet follows CAR (Context → Action → Result):
- Context: the situation or constraint (1 short phrase; implicit if obvious)
- Action: what YOU specifically did (concrete verb, named system, specific scale)
- Result: the outcome — a number, a delivered system, a measurable change

Examples that pass:
  "Moved a defecting source across two hostile borders in under 36 hours by
   improvising a vehicle swap and a forged transit manifest, with zero contact
   loss."

  "Cut new-recruit firearms qualification failures from 40% to under 10% across
   two cohorts by rebuilding the range curriculum around live-stress drills."

Examples that fail:
  "Spearheaded security initiatives across the organization"
    (banned verb, no concrete result)
  "Leveraged cutting-edge methods for robust outcomes"
    (3 banned terms, zero specifics)
"""

# Sentence-length-variance rule — same idea, lives here so apply.md can pull it.
SENTENCE_LENGTH_RULE = """\
Mix sentence lengths deliberately. Uniform 18-22 word bullets are the biggest
stylometric AI tell.
- A few crisp 4-8 word fragments mixed in.
- Most bullets 12-25 words with clear CAR structure.
- Occasional 30-40 word clauses with enumerated actor/system/scale details.
"""
