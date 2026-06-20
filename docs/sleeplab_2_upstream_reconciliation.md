# SleepLab 2.0 Upstream Reconciliation

## Current relationship

`kilo-WATT/sleeplab` is the fork used for SleepLab 2.0 development, with
`develop/2.0` as its active beta line. `joshuamyers-dev/sleeplab` remains the
upstream repository, but its default `main` branch is effectively the 1.4.x
line and has no corresponding active 2.0 branch.

The branches have diverged substantially since their merge base at `ed2b71a`
(release 1.3.1). At the time of this review, `develop/2.0` has 213 commits not
in `upstream/main`, while `upstream/main` has 37 commits not in
`develop/2.0`. SleepLab 2.0 is currently released as `v2.0.0-beta.1`.

## Why the branches should not be merged

Merging `upstream/main` into `develop/2.0` would combine two independently
evolved release lines and create a large, difficult-to-review conflict set.
Much of upstream's PDF and adherence work is already substantially represented
in 2.0, so a direct merge would also risk duplicated behavior or regressions
without providing a clean feature-level history.

For the same reason, a large pull request from `develop/2.0` to
`upstream/main` is not recommended now. It would ask the upstream 1.4.x line to
absorb hundreds of commits, architectural changes, schema evolution, and beta
work in one review, without an agreed 2.0 integration target.

## Specific conflict risks

- Migration numbers collide: 2.0 uses migrations `022` through `032`, while
  upstream has its own `022` and `023`. Combining them mechanically could
  produce ambiguous or incorrectly ordered database histories.
- Version and release history have diverged. The 1.4.x and 2.0 beta lines
  should not be reconciled by overwriting version metadata or release files.
- Dependency lockfiles describe the complete dependency graph of their branch.
  Transplanting an upstream lockfile would obscure intentional 2.0 dependency
  choices and could introduce unrelated upgrades or downgrades.
- Wholesale cherry-picking has many of the same risks as merging because
  apparently isolated commits may depend on upstream's schema, dependency, or
  application context.

## Candidates for later manual porting

No upstream-only change currently justifies merging the branches. One useful
candidate for a focused later review is upstream commit `0065f54`, which adds a
per-user adherence visibility toggle. If adopted, its behavior should be
reimplemented or manually ported against the 2.0 data model, permissions, and
UI, with dedicated tests. Dependency updates should likewise be evaluated
individually on 2.0 rather than imported through an upstream lockfile.

## Recommended strategy

- Keep `develop/2.0` as the active SleepLab 2.0 beta line.
- Coordinate with Josh before proposing 2.0 upstream so expectations, scope,
  and ownership are explicit.
- Prefer a dedicated upstream 2.0 target branch instead of targeting
  `upstream/main` directly.
- Review dependency changes independently and regenerate lockfiles in the 2.0
  branch when an update is accepted.
- Avoid wholesale merges or cherry-picks from `upstream/main`; assess and port
  valuable upstream behavior one feature at a time.
