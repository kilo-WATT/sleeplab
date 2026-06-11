# SleepLab 2.0 — Safe Validation Inputs

## Why this exists

SleepLab 2.0 only claims a device is **supported** after real fixtures pass
conformance checks against source data and OSCAR reference output (see
`docs/sleeplab_2_release_roadmap.md` and
`docs/sleeplab_2_fixture_validation_matrix.md`). Getting there needs evidence
from real machines — but CPAP data is health data. This note explains how to
help **without posting raw CPAP data publicly**.

Privacy comes first. When in doubt, share less.

## Do not post raw data publicly

- **Do not upload raw CPAP ZIPs, SD-card images, or `DATALOG` folders to GitHub**
  (issues, PRs, gists, or attachments). They contain therapy data and, in the
  identification files, a device serial number.
- Do not paste exact serial numbers, full nightly detail exports, or anything
  that identifies a person.
- This repository does **not** provide an upload location for raw data, and you
  should not ask anyone to post theirs.

## Publicly safe help (open an issue / PR)

These are safe to share in the open and are genuinely useful:

- **Machine brand and model** (e.g. "ResMed AirSense 10 AutoSet",
  "Philips DreamStation").
- **Whether OSCAR imports it**, and the **OSCAR version** you used.
- **Non-sensitive screenshots or copied summary values** — with private details
  (serial, name, location, exact dates) blurred or removed. A redacted daily
  summary (AHI, usage hours, pressure percentiles) is far more useful than a raw
  file and exposes much less.
- **Synthetic fixtures** — hand-built or generator-produced structures that carry
  no real patient or device data. These can be committed directly (see the
  existing `fixtures/conformance/synthetic-resmed-minimal/`).

When you copy summary values, prefer **rounded / high-level** figures and drop
exact timestamps unless they are essential to the bug.

## Trusted/private real fixtures

Real anonymized card data (like the committed
`tests/conformance/fixtures/resmed_airsense10_001/`) is valuable but requires
**separate, private coordination and a privacy review** before it enters the
repository:

- serials replaced with fixture placeholders,
- timestamps shifted by a fixed offset,
- provenance and anonymization documented,
- redistribution permission confirmed by the contributor.

Do not start this process by posting the data. Reach out privately first.

## Devices we most want evidence for

- **ResMed** AirSense 10 / AirSense 11 (extend existing coverage)
- **Philips Respironics** PRS1 / DreamStation
- **Löwenstein** Prisma
- **Fisher & Paykel**
- **BMC** (if available)

## Edge cases that expose real bugs

Conformance gaps usually hide in the unusual nights, not the typical ones:

- multiple mask-on / mask-off sessions in one night
- summary-only days (no detailed `DATALOG`)
- missing or corrupt files
- DST changes, travel, and timezone shifts
- a wrong machine clock
- a device reset
- an SD-card replacement
- duplicate / re-import behavior
- oximetry (SpO₂ / pulse) data

If you have a device that does one of these, just telling us *that it happens*
and *what OSCAR shows* is useful — no raw data required.

## The goal: honest, evidence-based support states

Every device's status should reflect evidence, never optimism:

- **validated** — real fixtures pass conformance vs OSCAR reference
- **partial** — some capabilities validated, others not
- **experimental** — parses, but unverified against reference output
- **detected-only** — recognized, not yet parsed/validated
- **blocked pending fixture evidence** — cannot advance without safe data

Your safe, public contributions move devices up this ladder without anyone's
private therapy data leaving their hands.
