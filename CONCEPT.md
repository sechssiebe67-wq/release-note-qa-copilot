# Release Note QA Copilot — Concept

## Goal

Release Note QA Copilot turns raw changelog and ticket exports into a
**reviewable release-note draft** in which every core statement is linked to a
source and risky wording is flagged before publishing.

Publishers under time pressure tend to write benefits that are vague, not
backed by evidence, or inconsistent across a release. The Copilot does not try
to write marketing copy; it makes the draft **traceable and checkable**:

- group entries into Features, Fixes, Breaking Changes and Other,
- generate a Markdown draft with a customer benefit per entry,
- attach each entry to its source/reference,
- flag vague, unsupported or incomplete claims,
- produce a short QA checklist for release approval,
- export the draft as Markdown (Confluence, GitHub Releases, e-mail) and the
  QA, checklist and diff data as CSV / JSON for spreadsheets and tooling.

## Intended users

- Product and release managers in B2B SaaS teams with weekly to monthly
  releases.
- Support and marketing reviewers who need to sign off on what customers read.
- Developers who want a clear, sourced summary of the changes they shipped.

## Current capabilities

The MVP is a dependency-free Python web application:

| Capability | Detail |
| --- | --- |
| Import | CSV or JSON changelog/ticket exports, flexible header aliases, auto-detect |
| Grouping | Features, Fixes, Breaking Changes, Other |
| Draft | Markdown with grouped sections and a customer benefit per entry |
| Traceability | Source/link rendered next to every entry |
| QA | Flags missing source, vague language, unsupported quantified claims, missing benefit, undocumented breaking changes |
| Checklist | Five pass/warn/fail checks plus a publishability verdict |
| Diff | Compare two drafts: added / changed / removed / unchanged entries, repeated claims and regressions |
| Export | Markdown download plus CSV/JSON downloads of QA flags, checklist, diff, effective rules and the full draft; `GET /api/export/*` for automation |
| History | Optional file-backed store of generated drafts (`DATA_DIR`) |
| Share links | Read-only `GET /s/<draft_id>` HTML page and `GET /api/share/<draft_id>` JSON view of a stored draft, with QA re-run against the current heuristics; requires `DATA_DIR` and is unauthenticated |
| Review workflow | Mark a draft `pending` / `approved` / `rejected` with reviewer + comment; every transition appended to an append-only audit log; in-memory fallback when `DATA_DIR` is unset |
| Workspace rules | Per-workspace JSON config: extra/allowed vague terms, banned phrases, required sections, severity overrides |
| QA rule profiles | Named, reusable rule sets (`strict`, `marketing-safe`, `internal`) that bundle a `WorkspaceConfig` with a description; attach a profile to a draft and get an effective-rule summary |
| Trend analysis | Analyse 2–20 ordered releases: per-release QA counts, publishability, sourced-entry ratio, vague/banned hits, consecutive deltas (`improving` / `stable` / `regressing`) and an overall verdict; render a table with SVG sparklines and export as CSV/JSON |
| Draft comparison | Compare 2–10 complete draft payloads: per-draft QA metrics, a per-entry presence matrix (`identical` / `changed` / `unique`), a shared/unique breakdown and a deterministic publishability recommendation; export as CSV/JSON |
| API | `GET /healthz`, `GET /api/sample`, `GET /api/config/sample`, `GET /api/profiles`, `GET /api/trend/sample`, `GET /api/compare/sample`, `GET /api/export/qa.csv`, `GET /api/export/checklist.csv`, `GET /api/export/diff.csv`, `GET /api/export/draft.json`, `GET /api/export/rules.csv`, `GET /api/export/rules.json`, `GET /api/export/trend.csv`, `GET /api/export/trend.json`, `GET /api/export/comparison.csv`, `GET /api/export/comparison.json`, `GET /api/export/comparison-matrix.csv`, `GET /api/history`, `GET /api/review/<id>`, `GET /api/share/<id>`, `GET /s/<id>`, `POST /api/generate`, `POST /api/diff`, `POST /api/review`, `POST /api/trend`, `POST /api/compare` |

The UI is a single page (`releasenote/static/index.html`): paste or load sample
data, generate, inspect QA flags and copy/download the Markdown. A *QA rule
profile* selector defaults to *none* so current behaviour is unchanged; picking
a profile pre-fills the *Workspace rules* panel. After a successful generate it
also offers *Download QA CSV*, *Download checklist CSV*, *Download JSON*,
*Download rules CSV* and *Download rules JSON*; after a diff it adds *Download
diff CSV*. Those downloads are built in the browser from the payload already on
screen, so they need no extra server round-trip. When a draft has a `draft_id`
the review controls also offer a *Copy share link* button that copies the
read-only `GET /s/<draft_id>` URL. A *Trend* section accepts one
release per block, renders the per-release table, inline SVG sparklines for the
total flag count and sourced ratio and a verdict badge per delta, and exposes
*Download trend CSV* / *Download trend JSON* after a successful analysis. A
*Compare* section accepts 2–10 drafts (one per block, same shape as the trend
input), renders the per-draft metrics, the entry presence matrix, the
shared/unique breakdown and a recommendation badge, and exposes *Download
comparison CSV* / *Download comparison JSON* / *Download matrix CSV*.

## Architecture

```
releasenote/
  models.py     ChangeEntry, Flag, QAResult, canonical groups
  config.py     WorkspaceConfig: validated per-workspace QA rule overrides
  profiles.py   named reusable rule profiles + effective-rule summary
  importer.py   CSV/JSON parsing + header aliasing
  grouping.py   bucketing by change type
  qa.py         heuristic QA rules, workspace overrides and checklist
  diff.py       compare two drafts: classification, repeated claims, regressions
  trend.py      multi-release trend analysis: metrics, deltas, verdicts
  compare.py    draft comparison matrix: metrics, presence, shared/unique, recommendation
  generator.py  Markdown rendering with inline QA annotations
  exporters.py  CSV / JSON rendering of qa, checklist, diff, trend, comparison and full payload
  service.py    parse -> group -> qa -> render pipeline (+ build_diff, build_trend, build_comparison, reviews, shared drafts)
  storage.py    optional append-only draft history + review/audit store
  server.py     stdlib HTTP server + JSON API + read-only share pages
  static/       single-page browser UI (draft, diff, review controls, share link)
tests/          offline unit tests (no network)
Dockerfile      non-root, read-only-friendly image
deploy/helm/    one chart exposing image digest, ingress, probes, PVC
```

No database or external service is required. The server uses only the Python
standard library, which keeps the image small and operating costs minimal.

## QA rules

Each rule is a deterministic heuristic so results are explainable:

- **missing_source** (high): no source or link, so nothing can be traced.
- **quantified_claim_without_source** (high): a number ("50%", "3x", "2 days")
  without a source.
- **vague_language** (medium): unmeasurable terms such as "better", "seamless",
  "various".
- **missing_benefit** (medium): a feature with no customer benefit.
- **missing_description** (medium): an entry that cannot be verified.
- **breaking_without_migration** (medium): a breaking change without migration
  or required-action wording.
- **missing_title** (high): an entry without a title.

Workspace rules add two more flags when configured:

- **banned_phrase** (medium): a literal phrase from `banned_phrases` appears in
  an entry; the message names the matched phrase.
- **missing_required_section** (medium): a canonical group listed in
  `required_sections` has no entries (a draft-level flag with an empty
  `entry_id`).

The release diff adds three more flags on the *current* draft when it is
compared with a previous release:

- **regression_unsourced** (high): an entry that previously carried a source or
  link no longer has one.
- **regression_vague_benefit** (medium): a previously non-vague benefit now
  contains a vague term.
- **repeated_claim** (medium): identical normalized benefit text is reused, or
  an entry's title and description are unchanged from the previous release.

A draft is **publishable** once no high-severity flag remains. Any flag code can
be re-ranked to `high`/`medium`/`low` through `severity_overrides`.

## Workspace configuration

The single fixed set of QA heuristics is useful out of the box, but every team
has its own vocabulary and tone. Release Note QA Copilot therefore supports a
**workspace configuration** that is layered on top of the built-in rules during
analysis, so a team can ban its own marketing words, whitelist terms it uses
legitimately, require certain sections and re-rank flags — without losing the
traceability core.

**Where it is loaded from.** The config is optional and supplied per request.
`POST /api/generate` accepts a `config` object in its JSON body, and the browser
UI collects the same fields from the collapsible *Workspace rules* panel and
sends them on generate. `GET /api/config/sample` returns a documented example
that is valid input for the schema. No server-side file is required; a team that
wants a fixed config can paste it into the request or default it in the UI.

**Schema** (all fields optional; unknown keys are rejected):

| Field | Type | Effect |
| --- | --- | --- |
| `extra_vague_terms` | list of strings | Appended to the built-in `VAGUE_TERMS`. |
| `allow_terms` | list of strings | Removed from the effective vague list (case-insensitive). |
| `required_sections` | list of strings | Canonical group keys (`feature`, `fix`, `breaking`, `other`) that must be non-empty, else a `missing_required_section` flag. |
| `banned_phrases` | list of strings | Literal phrases that trigger a `banned_phrase` flag naming the match. |
| `severity_overrides` | object mapping flag code → `high`/`medium`/`low` | Re-ranks the matching flag. |

Validation is strict: non-object configs, non-string list entries and
non-canonical severities raise `ValueError`, which the API reports as HTTP 400
and never stores a draft. When no config is supplied the built-in rules and
their severities are unchanged.

Example payload:

```json
{
  "extra_vague_terms": ["blazing", "revolutionary"],
  "allow_terms": ["seamless"],
  "required_sections": ["feature", "breaking"],
  "banned_phrases": ["world's best", "guaranteed"],
  "severity_overrides": {"vague_language": "high"}
}
```

This works with the existing traceability model: the response echoes the
*effective* config under `config`, so reviewers can see which rules produced
each flag. The feature ships in **v0.2.0** (Python package and Helm chart).

## Release diff

Publishing a release note is not only about the current draft: reviewers also
need to know what changed since the last one and whether a claim got worse.
`POST /api/diff` (and `releasenote.diff.diff_drafts`) compare a **previous**
draft with the **current** one, entirely offline:

- every current entry is classified as `added`, `changed` or `unchanged`, by
  matching on `id` first and then on the normalized title;
- previous entries with no counterpart are listed as `removed`;
- `repeated_claims` collects entries whose normalized benefit text is reused
  or whose title and description are identical to the previous release;
- `regressions` collects entries that lost their source/link, whose benefit
  turned vague, or that were publishable before but now carry a high-severity
  flag.

The result is a JSON-serialisable object keyed by those six lists, where every
item carries per-entry `id`, `title`, `type` and a human-readable `reason`. The
new QA flags are merged into the current draft's `qa` result and honour
`severity_overrides`, so a workspace that re-ranks `regression_unsourced` sees
the overridden severity in the response. The feature ships in **v0.3.0**
(Python package and Helm chart).

## Export

Reviewers who track findings in a spreadsheet or feed a release into other
tooling need the QA result as data, not just as a rendered page.
`releasenote.exporters` turns the payload returned by `build_draft` /
`build_diff` into CSV and JSON strings with pure functions (no I/O), and the
server exposes them under `GET /api/export/*`:

| Endpoint | Payload | Columns / shape |
| --- | --- | --- |
| `GET /api/export/qa.csv` | `build_draft` | `entry_id,code,severity,message` — one row per QA flag. |
| `GET /api/export/checklist.csv` | `build_draft` | `label,status,detail` — one row per checklist item. |
| `GET /api/export/diff.csv` | `build_diff` | `category,id,title,type,reason` — one row per diff item, `category` ∈ `added`, `changed`, `removed`, `unchanged`, `repeated_claims`, `regressions`. |
| `GET /api/export/draft.json` | `build_draft` | The full payload as JSON (stable key order, UTF-8, no `NaN`). |

The endpoints accept the same `format`, `title` and `content` query parameters
as `POST /api/generate`, plus `previous` and `current` for `diff.csv`. They
reply with `text/csv` or `application/json` and a
`Content-Disposition: attachment; filename=...` header. Missing or invalid
parameters return HTTP 400 and unparseable content returns HTTP 422. CSV uses
`\n` line endings and quotes any field containing a comma, quote or newline, so
it round-trips through standard parsers.

The browser UI builds the same CSV/JSON strings from the payload it already
holds (`Blob` + `URL.createObjectURL`) and shows the download buttons only
after a successful generate or diff. The feature ships in **v0.4.0** (Python
package and Helm chart).

## Review workflow

The QA verdict tells a reviewer *what* is risky, but not *who* signed off on
the draft or when. The review workflow adds a lightweight, offline sign-off
layer on top of the existing draft pipeline and file-backed history:

- A draft has exactly one **review status**: `pending`, `approved` or
  `rejected`. Drafts start as `pending`.
- Recording a transition stores an optional `reviewer` name and `comment` and
  appends one entry to an **append-only audit log**. The audit log is the
  system of record: it keeps the timestamp, the draft id, the previous status
  and the new status of every transition.
- The state and the audit trail are exposed through the API and rendered in
  the UI as a status badge plus *Approve* / *Reject* / *Reset to pending*
  controls.

**Storage.** With `DATA_DIR` set, review state is written to
`<DATA_DIR>/reviews.jsonl` (the latest line per draft wins) and every
transition is additionally appended to `<DATA_DIR>/audit.jsonl`. Each entry
uses `datetime.now(timezone.utc).isoformat()` and a monotonically increasing
`seq`, so ordering is stable even when two transitions share a timestamp.
When `DATA_DIR` is unset the store falls back to in-memory dictionaries: the
API keeps working, but reviews are lost on restart and draft existence cannot
be validated (any draft id is accepted). This is why the durable audit trail
requires history to be enabled.

**API.**

| Endpoint | Body / query | Response |
| --- | --- | --- |
| `POST /api/review` | `{"draft_id": "...", "status": "approved"\|"rejected"\|"pending", "reviewer": "...", "comment": "..."}` | The updated record: `draft_id`, `status`, `reviewer`, `comment`, `updated_at` and the `audit` list. |
| `GET /api/review/<draft_id>` | — | `{"draft_id": ..., "status": ..., "reviewer": ..., "comment": ..., "updated_at": ..., "audit": [ ... ]}`. |

Each audit entry has the shape:

```json
{
  "seq": 1,
  "draft_id": "…",
  "status": "approved",
  "reviewer": "Ada",
  "comment": "Ship it",
  "updated_at": "2026-01-01T12:00:00+00:00",
  "previous_status": "pending"
}
```

An unknown `status` returns HTTP 400 and an unknown `draft_id` returns HTTP
404; neither call appends to the audit log. A known draft that has never been
reviewed reports `pending` with an empty audit list. The endpoints reuse the
same 2 MiB body cap and JSON-object validation as the rest of the API and add
no new dependencies. The feature ships in **v0.5.0** (Python package and Helm
chart).

## QA rule profiles

Workspace configuration (v0.2.0) lets a team express *what* its QA rules are,
but it is passed inline on every request and there is no way to **name**, list
or reuse a known rule set. A reviewer signing off a draft therefore cannot
easily tell whether a clean draft is clean because the rules were lenient or
because the entries were genuinely good.

A **QA rule profile** is a named, reusable bundle of a `WorkspaceConfig` plus a
human-readable `description`. Profiles make the *effective rules* explicit:
the draft payload and the UI show exactly which vague terms, banned phrases,
required sections and severity overrides were active, and how many flags of
each code actually fired.

**Built-in sample profiles.** `releasenote.profiles.BUILTIN_PROFILES` ships
three profiles, each a valid `WorkspaceConfig`:

| Profile | Purpose |
| --- | --- |
| `strict` | Re-ranks `vague_language` to `high` and requires a `breaking` section, so loose wording blocks sign-off. |
| `marketing-safe` | Bans common hype phrases (`world's best`, `best-in-class`, `game-changing`, `guaranteed`, `revolutionary`, `cutting-edge`). |
| `internal` | Allows internal shorthand terms (`misc`, `stuff`, `things`, `various`, `several`) that are acceptable for internal audiences. |

**Attaching and layering.** `POST /api/generate` and `POST /api/diff` accept an
optional `profile` string alongside the existing inline `config`. A profile
must be known; an unknown name returns HTTP 400 naming the profile and stores
no draft. When both are supplied the profile is applied first and the inline
config is layered on top (list fields are unioned, `severity_overrides` merge
with the inline entry winning). `releasenote.profiles.resolve_config` performs
this resolution and returns a validated `WorkspaceConfig`. The response echoes
the resolved `profile` (or `null`) and the merged `config`.

**Effective-rule summary.** Every draft payload gains a `rules` object built by
`releasenote.profiles.rule_summary`: the effective vague-term list, the banned
phrases, the required sections, every severity override, and per flag code the
number of flags that fired (plus the severity each code resolved to). Because
it is derived from the same deterministic heuristics as the flags themselves,
the summary explains *why* a draft was flagged.

**Endpoints.**

| Endpoint | Response |
| --- | --- |
| `GET /api/profiles` | The built-in profiles as `{"profiles": [{"name", "description", "config"}]}`. |
| `GET /api/export/rules.csv` | `code,severity,count` — one row per firing flag code, header only when nothing fired. |
| `GET /api/export/rules.json` | The full `rules` summary for a draft. |

The export endpoints reuse the `format`, `title`, `content` (and `previous` /
`current` for the diff) query contract of the other exports and additionally
accept an optional `profile`. They answer with `text/csv` or
`application/json` and a `Content-Disposition: attachment` header; an unknown
profile returns HTTP 400, missing parameters HTTP 400 and unparseable content
HTTP 422. The UI adds a profile selector and the *Download rules CSV* /
*Download rules JSON* buttons. The feature ships in **v0.6.0** (Python package
and Helm chart).

## Multi-release trend analysis

A single draft answers "is *this* release fine?" and a diff answers "what
changed since the last one?", but a release manager still cannot answer "is our
release quality getting better or worse over the last N releases?" without
exporting CSVs and building a spreadsheet by hand. The trend analysis answers
that question directly and deterministically, entirely offline.

**Goal.** Given an **ordered list of releases**, produce a compact report of
per-release QA metrics plus the direction of travel between consecutive
releases, so a team can see at a glance whether quality is improving, holding
steady or slipping.

**Input.** An ordered list of `2–20` release payloads, each
`{"label": str, "content": str, "format": "auto"|"csv"|"json"}`. Every content
string is parsed with `importer.parse_entries` and QA-checked with
`qa.analyze_entries` under the resolved `config` / `profile`, so a release in
the trend scores exactly like the same content through `POST /api/generate`.

**Output.** A JSON-serialisable report:

```json
{
  "releases": [
    {"label": "2026.1", "entry_count": 2,
     "counts": {"high": 2, "medium": 1, "low": 0, "total": 3},
     "publishable": false, "sourced_ratio": 0.0,
     "vague_hits": 1, "banned_hits": 0}
  ],
  "deltas": [
    {"from": "2026.1", "to": "2026.2", "high": -2, "medium": -1, "total": -2,
     "sourced_ratio": 1.0, "verdict": "improving"}
  ],
  "overall": {"verdict": "improving", "best": "2026.3", "worst": "2026.1"}
}
```

Each release carries its `label`, `entry_count`, `counts` (high/medium/low/
total), `publishable`, `sourced_ratio` (fraction of entries with a source or
link; `0.0` when there are no entries) and the `vague_hits` / `banned_hits`
counts. Each delta is the signed change between two consecutive releases plus a
**verdict**.

**Verdict rule (deterministic).** Compare the `high` count first, then `total`,
then `sourced_ratio`; a release is `improving` if it is strictly better on the
first metric that differs, `regressing` if strictly worse, and `stable` when
every compared metric is equal. The `overall` verdict applies the same rule
from the first to the last release; `best` and `worst` are the releases with
the strongest and weakest metrics.

**Endpoints.**

| Endpoint | Body / query | Response |
| --- | --- | --- |
| `POST /api/trend` | `{"releases": [{"label", "content", "format"}], "profile", "config"}` | The full report plus the resolved `config`, `profile` and the `rules` summary for the last release. |
| `GET /api/trend/sample` | — | A valid 3-release example payload that `POST /api/trend` accepts. |
| `GET /api/export/trend.csv` | repeated `release=label:content` (URL-encoded) + optional `profile` | `text/csv` with the header `label,entry_count,high,medium,low,total,publishable,sourced_ratio,vague_hits,banned_hits`, one row per release. |
| `GET /api/export/trend.json` | same as `trend.csv` | The full trend report as JSON. |

Both exports answer with a `Content-Disposition: attachment` header. The
endpoints reuse the existing 2 MiB body cap, JSON-object validation, HTTP 400
for bad input (including 0, 1 or 21 releases and an unknown profile), and HTTP
422 for unparseable content.

**Browser UI.** The *Trend* section accepts one release per block (a label line
then the CSV/JSON content, blocks separated by a blank line), posts it to
`POST /api/trend`, renders a table with one row per release, inline SVG
sparklines for `total` and `sourced_ratio`, a verdict badge per delta and the
download buttons (built in the browser from the returned payload). The browser
formats the trend cells the way Python does (`True`/`False`, integral floats as
`1.0`/`0.0`, sorted JSON keys) so both downloads are byte-identical to
`GET /api/export/trend.csv` / `.json` for the same input. The feature ships in
**v0.7.0** (Python package and Helm chart). No new third-party dependency is
introduced; the standard library is still the only requirement.

## Draft comparison matrix

**Goal.** A reviewer who handles several drafts at once — a release train with
two parallel branches, or a weekly digest plus a monthly rollup — otherwise has
to open each draft individually, read its QA counts by eye and compare them
mentally. The pairwise diff answers "what changed since the last release?" and
the trend answers "is quality getting better?", but neither answers *"which of
these N candidate drafts is the most publishable, and what is unique to each?"*.
The comparison matrix answers that question directly, deterministically and
entirely offline.

**Input.** A list of **2–10** complete draft payloads, each
`{"label": str, "content": str, "format": "auto"|"csv"|"json"}`, exactly like
the trend input. Every `content` is parsed with `importer.parse_entries` and
QA-checked with `qa.analyze_entries` under the resolved `config` / `profile`, so
a draft scores exactly as it does through `POST /api/generate`.

**Output.** A JSON-serialisable report:

```json
{
  "drafts": [
    {"label": "branch-a", "entry_count": 2,
     "counts": {"high": 0, "medium": 1, "low": 0, "total": 1},
     "publishable": true, "sourced_ratio": 1.0,
     "vague_hits": 1, "banned_hits": 0}
  ],
  "matrix": [
    {"key": "rel-1", "id": "REL-1", "title": "Bulk export", "type": "feature",
     "presence": {"branch-a": "changed", "branch-b": "identical"}}
  ],
  "shared": ["rel-4"],
  "unique": {"branch-a": ["rel-2"], "branch-b": ["rel-3"]},
  "recommendation": {"label": "branch-b", "reason": "…"}
}
```

Each `drafts` row reuses the trend table's metrics: `label`, `entry_count`,
`counts` (high/medium/low/total), `publishable`, `sourced_ratio`, `vague_hits`
and `banned_hits`. Every distinct entry — keyed by normalized `id` first, then
normalized title — becomes one `matrix` row carrying the canonical `id`,
`title`, `type` and a `presence` mapping `label → identical|changed|unique`:
`identical` means the entry is byte-identical to the first draft that contained
it, `changed` means it appears in that draft with different fields, and `unique`
means it appears in exactly one draft. `shared` lists the keys present in every
draft, and `unique` maps each label to the keys that appear only in that draft.

**Recommendation rule (deterministic).** The recommended draft has the fewest
`high` flags, then the fewest `total` flags, then the highest `sourced_ratio`,
then the earliest input position — the same ordering rule the trend module uses.

**Endpoints.**

| Endpoint | Body / query | Response |
| --- | --- | --- |
| `POST /api/compare` | `{"drafts": [{"label", "content", "format"}], "profile", "config"}` | The full report plus the resolved `config` and `profile`. |
| `GET /api/compare/sample` | — | A valid 2-draft example payload that `POST /api/compare` accepts. |
| `GET /api/export/comparison.csv` | repeated `draft=label:content` (URL-encoded) + optional `profile` | `text/csv` with the header `label,entry_count,high,medium,low,total,publishable,sourced_ratio,vague_hits,banned_hits`, one row per draft. |
| `GET /api/export/comparison-matrix.csv` | same as `comparison.csv` | `text/csv` with the header `key,id,title,type,<label1>,<label2>,…` — one column per draft label in input order, one row per matrix entry, cells `identical` / `changed` / `unique` / empty. |
| `GET /api/export/comparison.json` | same as `comparison.csv` | The full report as JSON (stable key order, UTF-8, no `NaN`). |

All three exports answer with a `Content-Disposition: attachment` header. The
endpoints reuse the existing 2 MiB body cap, JSON-object validation and query
contract: HTTP 400 for bad input (including 0, 1 or 11 drafts, a missing
`content`, a non-object draft and an unknown profile), and HTTP 422 for
unparseable content.

**Browser UI.** The *Compare* section accepts one draft per block (a label line
then its CSV/JSON content, blocks separated by a blank line, mirroring the
*Trend* input), posts it to `POST /api/compare`, and renders a per-draft metrics
table, the entry presence matrix, the shared/unique breakdown, a recommendation
badge and the *Download comparison CSV* / *Download comparison JSON* /
*Download matrix CSV* buttons — all built in the browser from the returned
payload. The feature ships in **v0.9.0** (Python package and Helm chart). No new
third-party dependency is introduced; the standard library is still the only
requirement.

## Share links

**Goal.** Release Note QA Copilot exists to turn raw changelog and ticket
exports into a reviewable, sourced draft that a whole release team can inspect.
A reviewer often has to hand a generated draft to a colleague — a support lead,
a marketing editor or an approver — and the previous options were to copy-paste
the Markdown, re-upload the source content or export a file. Share links remove
that friction: the `draft_id` already returned by `POST /api/generate` becomes a
**stable, read-only link** that anyone with the URL can open.

**Intended users.** The same audience as the rest of the product — product and
release managers, support and marketing reviewers, and the developers who wrote
the changes — but with a focus on the *hand-off* step: passing a draft to a
colleague who does not have the source data to hand and should not have to edit
anything.

**How it works.** When history is enabled (`DATA_DIR` set) the draft store keeps
the parsed entries and the resolved workspace config alongside the Markdown. The
share view loads that record, **re-runs** `qa.analyze_entries` and
`profiles.rule_summary` on the stored entries and merges the current review
status, so the page always reflects the heuristics that are live *now* rather
than the flags computed when the draft was generated. Editing the QA rules
changes every share link without regenerating the draft.

**Endpoints.**

| Endpoint | Response |
| --- | --- |
| `GET /s/<draft_id>` | A small self-contained `text/html; charset=utf-8` page: title, QA counts, review badge and the Markdown inside a `<pre>`, with inline CSS and no scripts or external stylesheets. |
| `GET /api/share/<draft_id>` | The same payload as `application/json`: `{draft_id, title, markdown, qa, checklist, rules, review, created_at}`. |

An unknown `draft_id` returns HTTP 404 with a JSON error body, and requesting a
share link while `DATA_DIR` is unset returns HTTP 400 explaining that history is
required. Both calls are pure reads: neither writes to disk. The endpoints reuse
the existing body-size and JSON validation helpers and add no new dependency.

**Limitations.** Share links require `DATA_DIR` (there is nothing to share
without a stored draft), they are **unauthenticated** — anyone with the URL can
read the draft — and they are **read-only**: the HTML page has no approve /
reject controls and the JSON view is a snapshot, not an update endpoint.

**Browser UI.** After a successful generate with a `draft_id`, the review
controls gain a *Copy share link* button that copies `location.origin + '/s/' +
draft_id` using `navigator.clipboard.writeText` with a
`document.execCommand('copy')` fallback, and shows a hint when `DATA_DIR` is
unset. The feature ships in **v0.8.0** (Python package and Helm chart).

## Deployment contract

- Docker image builds for `linux/amd64` and `linux/arm64`, serves HTTP on port
  `8080`, and exposes `GET /healthz`.
- Exactly one Helm chart lives in `deploy/helm/release-note-qa-copilot/`.
- It renders `image.repository@image.digest` when a digest is pinned.
- Ingress is configured through `ingress.enabled`, `ingress.host` and
  `ingress.className`.
- Resource requests/limits, non-root execution and a read-only root filesystem
  are set. Optional persistence (`persistence.enabled`) mounts a PVC at `/data`.
- `.github/workflows/factory-release.yml` builds reviewed merges and publishes
  the immutable image manifest. The Factory manager publishes the chart;
  product workflows never receive infrastructure credentials.

## Next direction

- Confluence / GitHub Releases / e-mail publish integrations behind a review
  gate.
- Multi-user roles (author / QA reviewer / approver) and audit log — the
  status + audit-log half landed in **v0.5.0**; per-user authentication and
  role enforcement are still open.
- Optional LLM assistance for benefit wording that still cites a source.
- Persist workspace rules server-side and version them alongside drafts.
- Profile libraries per organisation, with profile versions attached to a draft
  so a past sign-off can be replayed against the rules that were live then.
- Authenticated, optionally expiring share links with per-recipient access
  control — the read-only public share view landed in **v0.8.0**; access
  control is still open.

Per-workspace term and tone rules (was the first item in this list) landed in
**v0.2.0** — see [Workspace configuration](#workspace-configuration).
Diffing two releases to highlight regressions and repeated claims (was the
second item) landed in **v0.3.0** — see [Release diff](#release-diff).
Machine-readable CSV / JSON export of the QA result and diff landed in
**v0.4.0** — see [Export](#export).
The review workflow and audit trail landed in **v0.5.0** — see
[Review workflow](#review-workflow).
Named, reusable QA rule profiles and the effective-rule summary landed in
**v0.6.0** — see [QA rule profiles](#qa-rule-profiles).
Multi-release trend analysis with sparklines and trend export landed in
**v0.7.0** — see
[Multi-release trend analysis](#multi-release-trend-analysis).
Read-only draft share links for reviewer hand-off landed in **v0.8.0** — see
[Share links](#share-links).
The draft comparison matrix with per-entry presence and a publishability
recommendation landed in **v0.9.0** — see
[Draft comparison matrix](#draft-comparison-matrix).

## Success signals

- Time from changelog import to approved draft.
- Number of flagged claims resolved before publication.
- Share of entries with a verified source.
- Release approval rate on first review.

---

# Product Criteria

## Hard Requirements (MUST)

Every product must satisfy these requirements before the Salesman can approve its development.

- **Web Deployable:** Must run as a web application and be accessible through a browser.
- **Originality:** Must not be a direct copy of an existing product, whether internally developed or commercially available. Existing concepts may be combined or significantly improved to create something unique.
- **Expandable:** Must have an architecture that allows additional features and future improvements.
- **Real User Value:** Must solve a real problem, provide entertainment, improve productivity, or offer another clearly identifiable benefit.
- **Clearly Defined Audience:** Must have at least one identifiable target audience.
- **Standalone:** Must function independently without requiring another AI Product Factory application.
- **Autonomous Operation:** Must operate without continuous human intervention after deployment.
- **Kubernetes Compatible:** Must be containerized and deployable on the existing k3s cluster using Helm and Argo CD.
- **Resource Efficient:** Must stay within defined CPU, RAM, and storage budgets.
- **Security:** Must follow basic security practices, including dependency scanning, safe secret handling, and input validation.
- **Testable:** Must support automated testing and have measurable acceptance criteria.
- **Maintainable:** Must contain readable source code, clear project structure, and sufficient technical documentation.
- **Legal Compliance:** Must respect software licences, copyright, privacy regulations, and applicable laws.
- **Health Monitoring:** Must expose a reliable way to verify whether the deployment is healthy.
- **Unique Product Identity:** Must have a unique repository name, product name, and subdomain.

## Soft Requirements (SHOULD)

These requirements should influence the Salesman's evaluation and ranking.

- **Database Independence:** Should preferably work without a database. A database is allowed when persistent storage adds meaningful value.
- **Monetization Potential:** Should have a realistic path to revenue, such as subscriptions, advertisements, one-time purchases, or paid features.
- **Low Operating Costs:** Should require minimal infrastructure, external APIs, and paid services.
- **Fast MVP Development:** Should ideally have a deployable minimum viable product within a limited development budget.
- **Low Maintenance:** Should require minimal ongoing maintenance and manual administration.
- **Market Differentiation:** Should offer a meaningful advantage over existing alternatives.
- **Growth Potential:** Should be able to attract additional users without a proportional increase in operating costs.
- **User Retention:** Should provide reasons for users to return regularly.
- **Discoverability:** Should be understandable, searchable, and easy to share.
- **Mobile Friendly:** Should work well on smartphones, tablets, and desktop browsers.
- **Accessibility:** Should follow established accessibility principles.
- **Minimal Dependencies:** Should avoid unnecessary external services and vendor lock-in.
- **Measurable Success:** Should provide metrics that help evaluate adoption, reliability, and product value.
- **Reusability:** Should preferably reuse existing components, design patterns, and deployment templates.
- **Innovation Potential:** Should have opportunities for unique features or future experimentation.

## Product Rejection Criteria

The Salesman must reject a product idea if:

- It is merely a clone without meaningful differentiation.
- Its purpose or target audience cannot be explained clearly.
- It cannot run within the available infrastructure budget.
- It requires unavailable credentials, licences, hardware, or external services.
- It introduces unacceptable security, legal, or privacy risks.
- It has no realistic path to a working MVP.
- It requires continuous manual operation.
- It offers no meaningful advantage over an already active product.
- Its projected value does not justify its development and operating costs.

## Product Lifecycle Requirements

Every approved product must support:

1. **Creation:** A dedicated GitHub repository is created.
2. **Development:** The AI Developer implements the MVP.
3. **Review:** Automated testing and AI code review validate the product.
4. **Deployment:** Helm and GitOps configuration deploy the application.
5. **Monitoring:** Operational health and product performance are evaluated.
6. **Iteration:** The product can receive additional features and improvements.
7. **Archival:** The repository is preserved while the deployment and Helm chart are removed.
8. **Reactivation:** An archived product can be rebuilt and deployed again if its potential improves.

The maximum number of simultaneously active product repositories is **10**.
