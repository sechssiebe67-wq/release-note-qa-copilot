# Release Note QA Copilot

Turn a changelog or ticket export (CSV / JSON) into a **reviewable release-note
draft** where every core statement is linked to a source and vague or
unsupported claims are flagged before publishing.

- Group changes into Features, Fixes, Breaking Changes and Other.
- Generate a Markdown draft with a customer benefit per entry.
- Trace each entry back to its source/ticket.
- Flag missing sources, vague wording, unsupported numbers and undocumented
  breaking changes.
- Get a short QA checklist and a publishability verdict.
- Tailor the QA rules per workspace (extra/allowed terms, banned phrases,
  required sections, severity overrides).
- Attach a named, reusable **QA rule profile** and see which rules were
  effective and which fired.
- Render the draft with a **release-note template** (`default`,
  `github-release`, `email` or inline) that controls section order, an optional
  intro/outro and which sections are required.
- Diff two releases to highlight regressions and repeated claims.
- Compare **2–10 candidate drafts** side by side: per-draft QA metrics, a
  per-entry presence matrix (`identical` / `changed` / `unique`), a shared vs.
  unique breakdown and a deterministic publishability recommendation.
- Compare the **same** changelog under every QA rule profile side by side: one
  row per profile with its metrics and delta against a baseline, plus a
  deterministic recommendation of the best profile to adopt.
- Get a single **release readiness score** (0–100) with a `ready` / `review` /
  `risky` band and an explainable penalty breakdown, plus an optional
  `min_score` / `max_high` **gate policy** that turns the score into a
  deterministic `pass` / `fail` ship decision.
- Export Markdown for Confluence, GitHub Releases or e-mail, and the QA flags,
  checklist and diff as CSV / JSON for spreadsheets and tooling.
- Hand a draft to a colleague with a read-only share link (`/s/<draft_id>`)
  instead of copy-pasting Markdown or re-uploading the source.
- Find a past draft again with a searchable **archive**: filter by title/entry
  title, tag, review status, score band and date, and organise drafts into sets
  with free-form tags kept in an append-only tag log.
- Track how a draft evolves with **draft versioning**: generating again with the
  same `draft_key` appends an immutable version, and you can list versions, diff
  two of them and roll back to a previous one.
- Lint the writing itself with the offline **language lint**: readability
  (Flesch reading ease, sentence length, syllables, long-sentence ratio), style
  findings (passive voice, first-person slips, ALL-CAPS, emoji, jargon, mixed
  tense, duplicate headings) and a bounded penalty folded into the release
  readiness score.
- Discuss a draft **per entry**: leave threaded comments attached to the
  specific claim, reply, mark a thread resolved and download the discussion as
  CSV/JSON, with an unresolved count folded into the draft payload.

See [CONCEPT.md](CONCEPT.md) for the product goal, users and roadmap.

## Run locally

No dependencies beyond Python 3.8+ are required.

```sh
python3 -m releasenote
# open http://127.0.0.1:8080
```

Configuration:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | Listen address |
| `PORT` | `8080` | Listen port |
| `DATA_DIR` | unset | Enable draft history plus durable review/audit, archive tag, draft version and per-entry comment logs below this directory |

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Liveness/readiness probe (`ok`) |
| GET | `/api/version` | Product name and version |
| GET | `/api/sample?format=csv\|json` | Example import payload |
| GET | `/api/config/sample` | Example workspace-rules config |
| GET | `/api/profiles` | Built-in QA rule profiles (`name`, `description`, `config`) |
| GET | `/api/templates` | Built-in release-note templates (`name`, `description`, `sections`, `intro`, `outro`) |
| GET | `/api/trend/sample` | Example 3-release trend payload |
| GET | `/api/compare/sample` | Example 2-draft comparison payload |
| GET | `/api/compare-profiles/sample` | Example one-changelog profile-comparison payload |
| GET | `/api/language/sample` | Example input payload that exercises the language lint |
| GET | `/api/export/language.csv` | Language findings as CSV (`code,severity,entry_id,message`) |
| GET | `/api/export/language.json` | Full `language` object as JSON |
| GET | `/api/export/comments.csv` | Per-entry comments as CSV (`entry_id,comment_id,parent_id,author,created_at,resolved,body`), optional `draft_id` filter |
| GET | `/api/export/comments.json` | Comment thread payload as JSON, optional `draft_id` filter |
| GET | `/api/export/qa.csv` | QA flags as CSV (`entry_id,code,severity,message`) |
| GET | `/api/export/checklist.csv` | QA checklist as CSV (`label,status,detail`) |
| GET | `/api/export/diff.csv` | Release diff as CSV (`category,id,title,type,reason`) |
| GET | `/api/export/rules.csv` | Effective rule summary as CSV (`code,severity,count`) |
| GET | `/api/export/rules.json` | Effective rule summary as JSON |
| GET | `/api/export/template.csv` | Resolved template as CSV (`section,title,required,entry_count`) |
| GET | `/api/export/template.json` | Resolved template with per-section entry counts as JSON |
| GET | `/api/export/trend.csv` | Multi-release trend as CSV |
| GET | `/api/export/trend.json` | Multi-release trend as JSON |
| GET | `/api/export/comparison.csv` | Draft comparison per-draft metrics as CSV |
| GET | `/api/export/comparison.json` | Full draft comparison report as JSON |
| GET | `/api/export/comparison-matrix.csv` | Draft comparison presence matrix as CSV |
| GET | `/api/export/profile-comparison.csv` | Per-profile comparison metrics as CSV |
| GET | `/api/export/profile-comparison.json` | Full profile comparison report as JSON |
| GET | `/api/export/score.csv` | Release readiness score penalties as CSV (`code,severity,count,points`) |
| GET | `/api/export/score.json` | Full `score` object as JSON |
| GET | `/api/export/archive.csv` | Draft archive listing as CSV (`draft_id,title,created_at,status,band,entry_count,tags`) |
| GET | `/api/export/archive.json` | Full draft archive listing as JSON |
| GET | `/api/export/versions.csv` | Draft version listing as CSV (`version,draft_id,created_at`) |
| GET | `/api/export/versions.json` | Full draft version listing as JSON |
| GET | `/api/export/version-diff.csv` | Version diff as CSV (`version,category,id,title,type,reason`) |
| GET | `/api/export/draft.json` | Full draft payload as JSON |
| GET | `/api/history` | Stored draft summaries (when `DATA_DIR` is set) |
| GET | `/api/history/<id>` | One stored draft including Markdown |
| GET | `/api/review/<draft_id>` | Current review status plus the audit trail |
| GET | `/api/comments/<draft_id>` | Per-entry comment thread plus summary |
| GET | `/api/archive` | Search/filter/paginate the stored draft archive (needs `DATA_DIR`) |
| GET | `/api/archive/<draft_id>` | One archived draft’s metadata, tags and tag audit trail |
| GET | `/api/archive/sample` | Example archive listing for the UI and tests |
| GET | `/api/versions/<draft_key>` | List versions of a logical draft in ascending order (needs `DATA_DIR`) |
| GET | `/api/versions/<draft_key>/<version>` | One stored version payload (needs `DATA_DIR`) |
| GET | `/api/share/<draft_id>` | Read-only JSON view of a stored draft (needs `DATA_DIR`) |
| GET | `/s/<draft_id>` | Read-only HTML share page for a stored draft (needs `DATA_DIR`) |
| POST | `/api/generate` | Build a draft |
| POST | `/api/diff` | Compare a previous and a current draft |
| POST | `/api/review` | Record an approve / reject / reset transition |
| POST | `/api/comments` | Add a per-entry comment (`{draft_id, entry_id, body, author?, parent_id?}`) |
| POST | `/api/comments/resolve` | Set a comment's resolved flag (`{draft_id, comment_id, resolved}`) |
| POST | `/api/archive/tag` | Add or remove a tag on a stored draft |
| POST | `/api/versions/diff` | Diff two versions of the same logical draft |
| POST | `/api/versions/rollback` | Roll back to a stored version |
| POST | `/api/trend` | Analyse an ordered list of 2–20 releases |
| POST | `/api/compare` | Compare 2–10 candidate drafts |
| POST | `/api/compare-profiles` | Compare one changelog across QA rule profiles |

`POST /api/generate` body:

```json
{
  "format": "auto",
  "title": "Release 2.0",
  "content": "<csv or json text>",
  "profile": "strict",
  "config": {},
  "template": "github-release",
  "draft_key": "release-2.0"
}
```

`profile`, `config`, `template` and `draft_key` are all optional. Response
contains `markdown`, `qa` (flags + counts + `publishable`), `checklist`, the
grouped `entries`, the effective `config`, the resolved `profile` (or `null`),
the resolved `template`, a `rules` summary (which includes the resolved template
and the effective gate), a `language` report (readability, style, findings and
summary; its findings are also merged into `qa.flags`), the `score` (with its
`band` and `penalties`), the `gate` decision (`status` + `reasons`), and a
`draft_id` when history is enabled. When `draft_key` is supplied and history is enabled the payload also
echoes `draft_key` and the assigned `version`. When a `draft_id` exists the
payload additionally carries a `comments` summary (see
[Discussion](#discussion)); drafts without a `draft_id` are unchanged.

## Workspace rules

QA heuristics can be tailored per workspace. Pass an optional `config` object
to `POST /api/generate` (the UI's collapsible *Workspace rules* panel does this
for you). `GET /api/config/sample` returns a valid example.

| Field | Type | Effect |
| --- | --- | --- |
| `extra_vague_terms` | list of strings | Appended to the built-in vague-word list. |
| `allow_terms` | list of strings | Removed from the effective vague list (case-insensitive). |
| `required_sections` | list of strings | Group keys (`feature`, `fix`, `breaking`, `other`) that must be non-empty, else a `missing_required_section` flag (medium, empty `entry_id`). |
| `banned_phrases` | list of strings | Literal phrases that trigger a `banned_phrase` flag (medium) naming the match. |
| `severity_overrides` | object `code → high\|medium\|low` | Re-ranks matching flags. |
| `min_score` | int `0..100` | Gate: fail when the release readiness score is below this. |
| `max_high` | int `>= 0` | Gate: fail when the high-severity flag count exceeds this. |

All fields are optional. Unknown keys, non-string list entries, invalid
severities and invalid gate values are rejected with HTTP 400 and no draft is
stored. With no `config` the built-in rules and severities are unchanged.

```json
{
  "content": "id,type,title,description,benefit,source\nA,fix,Blazing sync,Sync is blazing fast,Quick,A\n",
  "format": "csv",
  "config": {
    "extra_vague_terms": ["blazing"],
    "allow_terms": ["seamless"],
    "required_sections": ["breaking"],
    "banned_phrases": ["world's best"],
    "severity_overrides": {"vague_language": "high"}
  }
}
```

The response echoes the effective config under `config` so reviewers can see
which rules produced each flag. Workspace rules were added in **v0.2.0**.

## QA rule profiles

A **profile** is a named, reusable `WorkspaceConfig` plus a description. Pass
an optional `profile` string to `POST /api/generate` or `POST /api/diff` (in
addition to the existing inline `config`); the profile is applied first and the
inline config is layered on top (list fields are unioned, `severity_overrides`
merge with the inline value winning). `GET /api/profiles` lists the built-ins:

| Profile | Purpose |
| --- | --- |
| `strict` | Re-ranks `vague_language` to `high`, requires a `breaking` section and gates on `min_score = 85` / `max_high = 0`. |
| `marketing-safe` | Bans common hype phrases (`world's best`, `best-in-class`, `game-changing`, `guaranteed`, `revolutionary`, `cutting-edge`). |
| `internal` | Allows internal shorthand terms (`misc`, `stuff`, `things`, `various`, `several`). |

An unknown profile returns HTTP 400 naming the profile and stores no draft. The
response echoes the resolved profile under `profile` and adds a `rules` object
with the effective vague terms, banned phrases, required sections, severity
overrides, the effective gate policy and the number of flags that fired per
code. Download it with:

| Endpoint | Shape |
| --- | --- |
| `GET /api/export/rules.csv` | `code,severity,count` (header only when nothing fired). |
| `GET /api/export/rules.json` | The full `rules` summary. |

Both accept the same `format`, `title` and `content` query parameters as the
other exports plus an optional `profile`, and answer with `text/csv` /
`application/json` and a `Content-Disposition: attachment` header.

```sh
curl -X POST 'http://127.0.0.1:8080/api/generate' \
  -H 'Content-Type: application/json' \
  -d '{"format": "csv", "profile": "strict", "content": "id,type,title,description,benefit,source\nA,fix,Better sync,The sync is improved,A much better experience,A\n"}'
```

```sh
curl -G 'http://127.0.0.1:8080/api/export/rules.csv' \
  --data-urlencode 'format=csv' \
  --data-urlencode 'profile=strict' \
  --data-urlencode $'content=id,type,title,description,benefit,source\nA,fix,Better sync,The sync is improved,A much better experience,A\n'
```

The QA rule profiles and the effective-rule summary were added in **v0.6.0**.

## Release-note templates

A **template** controls the Markdown layout of a draft: which canonical groups
appear, in which order, an optional intro/outro block and which sections are
required. Pass an optional `template` to `POST /api/generate` or `POST
/api/diff` as a built-in **name** or an inline object. `GET /api/templates`
lists the built-ins:

| Template | Layout |
| --- | --- |
| `default` | Canonical order (Features, Fixes, Breaking Changes, Other), no intro/outro. |
| `github-release` | Breaking Changes first, then Features (required), Fixes, Other; `## What's Changed` intro and `Full Changelog` outro. |
| `email` | Fixes, Features, Breaking Changes (empty dropped); greeting intro and `Thanks, / The Release Team` outro. |

An inline template layers over a built-in one (`sections` replaces the order;
the other fields override one by one):

| Field | Type | Effect |
| --- | --- | --- |
| `name` | string | Built-in to use as the base (unknown names fall back to `default` when the object is otherwise valid). |
| `description` | string | Human-readable description echoed back. |
| `sections` | list | Ordered `{"key": "feature"\|"fix"\|"breaking"\|"other", "required": bool}` objects (naked strings are allowed and default to `required: false`). |
| `intro` | string | Markdown block rendered before the sections. |
| `outro` | string | Markdown block rendered after the sections. |

Empty sections are skipped unless `required` is true; a required-but-empty
section renders `_(no entries)_` and raises one **`missing_template_section`**
flag (medium, empty `entry_id`), which honours `severity_overrides`. The
resolved template is echoed under `template` in the draft payload and under
`rules.template`. An unknown name returns HTTP 400 naming it and stores no
draft; an invalid inline template returns HTTP 400.

```sh
curl 'http://127.0.0.1:8080/api/templates'
```

```sh
curl -X POST 'http://127.0.0.1:8080/api/generate' \
  -H 'Content-Type: application/json' \
  -d '{"format": "csv", "template": "github-release", "content": "id,type,title,description,benefit,source\nA,feature,Add SSO,SAML login,Central control,A\n"}'
```

Download the resolved template (one row per section):

```sh
curl -G 'http://127.0.0.1:8080/api/export/template.csv' \
  --data-urlencode 'format=csv' \
  --data-urlencode 'template=github-release' \
  --data-urlencode $'content=id,type,title,description,benefit,source\nA,fix,Fix login,Login no longer fails,Reliable access,A\n'
```

```csv
section,title,required,entry_count
breaking,Breaking Changes,False,0
feature,Features,True,0
fix,Fixes,False,1
other,Other Changes,False,0
```

`GET /api/export/template.csv` and `GET /api/export/template.json` accept the
same `format`, `title`, `content`, `profile` and `template` query parameters as
the other exports (plus `previous`/`current` for a diff) and answer with a
`Content-Disposition: attachment` header. Release-note templates were added in
**v0.10.0**.

## Comparing releases

`POST /api/diff` compares a previous release with the current draft and flags
what changed, what was repeated and where a claim regressed. It accepts the
same `format`, `title` and `config` fields as `/api/generate`, plus `previous`
and `current` (both CSV or JSON text).

```json
{
  "format": "csv",
  "title": "Release 2.0",
  "previous": "id,type,title,description,benefit,source\nA,feature,Bulk export,Export all reports,Cuts weekly effort,REL-1\n",
  "current": "id,type,title,description,benefit,source\nA,feature,Bulk export,Export all reports,Exports every report in one click,\n"
}
```

Response (all lists contain `id`, `title`, `type` and a human-readable
`reason`):

```json
{
  "markdown": "# Release 2.0\n...",
  "qa": { "publishable": false, "counts": { "high": 2, "medium": 1, "low": 0, "total": 3 }, "flags": [] },
  "checklist": [],
  "config": {},
  "diff": {
    "added": [],
    "changed": [{ "id": "A", "title": "Bulk export", "type": "feature", "reason": "Changed fields: benefit, source." }],
    "removed": [],
    "unchanged": [],
    "repeated_claims": [{ "id": "A", "title": "Bulk export", "type": "feature", "reason": "Title and description unchanged from the previous release." }],
    "regressions": [{ "id": "A", "title": "Bulk export", "type": "feature", "reason": "Previously sourced entry no longer has a source or link. Previously publishable entry now has high-severity flag(s): missing_source." }]
  }
}
```

An entry is matched on `id` first, then on its normalized title. The diff adds
these flags to the current draft's `qa` result (and honours
`severity_overrides`):

| Flag | Severity | Meaning |
| --- | --- | --- |
| `regression_unsourced` | high | An entry lost the source/link it had before. |
| `regression_vague_benefit` | medium | A previously non-vague benefit now has a vague term. |
| `repeated_claim` | medium | Reused benefit text or an unchanged title + description. |

Invalid bodies behave like `/api/generate`: malformed JSON or a missing
`previous`/`current` field returns HTTP 400, an unparseable payload returns
HTTP 422, a body over 2 MiB returns HTTP 413, and an invalid `config` returns
HTTP 400 without storing a draft. The release diff was added in **v0.3.0**.

## Exporting QA data

Beyond the Markdown draft, the QA result and diff can be downloaded as CSV or
JSON from the UI (the *Download QA CSV*, *Download checklist CSV*, *Download
JSON* and *Download diff CSV* buttons appear after a successful generate/diff)
or fetched directly from the API. The export endpoints accept the same
`format`, `title` and `content` query parameters as `POST /api/generate`, plus
`previous` and `current` for `diff.csv` and an optional `profile` / `template`.
They answer
with `text/csv` or `application/json` and a
`Content-Disposition: attachment; filename=...` header; missing or invalid
parameters return HTTP 400 and unparseable content returns HTTP 422.

| Endpoint | CSV columns / JSON shape |
| --- | --- |
| `GET /api/export/qa.csv` | `entry_id,code,severity,message` — one row per flag. |
| `GET /api/export/checklist.csv` | `label,status,detail` — one row per checklist item. |
| `GET /api/export/diff.csv` | `category,id,title,type,reason` — one row per item, `category` ∈ `added`, `changed`, `removed`, `unchanged`, `repeated_claims`, `regressions`. |
| `GET /api/export/rules.csv` | `code,severity,count` — one row per firing flag code (header only when nothing fired). |
| `GET /api/export/rules.json` | The effective `rules` summary as JSON. |
| `GET /api/export/template.csv` | `section,title,required,entry_count` — one row per resolved template section. |
| `GET /api/export/template.json` | The resolved template with a per-section `entry_count` as JSON. |
| `GET /api/export/language.csv` | `code,severity,entry_id,message` — one row per language finding. |
| `GET /api/export/language.json` | The full `language` object as JSON (stable key order, UTF-8, no `NaN`). |
| `GET /api/export/draft.json` | The full payload as JSON (stable key order, UTF-8, no `NaN`). |

CSV uses `\n` line endings and quotes any field containing a comma, quote or
newline, so it round-trips through `csv.reader`. Example:

```sh
curl -G 'http://127.0.0.1:8080/api/export/qa.csv' \
  --data-urlencode 'format=csv' \
  --data-urlencode $'content=id,type,title,description,benefit,source\nREL-104,feature,Better dashboard,The dashboard is improved,A much better experience,REL-104\n'
```

```csv
entry_id,code,severity,message
REL-104,vague_language,medium,"Vague wording without measurable meaning: better, improved."
```

The diff export also needs `previous`:

```sh
curl -G 'http://127.0.0.1:8080/api/export/diff.csv' \
  --data-urlencode 'format=csv' \
  --data-urlencode $'previous=id,type,title,description,benefit,source\nA,fix,Bug,Bug,Quick,A\n' \
  --data-urlencode $'current=id,type,title,description,benefit,source\nA,fix,Bug,Bug,Quick,\n'
```

The export capability was added in **v0.4.0**.

## Reviewing a draft

A draft returned by `POST /api/generate` carries a `draft_id` when history is
enabled. Use it to record a human sign-off. The allowed statuses are `pending`,
`approved` and `rejected`.

```sh
curl -X POST 'http://127.0.0.1:8080/api/review' \
  -H 'Content-Type: application/json' \
  -d '{"draft_id": "DRAFT_ID", "status": "approved", "reviewer": "Ada", "comment": "Ship it"}'
```

Response:

```json
{
  "draft_id": "DRAFT_ID",
  "status": "approved",
  "reviewer": "Ada",
  "comment": "Ship it",
  "updated_at": "2026-01-01T12:00:00+00:00",
  "audit": [
    {
      "seq": 1,
      "draft_id": "DRAFT_ID",
      "status": "approved",
      "reviewer": "Ada",
      "comment": "Ship it",
      "updated_at": "2026-01-01T12:00:00+00:00",
      "previous_status": "pending"
    }
  ]
}
```

Fetch the current status and the full trail with:

```sh
curl 'http://127.0.0.1:8080/api/review/DRAFT_ID'
```

An unknown `status` returns HTTP 400 and an unknown `draft_id` returns HTTP
404; neither appends to the audit log. Durable audit records require history to
be enabled: set `DATA_DIR` so reviews land in `<DATA_DIR>/reviews.jsonl` and
`<DATA_DIR>/audit.jsonl` (each transition also gets a monotonically increasing
`seq`). Without `DATA_DIR` the endpoints still work against an in-memory store,
but the records are lost on restart and the UI hides the review controls with a
hint. The review workflow was added in **v0.5.0**.

## Sharing a draft

A draft returned by `POST /api/generate` carries a `draft_id` when history is
enabled. That id doubles as a **stable, read-only share link** so a reviewer can
hand the draft to a colleague without copy-pasting Markdown or re-uploading the
source content.

| Endpoint | Response |
| --- | --- |
| `GET /s/<draft_id>` | A small self-contained HTML page (`text/html; charset=utf-8`): the title, the Markdown inside a `<pre>`, the QA counts, the review badge and a link back to `/`. Inline CSS only — no scripts or external stylesheets. |
| `GET /api/share/<draft_id>` | The same payload as JSON: `{draft_id, title, markdown, qa, checklist, rules, review, created_at}`. |

The stored entries are re-analysed with the **current** QA heuristics and the
workspace config that produced the draft, and the current review status is
merged in. Editing `releasenote/qa.py` therefore changes every share link
without regenerating the draft.

```sh
# Capture a draft_id from /api/generate …
curl -X POST 'http://127.0.0.1:8080/api/generate' \
  -H 'Content-Type: application/json' \
  -d '{"format": "csv", "content": "id,type,title,description,benefit,source\nA,fix,Fix login,Login no longer fails,Reliable access,A\n"}'

# … then share it.
curl 'http://127.0.0.1:8080/api/share/DRAFT_ID'
curl 'http://127.0.0.1:8080/s/DRAFT_ID'      # open this in a browser
```

Share links **require `DATA_DIR`**: without a stored draft there is nothing to
serve, and `GET /api/share/<id>` replies with HTTP 400 explaining that history
is required. An unknown id returns HTTP 404. Both views are **unauthenticated**
(anyone with the URL can read the draft) and **read-only** (there are no
approve / reject controls on the HTML page). Neither call writes to disk. The
feature was added in **v0.8.0** and needs no third-party dependency.

## Draft archive

The history list is flat, so a past draft becomes hard to find once it scrolls
away, and there is no way to group drafts into a coherent set. When `DATA_DIR`
is set, the **draft archive** adds search, filters and free-form tags on top of
the same stored drafts the history and share links use:

```sh
# List the most recent drafts.
curl 'http://127.0.0.1:8080/api/archive'

# Search by title or entry title, filter by tag/status/band/date and paginate.
curl -G 'http://127.0.0.1:8080/api/archive' \
  --data-urlencode 'q=export' \
  --data-urlencode 'tag=launch' \
  --data-urlencode 'status=approved' \
  --data-urlencode 'band=ready' \
  --data-urlencode 'since=2026-01-01T00:00:00+00:00' \
  --data-urlencode 'limit=50' \
  --data-urlencode 'offset=0'

# Tag a stored draft (append to the tag log).
curl -X POST 'http://127.0.0.1:8080/api/archive/tag' \
  -H 'Content-Type: application/json' \
  -d '{"draft_id": "DRAFT_ID", "tag": "launch", "action": "add"}'

# Inspect one draft’s tags and their audit trail.
curl 'http://127.0.0.1:8080/api/archive/DRAFT_ID'

# Download the (filtered) listing.
curl -G 'http://127.0.0.1:8080/api/export/archive.csv' --data-urlencode 'tag=launch'
curl -G 'http://127.0.0.1:8080/api/export/archive.json' --data-urlencode 'q=export'
curl 'http://127.0.0.1:8080/api/archive/sample'
```

Each listing entry carries `draft_id`, `title`, `created_at`, `status`, `band`,
`score`, `entry_count` and `tags`. The listing is ordered by `created_at`
descending (ties broken by `draft_id` ascending) and filters case-insensitively
on `q` (draft title and any stored entry title), exactly on `tag`, `status`
(`pending`/`approved`/`rejected`) and `band` (`ready`/`review`/`risky`) and
inclusively on `since`/`until` (ISO-8601); `limit` defaults to 50 (max 500) and
`offset` paginates without dropping or duplicating rows.

Tags are validated as non-empty, lower-cased slugs of at most 40 characters
matching `[a-z0-9][a-z0-9_-]*`. Every `POST /api/archive/tag` appends exactly
one line to `<DATA_DIR>/archive.jsonl` with a monotonically increasing `seq`,
the `draft_id`, the `tag`, the `action` (`add`/`remove`) and an ISO-8601
`updated_at`. The current tag set is the latest line per `(draft_id, tag)`, so
the log is append-only and the tag history is recoverable; a repeated `add` is
idempotent. `GET /api/archive/<draft_id>` returns the metadata plus `tags` and
the ordered `audit` list.

| Endpoint | Shape |
| --- | --- |
| `GET /api/export/archive.csv` | `draft_id,title,created_at,status,band,entry_count,tags` — one row per listed draft, tags joined by `|`, `\n` line endings and RFC-4180 quoting. |
| `GET /api/export/archive.json` | The full listing as stable UTF-8 JSON (sorted keys, no `NaN`). |

An unknown `draft_id` returns HTTP 404 and appends nothing; an invalid tag or
`action` returns HTTP 400 and appends nothing; a body over 2 MiB returns HTTP
413. Both exports accept the same filter query parameters as `GET /api/archive`
and answer with a `Content-Disposition: attachment` header.

Like share links, the archive **requires `DATA_DIR`**: without a stored draft
there is nothing to search, and every archive endpoint returns HTTP 404 naming
`DATA_DIR`. The browser UI hides an *Archive* section behind a hint in that
case; with history enabled it shows a search box, tag/status/band filters, a
results table with per-row *Open share page* and tag add/remove controls, and
*Download archive CSV* / *Download archive JSON* buttons built in the browser
from the returned listing. The feature was added in **v0.13.0** and needs no
third-party dependency.

## Draft versioning

The review log records state transitions but not the content that was approved,
so there is no way to see how a draft evolved, compare two revisions or revert
to an earlier one. When `DATA_DIR` is set, supplying an optional **`draft_key`**
to `POST /api/generate` treats the draft as a logical document and appends an
**immutable version** every time it is regenerated instead of overwriting it:

```sh
# Generate two versions of the same logical draft.
curl -X POST 'http://127.0.0.1:8080/api/generate' \
  -H 'Content-Type: application/json' \
  -d '{"content": "<csv or json text>", "format": "csv", "draft_key": "release-2.0"}'

# List versions (ascending) and fetch one payload.
curl 'http://127.0.0.1:8080/api/versions/release-2.0'
curl 'http://127.0.0.1:8080/api/versions/release-2.0/1'

# Diff two versions (same six categories as POST /api/diff).
curl -X POST 'http://127.0.0.1:8080/api/versions/diff' \
  -H 'Content-Type: application/json' \
  -d '{"draft_key": "release-2.0", "previous_version": 1, "current_version": 2}'

# Roll back to a version (response carries rolled_back_from).
curl -X POST 'http://127.0.0.1:8080/api/versions/rollback' \
  -H 'Content-Type: application/json' \
  -d '{"draft_key": "release-2.0", "version": 1}'

# Download the version listing / version diff.
curl 'http://127.0.0.1:8080/api/export/versions.csv?draft_key=release-2.0'
curl 'http://127.0.0.1:8080/api/export/versions.json?draft_key=release-2.0'
curl 'http://127.0.0.1:8080/api/export/version-diff.csv?draft_key=release-2.0&previous_version=1&current_version=2'
```

A `draft_key` is a trimmed, non-empty slug of at most 64 characters matching
`[a-z0-9][a-z0-9._-]*` (for example `release-2.0` or `security_patch`). Every
version is appended to `<DATA_DIR>/versions.jsonl` as one line carrying a
monotonically increasing `seq`, the canonical `draft_key`, the integer
`version`, the `draft_id` of the stored draft, an ISO-8601 UTC `created_at` and
the full `payload`; the latest line per `(draft_key, version)` wins when
reading, so the log is append-only.

| Endpoint | Body / query | Response |
| --- | --- | --- |
| `GET /api/versions/<draft_key>` | — | `{draft_key, versions: [{version, draft_id, created_at}]}` ascending; an unknown key yields `[]`. |
| `GET /api/versions/<draft_key>/<version>` | — | The stored version payload. |
| `POST /api/versions/diff` | `{"draft_key", "previous_version", "current_version", "config"?}` | `{previous_version, current_version, diff, flags}`. |
| `POST /api/versions/rollback` | `{"draft_key", "version"}` | The target payload with `rolled_back_from` set to the previously latest version. |
| `GET /api/export/versions.csv` | `draft_key` | `version,draft_id,created_at` — one row per version. |
| `GET /api/export/versions.json` | `draft_key` | The full version listing as stable UTF-8 JSON. |
| `GET /api/export/version-diff.csv` | `draft_key`, `previous_version`, `current_version` | `version,category,id,title,type,reason`, where `version` is `current_version`. |

A malformed key or body returns HTTP 400, an unknown version returns HTTP 404
and the exports set a `Content-Disposition: attachment` header. Versioning
**requires `DATA_DIR`**: without durable history there is nothing to version, so
every versioning endpoint returns HTTP 409 naming `DATA_DIR` and the UI hides
the *Versions* panel behind a hint. With history enabled, the *Draft key* input
next to the generate controls makes a draft versioned and a *Versions* panel
lists each version with *View*, *Diff against latest* and *Roll back* actions
plus *Download versions CSV* / *Download versions JSON* (and *Download version
diff CSV* after a diff). The feature was added in **v0.14.0** and needs no
third-party dependency.

## Trend analysis

A single draft or a pairwise diff cannot answer "is our release quality getting
better or worse over the last N releases?". `POST /api/trend` takes an ordered
list of **2–20** releases (oldest first), QA-checks each with the same rules as
`POST /api/generate`, and returns per-release metrics plus the direction of
travel between consecutive releases. Everything is deterministic and offline.

```json
{
  "releases": [
    { "label": "2026.1", "format": "csv", "content": "id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports,A much better experience,\n" },
    { "label": "2026.2", "format": "csv", "content": "id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports,One-click export saves time,REL-1\n" }
  ],
  "profile": "strict",
  "config": {}
}
```

`profile` and `config` are both optional and resolve exactly like
`POST /api/generate`. The response contains:

```json
{
  "releases": [
    {
      "label": "2026.1",
      "entry_count": 1,
      "counts": { "high": 1, "medium": 1, "low": 0, "total": 2 },
      "publishable": false,
      "sourced_ratio": 0.0,
      "vague_hits": 1,
      "banned_hits": 0
    }
  ],
  "deltas": [
    {
      "from": "2026.1", "to": "2026.2",
      "high": -1, "medium": -1, "total": -2,
      "sourced_ratio": 1.0, "verdict": "improving"
    }
  ],
  "overall": { "verdict": "improving", "best": "2026.2", "worst": "2026.1" },
  "config": {},
  "profile": "strict",
  "rules": {}
}
```

`deltas` has one entry per consecutive pair and `overall` classifies the first
release against the last. The verdict rule is deterministic: compare the `high`
count first, then `total`, then `sourced_ratio`; the first metric that differs
decides (`improving` when strictly better on that metric, `regressing` when
strictly worse), otherwise the verdict is `stable`.

Bad input behaves like the rest of the API: 0, 1 or 21 releases, a missing
`content`, a non-object body or an invalid `config` returns HTTP 400, an unknown
`profile` returns HTTP 400 naming it, and unparseable content returns HTTP 422.

Download the report directly (each repeated `release` is `label:content`,
URL-encoded):

```sh
curl -X POST 'http://127.0.0.1:8080/api/trend' \
  -H 'Content-Type: application/json' \
  -d '{"releases": [
        {"label": "2026.1", "format": "csv", "content": "id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports,A much better experience,\n"},
        {"label": "2026.2", "format": "csv", "content": "id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports,One-click export saves time,REL-1\n"}
      ]}'
```

```sh
curl -G 'http://127.0.0.1:8080/api/export/trend.csv' \
  --data-urlencode 'format=csv' \
  --data-urlencode $'release=2026.1:id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports,A much better experience,\n' \
  --data-urlencode $'release=2026.2:id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports,One-click export saves time,REL-1\n'
```

```sh
curl 'http://127.0.0.1:8080/api/trend/sample'
```

`GET /api/export/trend.csv` (or `trend.json`) accepts the same repeated
`release=label:content` parameters plus an optional `profile` and answers with
`text/csv` / `application/json` and a `Content-Disposition: attachment` header.
The CSV columns are:

```csv
label,entry_count,high,medium,low,total,publishable,sourced_ratio,vague_hits,banned_hits
2026.1,1,1,1,0,2,False,0.0,1,0
```

The browser UI adds a *Trend* section: one release per block (a label line then
its CSV/JSON content, blocks separated by a blank line), a results table with
one row per release, inline SVG sparklines for `total` and `sourced_ratio`, a
verdict badge per delta and the *Download trend CSV* / *Download trend JSON*
buttons. Both buttons are built in the browser from the returned payload and
emit exactly the same bytes as `GET /api/export/trend.csv` / `.json` (Python's
`True`/`False` and `1.0`/`0.0` text, sorted JSON keys). Trend analysis was
added in **v0.7.0**; no third-party dependency is required.

## Comparing drafts

The pairwise diff and the trend cannot answer *"which of these N candidate
drafts is the most publishable, and what is unique to each?"*. `POST
/api/compare` takes **2–10** complete drafts (each `{label, content, format}`,
the same shape as the trend input), QA-checks each with the same rules as
`POST /api/generate`, and returns a deterministic, offline comparison matrix.

```json
{
  "drafts": [
    { "label": "branch-a", "format": "csv", "content": "id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports in one click,Saves time,REL-1\n" },
    { "label": "branch-b", "format": "csv", "content": "id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports in one click,One-click export saves time,REL-1\n" }
  ],
  "profile": "strict",
  "config": {}
}
```

The response contains the per-draft rows (`label`, `entry_count`, `counts`,
`publishable`, `sourced_ratio`, `vague_hits`, `banned_hits` — the same metrics
the trend table uses), the resolved `config` and `profile`, plus:

- `matrix` — one row per distinct entry, keyed by normalized `id` first and
  then normalized title: `{key, id, title, type, presence}`, where
  `presence[label]` is `identical` (byte-identical to the first draft that
  contained it), `changed` (present with different fields) or `unique` (present
  in exactly one draft).
- `shared` — the entry keys present in every draft.
- `unique` — a `label → [entry keys]` mapping of entries that appear only in
  that draft.
- `recommendation` — `{label, reason}`.

The recommendation is deterministic: the draft with the **fewest `high`
flags**, then the **fewest `total` flags**, then the **highest
`sourced_ratio`**, then the earliest input position (the same ordering rule the
trend module uses).

Bad input behaves like the rest of the API: 0, 1 or 11 drafts, a missing
`content`, a non-object draft, a non-object body or an invalid `config` returns
HTTP 400, an unknown `profile` returns HTTP 400 naming it, and unparseable
content returns HTTP 422. None of these stores a draft.

Download the report directly (each repeated `draft` is `label:content`,
URL-encoded):

```sh
curl -G 'http://127.0.0.1:8080/api/export/comparison.csv' \
  --data-urlencode 'format=csv' \
  --data-urlencode $'draft=branch-a:id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports in one click,Saves time,REL-1\n' \
  --data-urlencode $'draft=branch-b:id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports in one click,One-click export saves time,REL-1\n'
```

```csv
label,entry_count,high,medium,low,total,publishable,sourced_ratio,vague_hits,banned_hits
branch-a,1,0,0,0,0,True,1.0,0,0
branch-b,1,0,0,0,0,True,1.0,0,0
```

`GET /api/export/comparison-matrix.csv` returns the presence matrix with the
header `key,id,title,type,<label1>,<label2>,…` — one column per draft label in
input order, one row per matrix entry, cells `identical` / `changed` / `unique`
or empty. `GET /api/export/comparison.json` returns the full report as JSON
(sorted keys, UTF-8, no `NaN`). All three accept an optional `profile` and
answer with a `Content-Disposition: attachment` header.

The browser UI adds a *Compare* section: one draft per block (a label line then
its CSV/JSON content, blocks separated by a blank line), a per-draft metrics
table, the presence matrix, the shared/unique breakdown, a recommendation badge
and the *Download comparison CSV* / *Download comparison JSON* / *Download
matrix CSV* buttons. The buttons are built in the browser from the returned
payload and emit exactly the same bytes as the corresponding export endpoints.
Draft comparison was added in **v0.9.0**; no third-party dependency is
required.

## Comparing QA rule profiles

Choosing a QA rule profile (see [QA rule profiles](#qa-rule-profiles)) used to
be guesswork: a clean draft may be clean because the entries are genuinely good
or because the profile was lenient, and there was no way to see how the *same*
changelog would score under every profile at once. `POST
/api/compare-profiles` fixes that. It parses the changelog once, QA-checks it
under each requested profile (every built-in by default) and returns the
per-profile metrics plus a deterministic recommendation. Everything is offline
and deterministic and reuses the existing importer, QA engine and config
layering.

```json
{
  "format": "csv",
  "content": "id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports in one click,The world's best export experience,REL-1\n",
  "profiles": ["marketing-safe", "internal"],
  "config": {}
}
```

`format`, `profiles` and `config` are all optional. `profiles` must be a list
of built-in profile names (default: `strict`, `marketing-safe`, `internal`);
`config` is an inline workspace config layered on top of every profile. The
response echoes a `baseline` (the first requested profile) and:

```json
{
  "baseline": "marketing-safe",
  "profiles": [
    {
      "name": "marketing-safe",
      "description": "Bans common marketing hype phrases from release notes.",
      "entry_count": 1,
      "counts": { "high": 0, "medium": 1, "low": 0, "total": 1 },
      "publishable": true,
      "sourced_ratio": 1.0,
      "vague_hits": 0,
      "banned_hits": 1,
      "flag_counts": { "banned_phrase": 1 },
      "flag_severities": { "banned_phrase": "medium" },
      "delta": { "high": 0, "total": 0, "sourced_ratio": 0.0 }
    }
  ],
  "recommendation": {
    "profile": "internal",
    "reason": "Fewest high-severity flags (0), fewest total flags (0) and highest sourced ratio (1.00)."
  }
}
```

Each `profiles` row reuses the trend/compare metrics (`entry_count`, `counts`,
`publishable`, `sourced_ratio`, `vague_hits`, `banned_hits`) and adds
`flag_counts` / `flag_severities` per flag code plus a `delta` of its `high`,
`total` and `sourced_ratio` against the baseline. The **recommendation rule**
is deterministic: pick the profile with the fewest `high` flags, then the
fewest `total` flags, then the highest `sourced_ratio`; ties fall back to input
order. The `reason` string names the winning `high`, `total` and
`sourced_ratio` values.

Bad input behaves like the rest of the API: malformed JSON, a missing
`content`, a non-list `profiles` value or an invalid `config` returns HTTP 400,
an unknown profile returns HTTP 400 naming it (and stores no draft), a body over
2 MiB returns HTTP 413 and unparseable content returns HTTP 422.

Sample request and downloads:

```sh
curl 'http://127.0.0.1:8080/api/compare-profiles/sample'

curl -X POST 'http://127.0.0.1:8080/api/compare-profiles' \
  -H 'Content-Type: application/json' \
  -d '{"format": "csv", "content": "id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports in one click,The world'"'"'s best export experience,REL-1\n"}'
```

```sh
curl -G 'http://127.0.0.1:8080/api/export/profile-comparison.csv' \
  --data-urlencode 'format=csv' \
  --data-urlencode 'profile=marketing-safe' \
  --data-urlencode 'profile=internal' \
  --data-urlencode $'content=id,type,title,description,benefit,source\nREL-1,feature,Bulk export,Export all reports in one click,The world'"'"'s best export experience,REL-1\n'
```

```csv
profile,entry_count,high,medium,low,total,publishable,sourced_ratio,vague_hits,banned_hits
marketing-safe,1,0,1,0,1,True,1.0,0,1
internal,1,0,0,0,0,True,1.0,0,0
```

| Endpoint | Shape |
| --- | --- |
| `GET /api/export/profile-comparison.csv` | `profile,entry_count,high,medium,low,total,publishable,sourced_ratio,vague_hits,banned_hits` — one row per profile (header only when the report is empty). |
| `GET /api/export/profile-comparison.json` | The full report as stable JSON (sorted keys, UTF-8, no `NaN`). |

Both exports accept `format`, `content`, an optional repeated `profile` query
parameter and an optional inline `config` (as a JSON query parameter), and
answer with a `Content-Disposition: attachment` header.

The browser UI adds a *Compare profiles* section: a textarea prefilled from
`/api/compare-profiles/sample`, a checkbox list populated from `/api/profiles`,
a *Compare profiles* button, a per-profile results table with the recommended
profile highlighted, and *Download profile comparison CSV* / *Download profile
comparison JSON* buttons built in the browser from the returned payload (so
they need no extra server round-trip). Profile comparison was added in
**v0.11.0**; no third-party dependency is required.

## Release readiness score

Individual QA flags tell a reviewer *what* is risky, but not *how close* a draft
is to shippable. Every draft returned by `POST /api/generate` and
`POST /api/diff` therefore carries a single, deterministic **release readiness
score** from `0` to `100` plus an optional **gate policy** that turns the score
into a `pass` / `fail` ship decision. The score is a pure function of the QA
flags and the entries: the same input always yields the same result.

The score starts at `100` and subtracts a fixed penalty per flag by severity
(`high` = 30, `medium` = 15, `low` = 5), divided by the number of entries so a
one-entry draft is not punished like a fifty-entry one; the result is clamped to
`0..100`. A draft with no flags scores `100`. The payload gains:

```json
{
  "score": {
    "score": 70,
    "band": "review",
    "penalties": [
      { "code": "missing_source", "severity": "high", "count": 1, "points": 30.0 }
    ],
    "gate": { "status": "fail", "reasons": ["score 70 is below min_score 85"] }
  },
  "gate": { "status": "fail", "reasons": ["score 70 is below min_score 85"] }
}
```

`band` is `ready` (`score >= 85`), `review` (`score >= 60`) or `risky`
(`score < 60`). `penalties` has one `{code, severity, count, points}` row per
firing flag code, sorted by `points` descending and then `code`, so the score is
explainable. `publishable` keeps its existing meaning (no high-severity flag
remains).

**Gate policy.** Add the optional fields to the workspace `config` (see
[Workspace rules](#workspace-rules)) to enforce a threshold:

| Field | Type | Effect |
| --- | --- | --- |
| `min_score` | int `0..100` | The gate fails when `score < min_score`. |
| `max_high` | int `>= 0` | The gate fails when the high-severity flag count exceeds `max_high`. |

Both are validated strictly: a non-integer, a negative or `min_score > 100`
returns HTTP 400 and stores no draft. With no policy configured `gate.status` is
`pass` and `reasons` is empty; a failing policy names each violated limit. The
built-in `strict` profile sets `min_score = 85` and `max_high = 0`, while
`marketing-safe` and `internal` set no gate. An inline `config` gate overrides
the profile gate, and `rules.gate` echoes the effective policy.

```sh
curl -X POST 'http://127.0.0.1:8080/api/generate' \
  -H 'Content-Type: application/json' \
  -d '{"format": "csv", "content": "id,type,title,description,benefit,source\nA,fix,Fix login,Login no longer fails,Reliable access,A\n", "config": {"min_score": 80, "max_high": 0}}'
```

Download the score (one row per penalty):

```sh
curl -G 'http://127.0.0.1:8080/api/export/score.csv' \
  --data-urlencode 'format=csv' \
  --data-urlencode 'profile=strict' \
  --data-urlencode $'content=id,type,title,description,benefit,source\nA,fix,Fix login,Login no longer fails,Reliable access,A\n'
```

```csv
code,severity,count,points
missing_required_section,medium,1,15.0
```

| Endpoint | Shape |
| --- | --- |
| `GET /api/export/score.csv` | `code,severity,count,points` — one row per penalty (header only when there are none). |
| `GET /api/export/score.json` | The full `score` object as stable JSON (sorted keys, UTF-8, no `NaN`). |

Both accept the same `format`, `title`, `content` (or `previous`/`current` for a
diff), `profile`, `config` and `template` query parameters as the other exports
and answer with a `Content-Disposition: attachment` header. Missing or invalid
parameters return HTTP 400, unparseable content returns HTTP 422 and a body over
2 MiB returns HTTP 413.

The browser UI shows the score, a band badge, the top penalties and the gate
status after a successful generate or diff, and adds *Download score CSV* /
*Download score JSON* buttons built in the browser from the payload already on
screen (no extra server round-trip). The release readiness score and gate policy
were added in **v0.12.0**; no third-party dependency is required.

## Language lint

Traceability and hype checks flag *what* is risky; the **language lint** checks
whether the writing itself is readable and consistent. It is deterministic and
offline (fixed sentence splitter, syllable counter, pattern/term/tense lists, no
external packages) and every draft gains a top-level `language` object:

```json
{
  "readability": {
    "flesch_reading_ease": 83.32,
    "avg_sentence_length": 5.0,
    "avg_syllables_per_word": 1.4,
    "long_sentence_ratio": 0.0
  },
  "style": {
    "passive_voice_hits": 1,
    "first_person_hits": 1,
    "all_caps_hits": 0,
    "emoji_hits": 0,
    "jargon_hits": 0,
    "tense_mix": 0
  },
  "findings": [
    {"code": "passive_voice", "severity": "low", "entry_id": "A",
     "message": "Passive voice: are excited."},
    {"code": "first_person", "severity": "low", "entry_id": "A",
     "message": "First-person wording: we."}
  ],
  "summary": {"grade": "acceptable", "counts": {"high": 0, "medium": 0, "low": 2, "total": 2}}
}
```

The readability numbers are computed over entry titles + descriptions +
benefits. `summary.grade` is `clear` (no findings), `acceptable` (up to three)
or `dense` (more). The findings reuse the `qa` flag shape, honour
`severity_overrides` and are **merged into `qa.flags`** alongside the
traceability and hype flags.

| Flag | Severity | Meaning |
| --- | --- | --- |
| `long_sentence` | medium | A sentence of 40 or more words. |
| `passive_voice` | low | A form of "to be"/"get" followed by a past participle. |
| `first_person` | low | First-person wording such as "we", "our" or "I". |
| `all_caps` | low | An ALL-CAPS word of four or more letters (common acronyms ignored). |
| `emoji_in_release_note` | low | Emoji characters in the draft. |
| `jargon` | medium | A term from the fixed jargon list (for example "leverage"). |
| `tense_inconsistency` | low | Benefits mix past and present tense. |
| `duplicate_heading` | medium | Two entries share the same heading/title. |

The release readiness score folds the language findings into a single **bounded**
penalty: two points per finding, capped at twenty, reported as one `language`
penalty row. Adding language findings therefore lowers `score` and can move the
`band` (and a `min_score` gate) while keeping the deduction explainable.

```sh
curl -G 'http://127.0.0.1:8080/api/export/language.csv' \
  --data-urlencode 'format=csv' \
  --data-urlencode $'content=id,type,title,description,benefit,source\nA,fix,Fix login,We are excited to ship this.,Reliable access,A\n'
```

```csv
code,severity,entry_id,message
passive_voice,low,A,Passive voice: are excited.
first_person,low,A,First-person wording: we.
```

`GET /api/language/sample` returns an input payload that exercises every code
and is valid for `POST /api/generate`. The browser UI renders a *Language* panel
after a successful generate with the readability numbers, a grade badge, the
findings table and *Download language CSV* / *Download language JSON* buttons.
The language lint was added in **v0.15.0**; no third-party dependency is
required.

## Discussion

A review comment describes the whole draft; the **per-entry discussion** keeps
feedback attached to the specific claim. When a draft has a `draft_id`
(history enabled), comments can be added per entry, replied to and resolved:

```sh
# Add a comment on one entry.
curl -X POST 'http://127.0.0.1:8080/api/comments' \
  -H 'Content-Type: application/json' \
  -d '{"draft_id": "DRAFT_ID", "entry_id": "REL-1", "body": "Where is the source?", "author": "Ada"}'

# Reply to it.
curl -X POST 'http://127.0.0.1:8080/api/comments' \
  -H 'Content-Type: application/json' \
  -d '{"draft_id": "DRAFT_ID", "entry_id": "REL-1", "body": "See REL-104.", "parent_id": "c1"}'

# Resolve (or reopen) the thread.
curl -X POST 'http://127.0.0.1:8080/api/comments/resolve' \
  -H 'Content-Type: application/json' \
  -d '{"draft_id": "DRAFT_ID", "comment_id": "c1", "resolved": true}'

# Read the thread plus summary.
curl 'http://127.0.0.1:8080/api/comments/DRAFT_ID'

# Download the comments (optionally filtered to one draft).
curl -G 'http://127.0.0.1:8080/api/export/comments.csv' --data-urlencode 'draft_id=DRAFT_ID'
curl -G 'http://127.0.0.1:8080/api/export/comments.json' --data-urlencode 'draft_id=DRAFT_ID'
```

A comment body is trimmed and must be non-empty and at most 4096 characters;
the author defaults to `anonymous`. Unknown bodies/ids return HTTP 400, an
unknown draft or comment returns HTTP 404 and a body over 2 MiB returns HTTP
413. The `comments.jsonl` log is append-only and lives under `DATA_DIR`; without
`DATA_DIR` the store falls back to memory so the API keeps working but nothing
survives a restart.

The draft payload gains a `comments` summary when it has a `draft_id`:

```json
{
  "comments": {
    "total": 2,
    "unresolved": 1,
    "resolved": 1,
    "entries": {
      "REL-1": {"total": 2, "unresolved": 1, "resolved": 1, "participants": ["Ada", "anonymous"]}
    }
  }
}
```

`GET /api/export/comments.csv` emits the header
`entry_id,comment_id,parent_id,author,created_at,resolved,body` (one row per
comment, header only when there are none) and `GET /api/export/comments.json`
returns the full thread payload as stable UTF-8 JSON (sorted keys, no `NaN`).
The browser UI adds a *Discussion* panel after a successful generate that lists
comments grouped by entry with author, timestamp and a resolved badge, a reply
box, a resolve toggle and *Download comments CSV* / *Download comments JSON*
buttons; it is replaced by a `DATA_DIR` hint when history is disabled. The
feature was added in **v0.16.0**; no third-party dependency is required.

## Input format

CSV with a header row, or JSON as a list / `{ "entries": [...] }`. Recognised
columns and common aliases:

| Field | Aliases |
| --- | --- |
| `id` | ticket, ticket_id, key, issue, number |
| `type` | category, kind, change_type |
| `title` | summary, name, headline |
| `description` | body, details, text |
| `benefit` | customer_benefit, value, impact |
| `source` | source_ref, reference, evidence |
| `link` | url |
| `audience` | persona, segment |

`type` accepts `feature`/`enhancement`/`new`, `fix`/`bug`/`patch`,
`breaking`/`deprecation` and `other`/`chore`/`docs`.

## Test

```sh
python3 -m unittest discover -s tests -v
```

## Docker

```sh
docker build -t release-note-qa-copilot .
docker run --rm -p 8080:8080 release-note-qa-copilot
```

The image runs as a non-root user (UID 10001) and works with a read-only root
filesystem.

## Deploy

The Helm chart lives in `deploy/helm/release-note-qa-copilot/`:

```sh
helm install release-note-qa-copilot deploy/helm/release-note-qa-copilot \
  --set image.repository=ghcr.io/OWNER/REPO \
  --set image.digest=sha256:... \
  --set ingress.enabled=true \
  --set ingress.host=release-notes.example.com \
  --set ingress.className=nginx
```

`.github/workflows/factory-release.yml` builds reviewed merges for
`linux/amd64` and `linux/arm64` and publishes the immutable image manifest.

## Pipeline

This repository is developed by the autonomous pipeline documented in
[`.github/README.md`](.github/README.md). Pipeline tests run with
`python3 -m unittest discover -s .github/scripts -p 'test_*.py' -v`.
