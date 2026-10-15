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
| Export | Markdown download; JSON API for automation |
| History | Optional file-backed store of generated drafts (`DATA_DIR`) |
| API | `GET /healthz`, `GET /api/sample`, `GET /api/history`, `POST /api/generate` |

The UI is a single page (`releasenote/static/index.html`): paste or load sample
data, generate, inspect QA flags and copy/download the Markdown.

## Architecture

```
releasenote/
  models.py     ChangeEntry, Flag, QAResult, canonical groups
  importer.py   CSV/JSON parsing + header aliasing
  grouping.py   bucketing by change type
  qa.py         heuristic QA rules and checklist
  generator.py  Markdown rendering with inline QA annotations
  service.py    parse -> group -> qa -> render pipeline
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

A draft is **publishable** once no high-severity flag remains.

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
- Team-specific term and tone rules configurable per workspace.
- Optional LLM assistance for benefit wording that still cites a source.
- Diff two releases to highlight regressions and repeated claims.
- Multi-user roles (author / QA reviewer / approver) and audit log.

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
