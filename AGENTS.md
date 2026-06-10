# SleepLab Agent Rules

## Active branch

All SleepLab 2.0 work happens on:

`develop/2.0`

Do not create new alpha branches.

Alpha and beta milestones are annotated git tags, not branches.

Current milestones:

- `v2.0.0-alpha.1`
- `v2.0.0-alpha.2`
- `v2.0.0-alpha.3`
- `v2.0.0-alpha.4`
- `v2.0.0-alpha.5`

Future milestones should continue as tags:

- `v2.0.0-alpha.6`
- `v2.0.0-alpha.7`
- `v2.0.0-beta.1`

## Branch rules

Use `develop/2.0` for normal SleepLab 2.0 development.

Do not use tool-specific branch names like:

- `codex/...`
- `claude/...`

Do not create a new branch for every alpha.

Only create short-lived branches for focused experiments or fixes, such as:

- `work/import-history-cleanup`
- `work/parser-regression-tests`
- `work/device-profile-model`

Short-lived branches should be merged back into `develop/2.0`.

## Folder rules

Do not create tool-specific folders such as:

- `codex/`
- `claude/`
- `codex-notes/`
- `claude-notes/`

Project documentation belongs in `docs/`.

Existing SleepLab 2.0 docs currently include:

- `docs/sleeplab_2_data_architecture.md`
- `docs/sleeplab_2_loader_and_conformance_plan.md`
- `docs/sleeplab_2_release_roadmap.md`

New SleepLab 2.0 planning docs may either follow the existing `docs/sleeplab_2_*.md` naming pattern or live under:

- `docs/sleeplab-2.0/`

Agent coordination notes go in:

- `dev-notes/`

Do not scatter planning docs across random temp folders.

## Before editing

Before making changes, run:

```bash
git status
git branch --show-current
git remote -v
git log --oneline --decorate -8