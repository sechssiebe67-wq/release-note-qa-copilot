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
| GET | `/api/export/qa.csv` | QA flags as CSV (`entry_id,code,severity,message`) |
| GET | `/api/export/checklist.csv` | QA checklist as CSV (`label,status,detail`) |
| GET | `/api/export/diff.csv` | Release diff as CSV (`category,id,title,type,reason`) |
| GET | `/api/export/draft.json` | Full draft payload as JSON |
| GET | `/api/history` | Stored draft summaries (when `DATA_DIR` is set) |
| GET | `/api/history/<id>` | One stored draft including Markdown |
| GET | `/api/review/<draft_id>` | Current review status plus the audit trail |
| POST | `/api/generate` | Build a draft |
| POST | `/api/diff` | Compare a previous and a current draft |
| POST | `/api/review` | Record an approve / reject / reset transition |

`POST /api/generate` body:

```json
{ "format": "auto", "title": "Release 2.0", "content": "<csv or json text>" }
```

Response contains `markdown`, `qa` (flags + counts + `publishable`),
`checklist`, the grouped `entries`, the effective `config`, and a `draft_id`
when history is enabled.

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
`previous` and `current` for `diff.csv`. They answer with `text/csv` or
`application/json` and a `Content-Disposition: attachment; filename=...`
header; missing or invalid parameters return HTTP 400 and unparseable content
returns HTTP 422.

| Endpoint | CSV columns / JSON shape |
| --- | --- |
| `GET /api/export/qa.csv` | `entry_id,code,severity,message` — one row per flag. |
| `GET /api/export/checklist.csv` | `label,status,detail` — one row per checklist item. |
| `GET /api/export/diff.csv` | `category,id,title,type,reason` — one row per item, `category` ∈ `added`, `changed`, `removed`, `unchanged`, `repeated_claims`, `regressions`. |
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
