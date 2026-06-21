# SleepLab 2.0 beta.2 plan & triage

Planning doc for the next **small** beta after `v2.0.0-beta.1`. beta.2 stays a
hardening milestone: fix what beta.1 feedback surfaces, smooth install/docs
rough edges, and land low-risk polish. It is **not** a feature milestone.

Status when this was written: `v2.0.0-beta.1` is tagged, pushed, and CI-green;
the private-card soak passed; upstream reconciliation is documented
([`sleeplab_2_upstream_reconciliation.md`](sleeplab_2_upstream_reconciliation.md)).

## Working constraints

- Stay on `develop/2.0`. Milestones are annotated tags, not branches (see
  [`../AGENTS.md`](../AGENTS.md)). Only create short-lived `work/*` branches for
  focused fixes.
- **Do not** merge, rebase, or cherry-pick from `upstream/main`, and do not
  reconcile version/release metadata across the 1.4.x and 2.0 lines. Port
  valuable upstream behavior one feature at a time against the 2.0 data model.
  See [`sleeplab_2_upstream_reconciliation.md`](sleeplab_2_upstream_reconciliation.md).
- A dedicated upstream 2.0 target branch is still an open question with Josh
  (raised on [PR #143](https://github.com/joshuamyers-dev/sleeplab/pull/143)).
  Don't propose a large 2.0→upstream PR until that's settled.

## Focus areas

### 1. Post-beta bug fixes

- Triage issues filed at <https://github.com/kilo-WATT/sleeplab/issues> against
  beta.1. Prioritize anything that can corrupt, duplicate, or silently
  misclassify imported data, then import failures, then display bugs.
- Keep the parser-enabled Linux CI matrix green; add a regression test for every
  confirmed import bug before fixing it.

### 2. Install / docs rough edges

Concrete drift found while preparing beta.1 feedback docs. Most are doc-only:

- **README self-hosting section vs. real compose layout.** The repo ships
  `compose.yaml` (minimal), `compose.advanced.yaml` (documented), and
  `.env.example`. The README's "Copy-Paste" compose block and surrounding prose
  still describe a single hand-rolled stack that exposes the API on `8000` and
  sets `OPENAI_API_KEY`/`API_URL` inline; the real `compose.yaml` exposes only
  `8080` and parameterizes far fewer vars. Reconcile the README example with the
  shipped compose files (or point at them instead of duplicating).
- **Quick Start manual-migration list is stale.** The README lists running
  `migrations/001`–`005` by hand, but 2.0 auto-applies migrations at API startup
  and the tree now goes well past `005` (2.0 uses `022`–`032`). The manual psql
  steps should be removed or clearly marked as legacy/advanced.
- **User Guide still describes DATALOG-folder selection.**
  [`user-guide.md`](user-guide.md) §2 tells users to "Select DATALOG Folder",
  which predates the 2.0 SD-card-**root** + cpap-parser flow. Update it to match
  the root `/upload/source/*` path and mention the legacy fallback.
- **mkdocs nav.** The `docs/sleeplab_2_*.md` planning/release docs (including the
  beta.1 release notes) are not in [`../mkdocs.yml`](../mkdocs.yml) `nav`. Decide
  which, if any, belong on the published site (at least the release notes).
- **Prettier drift.** `README.md` and `CHANGELOG.md` are not prettier-clean.
  Avoid a noisy full-file reformat mid-beta; consider a one-time
  `prettier --write` pass on docs as its own commit if/when desired.

### 3. Import troubleshooting

- Expand the README/User Guide troubleshooting sections for the parser path:
  what `GET /config` should report, the HTTP 503 "parser runtime absent" finish
  error, and the HTTP 409 mixed-history / DATALOG-in-parser-mode responses.
- Make failure messages in Import History actionable (already human-readable;
  link them to the matching troubleshooting entry where possible).

### 4. Small UI polish

- Low-risk, self-contained fixes only — Import History/result-card wording,
  empty/older-row rendering, Event Inspector and full-night waveform affordances.
- No major dashboard rewrite (see non-goals).

### 5. Possible manual port: per-user adherence visibility toggle

Upstream commit `0065f54` adds a per-user adherence visibility toggle. It's the
one upstream-only change flagged as a useful later candidate. If adopted in
beta.2, **reimplement** it against the 2.0 data model, permissions, and UI with
dedicated tests — do **not** cherry-pick it. Treat as optional/stretch; drop it
if beta feedback fills the milestone.

### 6. Dependency review (separate focused work)

Evaluate dependency updates individually on `develop/2.0` and regenerate
lockfiles in this branch when an update is accepted. Do **not** import an
upstream lockfile. Keep this as its own focused effort/PR, not bundled with bug
fixes or docs.

## Non-goals (out of scope for beta.2)

Same hard limits as beta.1 — no large features:

- No SpO2 / oximetry persistence on the parser path.
- No Lowenstein.
- No Philips / DreamStation.
- No Apple Health / wearable expansion.
- No major dashboard rewrite.
- No upstream merge/rebase/cherry-pick; no new milestone tag until scope lands.

## Exit sketch (non-binding)

beta.2 is ready to tag when: filed beta.1 bugs are triaged and the
data-integrity ones fixed with regression tests; the install/docs drift above is
resolved or explicitly deferred with reasons; parser-enabled CI is green; and no
new import path can corrupt or silently misclassify existing data.
