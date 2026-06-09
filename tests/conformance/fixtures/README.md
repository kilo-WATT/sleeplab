# Conformance fixture: `resmed_airsense10_001`

Anonymized ResMed **AirSense 10 AutoSet** SD card, used by the conformance tests
in [`../test_resmed_airsense10.py`](../test_resmed_airsense10.py) to pin the
behavior of the five ResMed bug fixes against *real device data* (not synthetic
stand-ins).

Origin: built for cpap-parser MR !12 by `scrub_sdcard.py` from a
contributor-owned card. Copied here byte-for-byte (all 53 files SHA-256-verified
identical to the source).

## This is anonymized data — do not replace it with a real card

- **Serial scrubbed.** The real serial was replaced with the placeholder
  `SN-FIXTURE-AirSense10-001` (see `Identification.tgt` `#SRN`).
- **Timestamps shifted.** Every EDF timestamp was shifted by a fixed offset
  (`timestamp_shift_days: -508`, per `manifest.json`), preserving all relative
  spacing while destroying the real calendar dates.
- `manifest.json` records `redistribution: permitted` and the full
  anonymization method. **Never** overwrite these files with an un-scrubbed SD
  card — that would commit real patient data to the repo. If you need to refresh
  the fixture, re-run the cpap-parser scrubber and copy its output, never a raw
  card.

## Layout

```
resmed_airsense10_001/
├── Identification.tgt        # machine identity (#SRN = scrubbed serial)
├── STR.edf                   # daily summary history (40 nights)
├── DATALOG/                  # detailed per-session EDF (3 nights only)
│   ├── 20260506/             # BRP/PLD/EVE/CSL/SAD
│   ├── 20260517/             # BRP/PLD/EVE/CSL/SAD
│   └── 20260528/             # BRP/PLD/EVE/CSL/SAD
├── oscar_reference/          # ground-truth exported from OSCAR
│   ├── summary.csv           # per-day rollup (the values tests assert against)
│   └── sessions.csv          # per-session breakdown
└── manifest.json             # provenance + anonymization metadata
```

## OSCAR reference values (what the tests assert against)

Exported from OSCAR reading the same card. `oscar_reference/summary.csv` is the
source of truth; the figures below are a human-readable summary.

| Property | Value |
| --- | --- |
| Summary nights (STR.edf) | **40** (`2026-04-21` … `2026-06-07`) |
| Nights with detailed DATALOG data | **3** (`2026-05-06`, `2026-05-17`, `2026-05-28`) |
| STR-only "ghost" nights (no DATALOG) | **37** |
| Per-day AHI range | `0.000` … `2.408` |
| Expected serial | `SN-FIXTURE-AirSense10-001` |

Representative nights (from `summary.csv`):

| Date | AHI | Total time | A count | H count | Detailed data? |
| --- | --- | --- | --- | --- | --- |
| `2026-04-28` | 2.408 | 07:28:34 | 5 | 0 | no (STR only) |
| `2026-05-05` | 0.600 | 06:50:00 | 0 | 0.68 | no (STR only) |
| `2026-05-06` | 0.138 | 07:15:03 | 0 | 0 | **yes** |

The tests read these values back out of `summary.csv` at runtime rather than
hard-coding them, so refreshing the fixture + its OSCAR export keeps them in
sync.

## Known discrepancy

`manifest.json` reports `"nights_included": 5`, but the card ships detailed
`DATALOG/` data for only **3** nights (the other dates are STR-only summary
history). The conformance tests treat the on-disk DATALOG directories as
authoritative for which nights have detailed data. This note is recorded here
rather than "fixed" — the fixture is real anonymized data and must not be
edited.

## Running

The serial test runs anywhere `cpap-parser` is installed (pure-Python identity
fallback). The OSCAR-comparison tests additionally need the **`cpap-py`** EDF
backend to decode `STR.edf` / `DATALOG`; they `importorskip("cpap_py")` and
skip with a visible reason when it is absent.

```
python -m pytest tests/conformance/ -v
```
