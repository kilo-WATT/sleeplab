# SleepLab 2.0 Loader and Conformance Plan

## Executive summary

`cpap-parser` can reasonably become a shared parsing backend behind SleepLab
adapters, but it is not yet a trustworthy universal backend. Its strongest
assets are a single Python package, a common Pydantic output schema, structural
detectors for eight vendor families, Rust implementations derived from OSCAR
for several binary formats, and an existing OSCAR-comparison harness. Its
largest gaps are lifecycle design, committed fixtures, machine/settings depth,
diagnostics, multi-machine handling, and validated parity outside a narrow
Lowenstein sample.

The present `cpap-parser` `main` branch should be treated as alpha:

- only the Lowenstein Prisma Line profile claims validation;
- the ResMed profile still says `needs_validation`;
- the four ResMed fixes proposed in merge request
  [!8](https://gitlab.com/open-cpap/cpap-parser/-/merge_requests/8) are not in
  `main`, add no regression tests, have an unchecked real-data test plan, and
  have a failed GitLab pipeline;
- real ResMed and Lowenstein cards and OSCAR exports are referenced by local
  paths but are not committed;
- the Philips Respironics adapter recognizes generic EDF layouts rather than
  OSCAR's native PRS1 `P-Series`/`PROP*.TXT`/`PROP.BIN` card format;
- the shared schema has no first-class settings snapshots, source files,
  channel metadata, import warnings, or detector evidence.

The right architecture is therefore not "ResMed versus non-ResMed" and not
"call `cpap-parser.parse()` first, then fall back." SleepLab should own an
OSCAR-style loader registry and normalized contract. A loader may use
`cpap-parser`, SleepLab's native ResMed code, or a future service internally.
Routing should depend on structural evidence, identity, capabilities, policy,
and validation status.

Go forward with `cpap-parser` as a candidate common backend. Keep native ResMed
temporarily as the production path and regression oracle. Validate Lowenstein
Prisma Line first after ResMed because it has the best existing parser and
OSCAR comparison evidence, while independently recognizing that the current
fixture is external and not yet reproducible in CI.

Research baseline:

- SleepLab fork `origin/main` at `37621e9` is 167 commits behind
  `upstream/main` at `ed2b71a`, with no fork-only commits in that comparison.
- Upstream SleepLab PR [#114](https://github.com/joshuamyers-dev/sleeplab/pull/114)
  is open, draft, and not currently mergeable.
- Upstream issue [#38](https://github.com/joshuamyers-dev/sleeplab/issues/38)
  contains the implementation history and links the four ResMed defects to
  `cpap-parser` issue
  [#28](https://gitlab.com/open-cpap/cpap-parser/-/work_items/28).
- This plan was based on current `cpap-parser` commit `37394cf` and OSCAR's
  local `master` checkout.

This branch also contains an executable, persistence-independent prototype in
`importer/loaders/`. It registers structural adapters for ResMed, Philips
Respironics, Lowenstein, Fisher & Paykel, and BMC; reports evidence, identity,
and capabilities; and deliberately leaves full import unimplemented. Run
`python -m importer.loaders <sd-card-or-archive-root>` to inspect a source
without changing production import behavior.

## OSCAR detection lifecycle

OSCAR's lifecycle is distributed across a small base interface, a loader
registry, UI/import orchestration, and an import context:

1. Loader modules instantiate themselves and call `RegisterLoader()`.
   `RegisterLoader()` initializes vendor channels and appends the loader to the
   global registry in `oscar/SleepLib/machine_loader.cpp`.
2. The scanner gets registered loaders with `GetLoaders(MT_CPAP)` and calls
   each loader's `Detect(path)`.
3. A matching loader calls `PeekInfo(path)` to read machine identity before a
   full import. `MachineLoader` defines `Detect`, `PeekInfo`, and `Open` in
   `oscar/SleepLib/machine_loader.h`.
4. The application creates a `ProfileImportContext`, attaches it with
   `MachineLoader::SetContext()`, and connects unexpected-data and
   unsupported/untested-device signals in `oscar/mainwindow.cpp`.
5. `Open(path)` canonicalizes the selection, discovers machine directories,
   creates or looks up machines, parses files, and emits normalized OSCAR
   sessions, settings, channels, events, and waveforms.
6. `ImportContext` provides machine creation, duplicate checks, session
   creation, storage, commit, backup path access, and unexpected-data
   aggregation in `oscar/SleepLib/importcontext.h` and `.cpp`.
7. Machines are identified by serial plus loader. The current database lookup
   is `MachineRepository::findBySerialAndLoader()` in
   `oscar/database/machine_repository.cpp`; profile-level loaders also use
   `Profile::lookupMachine(serial, loadername)`.
8. Vendor loaders map source codes into OSCAR's shared channel registry. The
   normalized channel taxonomy is initialized in `oscar/SleepLib/schema.cpp`,
   while vendor-specific setting channels are registered by each loader's
   `initChannels()`.
9. Parsers call `ImportContext::LogUnexpectedMessage()` (often through
   `CHECK_VALUE`, `CHECK_VALUES`, and `UNEXPECTED_VALUE`) when firmware or
   source values violate known expectations. The context deduplicates messages
   against those previously seen for the machine before alerting the user.

This separation matters. Detection is not parsing, machine identity is not a
session summary, and parser success is not validation.

### OSCAR vendor detection examples

#### ResMed

`ResmedLoader::Detect()` in
`oscar/SleepLib/loader_plugins/resmed_loader.cpp` requires both root
`DATALOG/` and root `STR.edf`. `PeekInfo()` then reads
`Identification.json` (preferred for newer cards) or `Identification.tgt`.
`Open()` accepts either root or `DATALOG`, canonicalizes back to root, requires
identity and `STR.edf`, and looks up the machine by serial plus loader.

This is stronger than `cpap-parser`'s current ResMed detector, which only
requires `DATALOG/` and is disabled entirely when `cpap-py` is unavailable.

#### Philips Respironics PRS1 and DreamStation

`PRS1Loader::Detect()` in
`oscar/SleepLib/loader_plugins/prs1_loader.cpp`:

- finds `P-Series` case-insensitively;
- tolerates the user selecting the `P-Series` directory by trying its parent;
- scans device subdirectories for `PROP*.TXT` or DreamStation 2 `PROP.BIN`;
- returns all machine directories, sorted oldest to newest.

`PeekInfo()` parses serial, model, family, data format, firmware/software
version, keys, and other properties. `Open()` imports every detected machine
and explicitly notes that detection should eventually return the set of
devices rather than surprising the user during `Open()`.

`cpap-parser` does not implement this native PRS1 lifecycle. Its
`RespironicsAdapter` looks for `.edf` files under `p0`, `P0`, `EDF`, or
date-named directories, returns serial `Unknown`, and exposes no daily
summaries.

#### Fisher & Paykel SleepStyle and ICON

`SleepStyleLoader` and `FPIconLoader` in
`oscar/SleepLib/loader_plugins/sleepstyle_loader.cpp` and `icon_loader.cpp`
canonicalize to `FPHCARE/ICON`, find machine directories containing
`SUM*.fph`, and inspect the summary header. The fifth CR-terminated header line
must identify `SLEEPSTYLE` or `ICON`. Directory names provide serial identity,
and multiple machine directories can be imported.

`cpap-parser` implements SleepStyle through
`FisherPaykelAdapter` and Rust `can_handle_fisher_paykel()`, using the same
structural concept. It does not advertise ICON as a separately validated
family and exposes summary/session data only, without events or waveforms.

#### Lowenstein / Prisma

`PrismaLoader::Detect()` in
`oscar/SleepLib/loader_plugins/prisma_loader.cpp` checks for either:

- `config.pscfg` for Prisma SMART; or
- `config.pcfg` for Prisma Line.

`PeekInfoFromConfig()` reads JSON identity for Prisma SMART or opens the
Prisma Line ZIP and reads `mnt/flash/conf/device.xml` for model and serial.
Prisma Line `Open()` also requires `therapy.pdat`, pairs `event_*.xml` with
`signal_*.wmedf`, and creates per-session tasks.

`cpap-parser`'s `LowensteinAdapter` similarly distinguishes
`WM_DATA.TDF` legacy data from `config.pcfg` plus `therapy.pdat` Prisma Line
data. This is currently its best candidate for shared-backend validation.

#### BMC and BMC G3X

Legacy `BmcLoader` delegates detection to
`BmcData::DirectoryHasBmcData()` in `bmcDataParsing.cpp`. It requires a
`.USR` file and matching `.idx` and `.000` files.

`BmcG3xLoader` first excludes legacy `.USR` cards, then calls
`BmcG3xData::DirectoryHasBmcG3xData()` in `bmcG3xDataParsing.cpp`. That logic
scans `.idx` candidates, validates a G3X-specific header, and requires a
matching `.000` waveform file. `PeekInfo()` reads serial, model, and firmware;
`Open()` warns for unknown firmware and models while continuing.

`cpap-parser` has only the legacy three-file BMC adapter. Its docs mention G2/G3
generically, but its Python adapter and Rust source do not provide OSCAR's
separate G3X detector or exclusion rule. SleepLab must not claim BMC G3X
support until that gap is resolved and fixture-tested.

## Proposed SleepLab loader contract

The contract should be owned by SleepLab and versioned independently of parser
implementations. Method names below intentionally mirror OSCAR while using
Python conventions.

```python
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

Confidence = Literal["none", "weak", "probable", "strong", "exact"]
Validation = Literal["unvalidated", "partial", "validated", "failed"]


class LoaderAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def detect(self, source: "ImportSource") -> list["DetectedDevice"]: ...
    def peek_info(
        self, source: "ImportSource", detected: "DetectedDevice"
    ) -> "MachineIdentity": ...
    def capabilities(
        self, source: "ImportSource", detected: "DetectedDevice"
    ) -> "Capabilities": ...
    def import_data(
        self,
        source: "ImportSource",
        detected: "DetectedDevice",
        options: "ImportOptions",
    ) -> "ImportRun": ...
```

### Detect

`detect()` is read-only and bounded. It must not decode all sessions or write
database rows. It returns zero or more candidates because one card can contain
multiple machines.

```python
@dataclass(frozen=True)
class DetectedDevice:
    adapter_id: str
    source_root: Path
    device_path: Path
    manufacturer_hint: str | None
    family_hint: str | None
    confidence: Confidence
    evidence: tuple["DetectionEvidence", ...]
    competing_adapter_ids: tuple[str, ...] = ()
    requires_user_choice: bool = False
```

Detection evidence should be structured, not only a human string:

```python
@dataclass(frozen=True)
class DetectionEvidence:
    kind: Literal[
        "required_path",
        "file_header",
        "filename_pattern",
        "directory_name",
        "identity_record",
        "negative_evidence",
    ]
    relative_path: str
    expected: str
    observed: str
    weight: int
```

### PeekInfo

`peek_info()` reads only identity/configuration material needed to distinguish
machines and select policy. A missing serial must be represented as missing,
not the literal string `"Unknown"`.

```python
@dataclass(frozen=True)
class MachineIdentity:
    manufacturer: str | None
    family: str | None
    model: str | None
    model_number: str | None
    serial_number: str | None
    firmware_version: str | None
    data_format_version: str | None
    loader_identity: str
    identity_confidence: Confidence
    source_fields: dict[str, str]
    warnings: tuple["ImportWarning", ...] = ()
```

Machine lookup should use `(user_id, loader_identity, serial_number)` when a
serial exists. When it does not, SleepLab should create an unresolved machine
candidate keyed by import/source fingerprint and require reconciliation rather
than merging unrelated devices.

### Capabilities

Capabilities report what the adapter can parse for this exact detected device,
not what the package claims in general.

```python
@dataclass(frozen=True)
class CapabilityStatus:
    available: bool
    validation: Validation
    notes: str


@dataclass(frozen=True)
class Capabilities:
    identity: CapabilityStatus
    sessions: CapabilityStatus
    session_blocks: CapabilityStatus
    settings: CapabilityStatus
    events: CapabilityStatus
    low_rate_signals: CapabilityStatus
    waveforms: CapabilityStatus
    oximetry: CapabilityStatus
    summary_only_days: CapabilityStatus
    source_manifest: CapabilityStatus
    timezone_basis: Literal[
        "timezone_aware",
        "machine_local",
        "fixed_offset",
        "assumed_utc",
        "unknown",
    ]
    leak_kinds: tuple[Literal["total", "unintentional", "large_leak", "unknown"], ...]
```

SleepLab route policy is separate from capabilities. A capable but unvalidated
adapter may be allowed in an opt-in test mode and blocked from production
routing.

### Import

`import_data()` returns normalized objects and diagnostics. Persistence is a
separate transaction so conformance tests can compare output without a
database.

```python
@dataclass
class ImportRun:
    run_id: str
    adapter_id: str
    adapter_version: str
    source_fingerprint: str
    started_at: datetime
    completed_at: datetime | None
    status: Literal["running", "complete", "partial", "failed"]
    detected_device: DetectedDevice
    machine: MachineIdentity
    capabilities: Capabilities
    source_files: list["SourceFile"]
    sessions: list["Session"]
    warnings: list["ImportWarning"]
```

### Normalized objects

The initial contract should define these concepts without requiring an
immediate schema migration:

```python
@dataclass(frozen=True)
class SourceFile:
    source_file_id: str
    relative_path: str
    size_bytes: int
    content_hash: str
    role: str
    used: bool
    parser_component: str | None
    diagnostics: tuple[str, ...] = ()


@dataclass
class Session:
    source_session_key: str
    machine_key: str
    start_time: datetime
    end_time: datetime
    machine_local_date: str
    timezone_basis: str
    blocks: list["SessionBlock"]
    settings: list["SettingsSnapshot"]
    signals: list["SignalChannel"]
    events: list["Event"]
    waveforms: list["WaveformSegment"]
    derived_values: list["DerivedValue"]
    source_file_ids: list[str]
    warnings: list["ImportWarning"]


@dataclass(frozen=True)
class SessionBlock:
    source_block_key: str
    start_time: datetime
    end_time: datetime
    block_kind: str
    source_file_ids: tuple[str, ...]


@dataclass(frozen=True)
class SettingsSnapshot:
    effective_at: datetime
    settings: dict[str, object]
    source_names: dict[str, str]
    source_file_ids: tuple[str, ...]
    confidence: Confidence


@dataclass(frozen=True)
class SignalChannel:
    channel_key: str
    source_label: str
    unit: str
    sample_rate_hz: float | None
    value_kind: Literal["sample", "span", "flag", "statistic"]
    leak_kind: Literal["total", "unintentional", "large_leak", "unknown"] | None
    source_file_ids: tuple[str, ...]


@dataclass(frozen=True)
class Event:
    source_event_key: str
    event_type: str
    source_type: str
    start_time: datetime
    duration_seconds: float | None
    source_file_id: str
    confidence: Confidence


@dataclass(frozen=True)
class WaveformSegment:
    channel_key: str
    start_time: datetime
    sample_rate_hz: float
    sample_count: int
    unit: str
    source_file_id: str
    storage_ref: str | None


@dataclass(frozen=True)
class DerivedValue:
    key: str
    value: float | int | str | None
    unit: str | None
    method: str
    input_refs: tuple[str, ...]
    validation: Validation


@dataclass(frozen=True)
class ImportWarning:
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    source_file_id: str | None = None
    source_value: str | None = None
    expected_values: tuple[str, ...] = ()
    affects: tuple[str, ...] = ()
```

Required invariants:

- timestamps are timezone-aware instants by the normalized boundary;
- source wall time and timezone assumptions remain in provenance;
- source IDs are stable across re-imports;
- units and leak semantics are mandatory for numeric channels;
- absence is `None`/empty, never a plausible zero or `"Unknown"`;
- derived values identify their inputs and method;
- unknown files, labels, firmware, and codes produce warnings;
- one malformed file may make an import partial but must not silently validate
  the whole run.

## Detection evidence

### Confidence

Confidence should be computed from positive and negative evidence:

- `exact`: required layout plus parseable identity and a format/header marker;
- `strong`: required layout and device-specific header, identity unavailable;
- `probable`: characteristic layout but a required identity/header is missing;
- `weak`: generic file extension or directory name only;
- `none`: required evidence absent or contradictory.

A generic `.edf` file is weak evidence. `DATALOG/` alone is probable ResMed
evidence; `DATALOG/` plus `STR.edf` plus parseable `Identification.*` is exact.

### Ambiguity

All detectors should run. SleepLab should not silently choose the first
matching adapter. When two candidates are close:

- record both results and their evidence;
- prefer an exact device-specific detector over a generic file detector;
- require a user or policy decision when confidence remains tied;
- never parse with one adapter merely because it appears earlier in a list.

This directly addresses `cpap-parser`'s current first-`can_handle()`-wins
behavior.

### Explicit source root

The public operation is **Select SD Card / Root Folder**. The detector receives
that directory as `source_root` and inspects only that root and
manufacturer-defined paths below it. It must not search parents, reinterpret a
selected `DATALOG` or `P-Series` directory, or recursively scan unrelated
directories.

Archive extraction is a separate source-preparation step. If an archive has a
single wrapper directory, the extraction coordinator may offer that directory
as the root before invoking detection. That decision and its provenance belong
to the coordinator, not to a manufacturer adapter.

Selecting ResMed `DATALOG`, PRS1 `P-Series`, or an individual machine
subdirectory therefore returns no match. The UI should explain that the user
must select the SD-card or extracted-archive root. `DetectedDevice.source_root`
retains the exact inspected root, while `device_path` identifies a machine
directory discovered beneath it.

### Multiple machines

Detection returns one `DetectedDevice` per machine directory. This is required
for PRS1 and Fisher & Paykel cards and is safer for reused or combined archives.
The import coordinator should show or import each device explicitly. One
adapter invocation must not silently merge serials.

### Unknown and partial layouts

Unknown layouts should return a structured no-match report containing observed
top-level names, candidate weak matches, and missing required evidence. Partial
known layouts should return a candidate plus warnings, not be misrepresented as
fully supported.

Examples:

- ResMed `DATALOG` without `STR.edf`: probable/partial native ResMed data;
- Prisma `config.pcfg` without `therapy.pdat`: identity-capable but
  session-import-incapable;
- BMC `.idx` without matching `.000`: identity or summary may be possible, but
  waveform capability is false;
- generic EDF directory: weak evidence until labels/header identify a vendor.

## cpap-parser audit

### Architecture

`cpap_parser/core.py` creates `UniversalCPAPParser`, registers adapters in a
fixed priority order, calls `can_handle()` sequentially, fully parses the first
match with `extract_and_map()`, stamps profile validation metadata, and returns
`CPAPDirectory`.

This resembles OSCAR only at the broadest registry level. Missing lifecycle
pieces are:

- no public detection result or evidence;
- no separate identity peek;
- no per-device capabilities;
- no ambiguity reporting;
- no multi-machine result contract;
- no import context or source manifest;
- no structured warnings for unknown source values;
- no stable source keys or provenance on sessions/events/signals;
- no persistence-independent normalized settings model.

### Supported vendors and current output

| Adapter | Detection | Identity | Sessions/events/signals/waveforms | Important gaps |
|---|---|---|---|---|
| ResMed | `DATALOG/`; requires `cpap-py` | JSON through `cpap-py`; may return `"Unknown"` on `main` | STR summaries; grouped BRP/PLD sessions; EVE events; BRP/PLD/oximetry signals when requested | Root evidence too weak; four open defects; settings largely absent; no source manifest |
| Philips Respironics | Generic EDF locations | Always `Unknown` | One session per EDF; EDF annotations; a small signal label map | Not native PRS1/DreamStation card support; no summaries/settings; relative timestamps in current code |
| DeVilbiss | `DV6/SET.BIN` or `SL/SET1` | Adapter/Rust dependent | Summary-oriented | No committed real fixture or OSCAR result |
| Apex | `APDATA/INFO.APC` and related files | Model/serial/firmware record | Summaries; no high-resolution waveform | No committed real fixture or OSCAR result |
| Lowenstein | `WM_DATA.TDF` or `config.pcfg`/`therapy.pdat` | Serial/model from binary or config ZIP | Summaries; Prisma sessions/events/waveforms; legacy output is shallower | Only external sample; legacy and Prisma Line validation status differ |
| BMC / 3B | `.USR` + matching `.idx` + `.000` | Serial/model from `.USR` | Summaries/sessions; Rust decoder has waveform data | No separate G3X support despite broad docs wording; no committed fixture |
| Fisher & Paykel | `FPHCARE/ICON/<serial>/SUM*.fph` and `SLEEPSTYLE` header | Serial directory and summary header | Summary/session timing and pressure mode | SleepStyle only in stated implementation; no events/AHI/waveforms; no committed fixture |
| Yuwell / DJMed | Several `RunLog.bys`/`YH-*` layouts | Model/serial from binary headers | Summaries/sessions | No committed fixture or OSCAR result |

The common schema in `cpap_parser/schema.py` exposes:

- `MachineInfo`: serial, product code, model, series, free-form properties,
  coarse validation status and notes;
- `CPAPSessionSummary`: daily date/start, AHI indices, pressure/leak summaries,
  usage, mode, selected respiratory/SpO2/arousal summaries;
- `CPAPSession`: start/end/duration, file type, nominal sample rate, events,
  and optional time series;
- `TimeSeriesData`: flow, pressure, mask pressure, leak, respiratory signals,
  SpO2, and pulse on high/low timestamp arrays;
- `CPAPEvent`: relative onset, source event string, optional duration/data.

Missing or ambiguous fields include:

- manufacturer as a first-class identity field;
- firmware/data-format version fields;
- settings snapshots and effective times;
- channel unit and leak-kind metadata;
- source file and source record provenance;
- session/block stable keys;
- event source IDs;
- waveform segments with independent sample rates and source refs;
- timezone/source-clock semantics per device;
- structured warnings and skipped-file diagnostics;
- per-capability validation;
- multiple machines per parse result.

The SleepLab mapper in `cpap_parser/adapters/sleeplab_output.py` further loses
information:

- creates one ID per date (`open-cpap-YYYY-MM-DD`), risking collisions and
  collapsing multi-block nights;
- flattens all events/metrics across sessions without source/session ownership
  in its top-level output;
- iterates only `timestamps`, so PLD-only values stored on `timestamps_low`
  can be omitted or misaligned;
- labels `pressure_50` as `avg_pressure`;
- treats zero as missing for several legitimate numeric fields;
- carries no units, leak semantics, files, warnings, or derived provenance.

### Four ResMed bugs and evidence

The authoritative report is `cpap-parser` issue
[#28](https://gitlab.com/open-cpap/cpap-parser/-/work_items/28), based on a
72-night AirSense 10 data set compared with OSCAR. The proposed implementation
is open MR [!8](https://gitlab.com/open-cpap/cpap-parser/-/merge_requests/8),
commit `105fe95`.

| Bug | Evidence | Current `main` behavior | Proposed fix | Disposition |
|---|---|---|---|---|
| Waveform/metric timestamps begin at Unix epoch | Issue #28 shows rows at `1970-01-01`, expected 2026 session time; source methods generate `i / sample_rate` while schema claims absolute UTC epoch | Reproducible directly from source contract mismatch in `_parse_brp_signals`, `_parse_pld_signals`, `_parse_generic_signals`; Respironics has the same pattern | Add `session_start.timestamp()` to each sample timestamp | Fix upstream with unit tests for all tracks/adapters. SleepLab may temporarily normalize relative timestamps only when the adapter/version contract explicitly says they are relative; do not guess by magnitude indefinitely. |
| Daily usage duration disagrees with OSCAR/session EDF | Issue #28 reports median `+60 s`, max `+30,060 s`, only 40% within 60 s, with a fragmented-night example | ResMed summary maps `STR.edf mask_duration`; session durations come separately from EDF headers | Replace summary `usage_hours` with sum of all parsed EDF session durations for the date | Fix upstream, but revise the proposed implementation: distinguish therapy usage, recording span, and block duration rather than overwriting one semantic. Require fixture tests for split sessions and summary-only days. |
| Serial is `"Unknown"` | Issue #28 records actual identity in `Identification.json` and OSCAR but parser output `"Unknown"`; current code returns `"Unknown"` when `cpap-py` parser returns `None` | Evidenced in source; real-data test expects a serial but is skipped when local data is absent | Direct JSON fallback through three nesting patterns | Fix upstream with anonymized JSON fixtures and typed parsing. Do not implement this as SleepLab normalization; identity extraction belongs to the adapter. Never store literal `"Unknown"` as identity. |
| Ghost daily sessions from full STR history | Issue #28 reports 439 summaries, 119 blocks, and 367 summaries without detailed EDF data | `main` returns all STR summaries unless caller passes optional `waveform_only`; SleepLab mapper iterates summaries into sessions | Filter summaries to dates represented by parsed sessions | Do not blindly apply the proposed fix. Upstream should expose summary-only days and detailed-data availability explicitly. SleepLab policy can choose whether to import summary-only records, but deleting valid summary history loses data. |

These are evidenced defects in the current ResMed-to-SleepLab usage, but MR !8
does not yet prove the fixes:

- it changes only `cpap_parser/adapters/resmed.py`;
- it adds no tests;
- its real-data validation checklist is unchecked;
- its pipeline failed;
- it is not merged into `develop` or `main`;
- it changes the timestamp contract incompatibly;
- its duration and ghost-summary fixes choose semantics that need a normalized
  model rather than destructive replacement/filtering.

### Fix ownership

- **Upstream `cpap-parser`:** parsing defects, identity extraction, timestamp
  contract, source semantics, detector precision, stable parser outputs, and
  parser-level regression tests.
- **Temporary SleepLab fork/pin:** only when an upstream fix exists, is covered
  by SleepLab conformance fixtures, and cannot be released promptly. Pin a
  commit SHA; never install an unpinned `main` tarball in production.
- **SleepLab normalization:** timezone policy, canonical channel names, unit
  conversion, leak-kind mapping, persistence IDs, import provenance,
  capability policy, and preserving distinct source semantics.

## PR #114 assessment

PR #114 should not be discarded. It contains useful research and working
pieces, but it combines too many concerns and encodes a transitional
ResMed/non-ResMed split as architecture.

### Retain

- GPL attribution and dependency posture, after correcting unrelated version
  regressions;
- provenance intent (`manufacturer`, parser source, validation state);
- machine/equipment linking as migration experience, while separating CPAP
  machine identity from replaceable equipment;
- tests for duplicate imports, timezone localization, event regrouping,
  SpO2 sanitization, and derivation fallbacks;
- use of a parser adapter rather than adding vendor conditionals throughout
  API/frontend code;
- keeping native ResMed routed in production while parser parity is uncertain.

### Change

- replace `ROUTE_ENABLED = {"Lowenstein": True, "ResMed": False}` with the
  loader registry, evidence, capabilities, validation policy, and adapter IDs;
- detect before full parse; do not catch every parser exception and silently
  fall back to native ResMed, because that can misclassify unsupported data;
- pin a released version or immutable commit, not the current `main` archive;
- remove the `cpap-parser`-specific SleepLab mapper as the normalized contract;
- make session identity machine- and source-block-scoped, not date-scoped;
- retain parser and source-file diagnostics instead of a single
  `parser_validated` boolean;
- preserve summary-only records distinctly from detailed sessions;
- model CPAP machines separately from consumable equipment records;
- verify Docker/lockfile changes against current upstream `main`; PR #114 was
  based on old fork commit `37621e9` and includes unrelated reversions such as
  removing `VERSION` from a Docker `COPY`.

### Split before merge

1. Loader contract and conformance documentation/tests.
2. Dependency packaging with an immutable parser version.
3. Machine/import provenance data model.
4. Read-only detection and identity peek.
5. Lowenstein opt-in import behind policy.
6. UI/API exposure of structured validation and warnings.
7. ResMed parser parity and eventual routing change.

The current draft should be rebased and harvested into those focused PRs rather
than merged as one feature branch.

## Conformance testing strategy

Conformance compares three layers:

1. raw `cpap-parser` adapter output;
2. OSCAR reference output for the same anonymized source;
3. SleepLab normalized output before persistence and after a duplicate import.

No single metric is enough. A pass requires explicit coverage and tolerances.

### Harness layout

Each fixture should have a private or redistributable source archive plus a
checked-in manifest:

```text
fixtures/<fixture-id>/
  manifest.json
  expected/
    detection.json
    identity.json
    oscar-summary.csv
    oscar-sessions.csv
    oscar-events.csv
    oscar-settings.json
    channel-inventory.json
    warnings.json
```

Large or sensitive archives should live in an access-controlled fixture store,
downloaded by hash only in an authorized validation job. CI may use small
synthetic/minimized files for structural and unit tests. The manifest must
record consent, anonymization method, source hash, machine family, parser
versions, OSCAR version/commit, timezone assumptions, and allowed uses.

### Machine identity

Compare manufacturer, family, model, model number, serial presence (using a
fixture pseudonym), firmware/data-format versions, number of detected machines,
and loader choice. Test reused cards and two-machine PRS1/F&P layouts.

### Session boundaries

Compare source block count, start/end instants, duration semantic, gaps,
cross-midnight assignment, fragments, and summary-only days. Do not compare
only one daily aggregate. Default tolerance should be at most one source sample
interval unless the format itself reports coarser boundaries.

### Settings

Compare therapy mode, fixed/min/max pressure, EPAP/IPAP/pressure support,
relief mode/level, ramp, humidifier, heated tube/temperature, mask, backup rate,
trigger/cycle, Ti min/max, and firmware-specific unknown codes. Missing is not
equal to zero/off.

### Events

Compare counts by normalized and source type, absolute timestamps, durations,
session/block ownership, duplicate annotations, and events near block
boundaries. Keep the source event string even when normalized.

### Leak

Require `unit` and `leak_kind`. Compare total leak, unintentional leak, and
large-leak spans separately. Never validate a channel merely because numeric
values look close. Verify conversions between L/s and L/min and manufacturer
threshold policy outside the parser.

### Pressure statistics

Compare source-reported and computed statistics separately. Record whether a
value is mean, median, 95th percentile, target pressure, mask pressure, IPAP,
or EPAP. Use time-weighted statistics when matching OSCAR's method and state
tolerances per channel resolution.

### Waveforms

Compare channel inventory, units, sample rates, segment start/end, sample
counts, missing spans, and selected statistics/correlations. At minimum test
flow and pressure; where available add leak, respiratory rate, tidal volume,
minute ventilation, snore, flow limitation, SpO2, and pulse.

### Timezone behavior

Test machine-local files in winter and summer, DST transitions, fixed-offset
devices, UTC-aware sources, wrong user timezone, and midnight crossings.
Normalized output must use aware instants while retaining original wall time
and the applied assumption. A parser must not label naive local time as UTC
without a warning.

### Duplicate imports

Run normalization and persistence twice, then with an expanded card:

- no duplicate machines, sessions, blocks, events, or samples;
- stable source keys;
- a partial day can be replaced/extended;
- corrected parser output updates derived values and invalidates stale
  analysis;
- two devices with the same local start time remain separate.

### Unknown firmware and signals

Mutate fixture headers or add unknown labels/codes. Import should continue when
safe, produce structured warnings, mark affected capabilities/derived values,
and never silently map an unknown channel to a known one.

### Acceptance gates

An adapter may be production-routed only when:

- detection/identity fixtures cover at least two independent devices for the
  family, including one non-happy path;
- session, setting, event, unit, and timezone comparisons pass;
- waveform capability is either tested or explicitly unavailable;
- all known deviations are encoded as expected warnings, not prose only;
- duplicate import behavior passes in SleepLab;
- fixture results are reproducible from immutable source and reference hashes.

## Fixture matrix

No CPAP SD-card fixture is committed in SleepLab or `cpap-parser`. SleepLab's
ResMed tests are synthetic/mocked. `cpap-parser` has local-path validation
tests, but the real archives and OSCAR exports are explicitly uncommitted.

| Manufacturer/device family | Fixture available? | Source | Identifying data removed? | OSCAR result available? | `cpap-parser` result available? | Validation status | Missing coverage |
|---|---|---|---|---|---|---|---|
| ResMed AirSense 10 (`cam`) | External only, 72 nights | Maintainer local path cited in issue #28/validation tests | No reproducible anonymized artifact documented | Summary comparison reported; CSV local | Yes locally | Failed/needs validation; four defects measured | Commit/access-controlled fixture, anonymization record, settings, events, waveform, timezone, regression tests |
| ResMed AirSense 11 (`cam`) | External only, described inconsistently with AirSense 10 report | Maintainer local path | Not documented | Local summary/sessions/details exports referenced | Yes locally | Needs validation | Clarify device/model fixture identity; immutable reports and parser version |
| ResMed AirSense 11 (`hanna`) | External only, 45 nights | Maintainer local path | Not documented | Local summary export referenced | Yes locally | Needs validation | Same as above; independent-device comparison |
| Lowenstein Prisma Line/Eyra sample | External only | Community contribution from `@drew2323` | Not documented in repository | Local OSCAR summary/sessions/details referenced | Yes locally | Parser profile says validated; waveform notes remain mixed | Redistributable/anonymized fixture, settings, timezone, multiple models/firmware |
| Lowenstein legacy `WM_DATA.TDF` | No reproducible fixture found | None committed | N/A | No | Parser implemented | Needs validation | Real card, identity, settings, sessions, events, waveforms |
| Philips PRS1/System One | No | None committed | N/A | No | Native card path not implemented | No-go | `P-Series`, `PROP*.TXT`, multi-machine, family/version, events/settings/waveforms |
| Philips DreamStation 1 | No | None committed | N/A | No | Native card path not implemented | No-go | Same plus encrypted/format variants as applicable |
| Philips DreamStation 2 | No | None committed | N/A | No | Generic EDF adapter is not evidence | No-go | `PROP.BIN`, identity/decryption, sessions/settings/events/waveforms |
| Fisher & Paykel SleepStyle | No | None committed | N/A | No | Parser implemented | Needs validation | At least two serial layouts, summary headers, settings, session boundaries |
| Fisher & Paykel ICON | No | None committed | N/A | No | Support unclear/separate from SleepStyle | No-go | ICON-specific header/model and parity |
| BMC legacy/G2/iBreeze | No | None committed | N/A | No | Parser implemented | Needs validation | `.USR/.idx/.000`, settings, events, waveform, units |
| BMC G3X | No | None committed | N/A | No | Separate OSCAR format missing | No-go | G3X index signature, firmware/model variants, waveform |
| DeVilbiss DV5/DV6 | Synthetic detector/minimal unit data only | Generated in tests | Yes/synthetic | No | Unit output only | Needs validation | Real cards, settings, sessions, events, units |
| Apex iCH/XT | No | None committed | N/A | No | Parser implemented | Needs validation | Identity, summaries, settings, known lack of waveform |
| Yuwell formats A-D | No | None committed | N/A | No | Parser implemented | Needs validation | One fixture per format, identity split, timestamps, events/settings |
| Unknown/ambiguous/wrapper archive | No shared corpus | To be generated | Synthetic | N/A | N/A | Required contract coverage | Nested root, partial card, mixed vendors, corrupt files, generic EDF collision |

Fixture acquisition priorities:

1. Two anonymized ResMed fixtures already used for comparison, minimized into
   a few representative nights while preserving the four bugs.
2. The Lowenstein community sample with explicit redistribution permission and
   anonymization record.
3. Native Philips PRS1/System One or DreamStation 1, because this is the
   largest architectural mismatch between OSCAR and `cpap-parser`.
4. Fisher & Paykel SleepStyle and ICON as distinct families.
5. Legacy BMC and BMC G3X as distinct layouts.

Never commit patient names, unmodified serials, device-generated identifiers
that can be linked back to a person, or full source cards without documented
consent. Pseudonymize consistently so identity matching can still be tested.

## Recommended implementation sequence

Each item is intended to be a small PR.

1. **Contract package and synthetic detector tests.** Add typed normalized
   objects and adapter protocols plus tests for exact/partial/ambiguous roots.
   No production routing.
2. **Fixture manifest and conformance CLI.** Add schemas/readers that compare
   normalized JSON with OSCAR exports. Keep private fixture retrieval outside
   the default test suite.
3. **Native ResMed detector/peek adapter.** Wrap existing ResMed behavior with
   root canonicalization, `Identification.*` identity, capabilities, and
   evidence. Do not change imported session behavior.
4. **Import-run diagnostics.** Persist or serialize source manifest, adapter
   version, warnings, skipped files, confidence, and validation status.
5. **Machine identity foundation.** Add a dedicated CPAP machine model and
   machine-scoped source session keys, designed to absorb useful PR #114
   equipment linkage without equating machines with consumables.
6. **Pin and package `cpap-parser`.** Use an immutable, tested commit or release;
   add license/build changes separately.
7. **Lowenstein read-only conformance adapter.** Detect, peek, and normalize
   into JSON in tests/diagnostics before database writes.
8. **Lowenstein opt-in persistence.** Enable only after fixture gates pass;
   expose partial capability/validation warnings.
9. **ResMed four-bug upstream tests.** Contribute minimized fixtures/tests or
   synthetic reproductions to `cpap-parser`; revise summary-only and duration
   semantics rather than accepting destructive filtering/overwriting.
10. **Parallel ResMed conformance.** Run native and parser adapters on the same
    fixtures, diff normalized output, and store no parser result in production.
11. **ResMed cutover decision.** Route parser-backed ResMed only after the
    retirement evidence below is met.
12. **Philips native-card adapter work.** Implement or upstream OSCAR-like
    `P-Series` detection and identity before claiming Respironics support.

The recommended next implementation PR is item 1: typed contract definitions
and synthetic detector-result tests, with no database or production importer
changes.

## Go/no-go conclusions

### Can `cpap-parser` be the common backend?

**Conditional go.** It is a credible shared implementation behind adapters,
especially for Lowenstein and OSCAR-derived Rust parsers. It is not ready to be
the sole production backend or the owner of SleepLab's domain contract.
SleepLab must retain routing, capabilities, normalization, provenance, and
validation policy.

### Should native ResMed parsing remain temporarily?

**Yes.** Keep it as the production path and regression oracle. `cpap-parser`
`main` still contains the four evidenced issues, and the proposed fix is
unmerged and untested in CI with committed fixtures.

### What evidence is required before retiring native ResMed?

At minimum:

- two independent anonymized ResMed card fixtures with immutable hashes;
- OSCAR summary, session, settings, event, and waveform references;
- all four issue #28 defects reproduced by tests and fixed;
- explicit, non-destructive semantics for summary-only days and duration types;
- machine identity parity including missing/variant JSON/TGT layouts;
- session/block starts and ends within source-resolution tolerance;
- event counts/timestamps/durations and waveform channels/sample rates pass;
- leak unit/kind and pressure statistic semantics match;
- DST/timezone and cross-midnight cases pass;
- duplicate and incremental imports are stable in SleepLab;
- unknown firmware/signals create diagnostics;
- a soak period where native and parser normalized outputs are diffed on real
  imports without parser-backed production writes.

### Which vendor should be validated first after ResMed?

**Lowenstein Prisma Line.** It already has the strongest parser implementation,
OSCAR-oriented validation code, session/event/waveform work, and a community
sample. The first task is making that evidence reproducible and checking
settings/timezone semantics, not assuming the current `validated` badge is
sufficient.

Philips Respironics should be the next architecture target after that because
its native card format exposes the largest gap between current `cpap-parser`
claims and OSCAR's loader behavior.

## Source references

SleepLab:

- [Upstream PR #114](https://github.com/joshuamyers-dev/sleeplab/pull/114)
- [Upstream issue #38](https://github.com/joshuamyers-dev/sleeplab/issues/38)
- `importer/import_sessions.py`
- `importer/edf_parser.py`
- `tests/test_resmed_import_regressions.py`
- `docs/oscar_import_gap_analysis.md` on commit `2f0135b`

`cpap-parser`:

- [Repository](https://gitlab.com/open-cpap/cpap-parser)
- [ResMed issue #28](https://gitlab.com/open-cpap/cpap-parser/-/work_items/28)
- [ResMed MR !8](https://gitlab.com/open-cpap/cpap-parser/-/merge_requests/8)
- `cpap_parser/core.py`
- `cpap_parser/adapters/base.py`
- `cpap_parser/adapters/*.py`
- `cpap_parser/adapters/sleeplab_output.py`
- `cpap_parser/schema.py`
- `cpap_parser/device_profiles.py`
- `validation/`

OSCAR:

- `oscar/SleepLib/machine_loader.h` and `.cpp`
- `oscar/SleepLib/importcontext.h` and `.cpp`
- `oscar/SleepLib/profiles.cpp`
- `oscar/database/machine_repository.cpp`
- `oscar/SleepLib/schema.cpp`
- `oscar/SleepLib/loader_plugins/resmed_loader.cpp`
- `oscar/SleepLib/loader_plugins/prs1_loader.cpp`
- `oscar/SleepLib/loader_plugins/sleepstyle_loader.cpp`
- `oscar/SleepLib/loader_plugins/icon_loader.cpp`
- `oscar/SleepLib/loader_plugins/prisma_loader.cpp`
- `oscar/SleepLib/loader_plugins/bmc_loader.cpp`
- `oscar/SleepLib/loader_plugins/bmcg3x_loader.cpp`
- `oscar/SleepLib/loader_plugins/bmcDataParsing.cpp`
- `oscar/SleepLib/loader_plugins/bmcG3xDataParsing.cpp`
