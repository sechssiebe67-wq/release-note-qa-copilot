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
- Diff two releases to highlight regressions and repeated claims.
- Export Markdown for Confluence, GitHub Releases or e-mail, and the QA flags,
  checklist and diff as CSV / JSON for spreadsheets and tooling.

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
| `DATA_DIR` | unset | Enable draft history and durable review/audit logs below this directory |

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Liveness/readiness probe (`ok`) |
| GET | `/api/version` | Product name and version |
| GET | `/api/sample?format=csv\|json` | Example import payload |
| GET | `/api/config/sample` | Example workspace-rules config |
| GET | `/api/profiles` | Built-in QA rule profiles (`name`, `description`, `config`) |
| GET | `/api/trend/sample` | Example 3-release trend payload |
| GET | `/api/export/qa.csv` | QA flags as CSV (`entry_id,code,severity,message`) |
| GET | `/api/export/checklist.csv` | QA checklist as CSV (`label,status,detail`) |
| GET | `/api/export/diff.csv` | Release diff as CSV (`category,id,title,type,reason`) |
| GET | `/api/export/rules.csv` | Effective rule summary as CSV (`code,severity,count`) |
| GET | `/api/export/rules.json` | Effective rule summary as JSON |
| GET | `/api/export/trend.csv` | Multi-release trend as CSV |
| GET | `/api/export/trend.json` | Multi-release trend as JSON |
| GET | `/api/export/draft.json` | Full draft payload as JSON |
| GET | `/api/history` | Stored draft summaries (when `DATA_DIR` is set) |
| GET | `/api/history/<id>` | One stored draft including Markdown |
| GET | `/api/review/<draft_id>` | Current review status plus the audit trail |
| POST | `/api/generate` | Build a draft |
| POST | `/api/diff` | Compare a previous and a current draft |
| POST | `/api/review` | Record an approve / reject / reset transition |
| POST | `/api/trend` | Analyse an ordered list of 2–20 releases |

`POST /api/generate` body:

```json
{
  "format": "auto",
  "title": "Release 2.0",
  "content": "<csv or json text>",
  "profile": "strict",
  "config": {}
}
```

`profile` and `config` are both optional. Response contains `markdown`, `qa`
(flags + counts + `publishable`), `checklist`, the grouped `entries`, the
effective `config`, the resolved `profile` (or `null`), a `rules` summary, and
a `draft_id` when history is enabled.

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

All fields are optional. Unknown keys, non-string list entries and invalid
severities are rejected with HTTP 400 and no draft is stored. With no `config`
the built-in rules and severities are unchanged.

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
| `strict` | Re-ranks `vague_language` to `high` and requires a `breaking` section. |
| `marketing-safe` | Bans common hype phrases (`world's best`, `best-in-class`, `game-changing`, `guaranteed`, `revolutionary`, `cutting-edge`). |
| `internal` | Allows internal shorthand terms (`misc`, `stuff`, `things`, `various`, `several`). |

An unknown profile returns HTTP 400 naming the profile and stores no draft. The
response echoes the resolved profile under `profile` and adds a `rules` object
with the effective vague terms, banned phrases, required sections, severity
overrides and the number of flags that fired per code. Download it with:

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
`previous` and `current` for `diff.csv` and an optional `profile`. They answer
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
