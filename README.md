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
- Export Markdown for Confluence, GitHub Releases or e-mail.

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
| `DATA_DIR` | unset | Enable draft history persisted below this directory |

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Liveness/readiness probe (`ok`) |
| GET | `/api/version` | Product name and version |
| GET | `/api/sample?format=csv\|json` | Example import payload |
| GET | `/api/config/sample` | Example workspace-rules config |
| GET | `/api/history` | Stored draft summaries (when `DATA_DIR` is set) |
| GET | `/api/history/<id>` | One stored draft including Markdown |
| POST | `/api/generate` | Build a draft |

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
