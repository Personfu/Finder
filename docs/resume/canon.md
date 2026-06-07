# Resume Canon: Facts of Record

`data/canon.json` is the single source of truth for the immutable facts on your resume: who you
are, where you worked, when, and what credentials you hold. The composer (`scripts/resume_compose.py`)
reads it to build the header, employment skeleton, education, certifications, and activities of
every tailored resume. It is the facts layer; the achievement bullets live separately in the skill
library (see below).

Canon holds PII (phone, address, references' contact info), so `data/canon.json` is gitignored.
The committed `data/canon.example.json` documents the schema with a deliberately broad fictional
persona, James Bond, used throughout these docs as a worked example. Copy the example to
`data/canon.json` and replace every value with your own.

## Schema

`canon.json` is a single JSON object (stdlib `json.load`; no PyYAML, per the project's
stdlib-only constraint). Six top-level sections:

### `personal`

```json
"personal": {
  "name": "James Bond",
  "location": "London, United Kingdom",
  "phone": "+44 20 7946 0007",
  "email_personal": "james.bond@example.com",
  "email_security": "j.bond@universal-exports.example"
}
```

The composer defaults to `email_security` for the contact line and treats `email_personal` as a
fallback. They are just two addresses; rename or repurpose them as you like.

### `education`

A list. Each entry carries school, location, degree, optional concentration, dates, GPA, and
honors:

```json
"education": [
  {
    "school": "University of Geneva",
    "location": "Geneva, Switzerland",
    "degree": "MA Oriental Languages",
    "concentration": "Russian and Japanese",
    "dates": "1991 – 1995",
    "gpa_major": "First Class",
    "honors": ["First Class Honours", "University Pistol Team Captain"]
  }
]
```

For a senior (10+ year) career, education is positioned at the bottom of the rendered resume;
relevance has decayed relative to recent employment. The composer handles ordering.

### `employment`

A list, most recent first. Each entry is the role skeleton (company, location, title, dates); the
achievement bullets for the role live in the skill library, not here.

```json
"employment": [
  { "id": "instructor",
    "company": "Fort Monckton Field Training",
    "location": "Gosport, UK (Hybrid)",
    "title": "Senior Field-Craft & Firearms Instructor",
    "start": "2021-07", "end": "present" },
  { "id": "universal_exports", "...": "..." }
]
```

Dates use `YYYY-MM`. The special value `present` marks a currently-held role.

**`id` is the join key.** Each `employment[].id` must match the `role_id` in the frontmatter of
`data/skill_library/<id>.md`. The Bond example ships four roles, so four IDs and four library
files:

| `employment[].id` | Skill-library file | Role |
|---|---|---|
| `instructor` | `data/skill_library/instructor.md` | Fort Monckton Field Training: Senior Field-Craft & Firearms Instructor |
| `universal_exports` | `data/skill_library/universal_exports.md` | Universal Exports Ltd: Regional Director (commercial cover) |
| `mi6_field` | `data/skill_library/mi6_field.md` | Secret Intelligence Service (MI6): Field Intelligence Officer, 00 Section |
| `royal_navy` | `data/skill_library/royal_navy.md` | Royal Navy: Commander |

When you add a role to `employment`, create the matching `data/skill_library/<id>.md` (or the role
contributes only a header line with no bullets).

### `references`

Optional list of name / title / company / email / phone. The composer omits the References section
unless `--include-references` is passed, so you can keep contact details in canon without printing
them on every resume.

```json
"references": [
  { "name": "Miles Messervy", "title": "Director",
    "company": "Secret Intelligence Service",
    "email": "m@example.gov", "phone": "+44 20 7946 0010" }
]
```

### `certifications`

A list of name / issuer / status. Only `status: "active"` certs surface in the composed resume;
`expired` ones are kept for the record but suppressed.

```json
"certifications": [
  { "name": "Advanced & Evasive Driving Certification",
    "issuer": "Royal Automobile Club", "status": "active" },
  { "name": "Combat Diver Qualification",
    "issuer": "Royal Navy", "status": "active" }
]
```

### `professional_activities`

Clubs, memberships, speaking, awards. Emitted in the Activities section near the bottom.

```json
"professional_activities": [
  { "name": "Blades Club, London", "role": "Member", "dates": "2003 – present",
    "description": "Private members' club; high-stakes bridge and baccarat" },
  { "name": "Royal Yacht Squadron", "role": "Member", "dates": "1998 – present" }
]
```

## How canon feeds the composer

```
data/canon.json                         ← facts (this file; PII; gitignored)
data/skill_library/<id>.md              ← role-tagged achievement bullets (committed)
scripts/resume_compose.py               ← joins the two by employment[].id == role_id
    └─→ composed master markdown        ← header + skills + experience + education + activities
```

The composer reads canon for the structural skeleton and the skill library for the bullet content,
joins them on the role ID, then emits a tailored markdown master for the chosen role archetype. See
[strategy.md](strategy.md) for the full architecture and [README.md](README.md) for day-to-day usage.

## Keeping canon clean

A few habits that keep the facts layer trustworthy as you tailor over time:

- **Pick the conservative, defensible number** when a fact is fuzzy (an acquisition price you half
  remember, a headcount you are estimating). A figure you can stand behind in an interview beats a
  bigger one you cannot source.
- **Pin dates to a single canonical document** and resolve conflicts once, rather than letting each
  tailoring pass reinvent them. AI rewrites are especially prone to hallucinating date ranges;
  trust your earliest authoritative resume for dates, not the latest tailored output.
- **Drop credentials you cannot back up.** If you sat for a class but never passed the exam, it is
  not a certification. Do not list it as "pending" either; leave it off.
- **Resolve, then record.** When you settle a conflicting fact, write the resolution down so a
  future tailoring run does not reintroduce the drift.
