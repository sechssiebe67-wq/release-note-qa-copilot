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
- export the draft as Markdown (Confluence, GitHub Releases, e-mail).

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
| Export | Markdown download; JSON API for automation |
| History | Optional file-backed store of generated drafts (`DATA_DIR`) |
| Workspace rules | Per-workspace JSON config: extra/allowed vague terms, banned phrases, required sections, severity overrides |
| API | `GET /healthz`, `GET /api/sample`, `GET /api/config/sample`, `GET /api/history`, `POST /api/generate`, `POST /api/diff` |

The UI is a single page (`releasenote/static/index.html`): paste or load sample
data, generate, inspect QA flags and copy/download the Markdown.

## Architecture

```
releasenote/
  models.py     ChangeEntry, Flag, QAResult, canonical groups
  config.py     WorkspaceConfig: validated per-workspace QA rule overrides
  importer.py   CSV/JSON parsing + header aliasing
  grouping.py   bucketing by change type
  qa.py         heuristic QA rules, workspace overrides and checklist
  diff.py       compare two drafts: classification, repeated claims, regressions
  generator.py  Markdown rendering with inline QA annotations
  service.py    parse -> group -> qa -> render pipeline (+ build_diff)
  storage.py    optional append-only draft history
  server.py     stdlib HTTP server + JSON API
  static/       single-page browser UI
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
- Multi-user roles (author / QA reviewer / approver) and audit log.
- Optional LLM assistance for benefit wording that still cites a source.
- Persist workspace rules server-side and version them alongside drafts.

Per-workspace term and tone rules (was the first item in this list) landed in
**v0.2.0** — see [Workspace configuration](#workspace-configuration).
Diffing two releases to highlight regressions and repeated claims (was the
second item) landed in **v0.3.0** — see [Release diff](#release-diff).

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
