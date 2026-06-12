# Nightly session workflow

The session detail page is the primary read surface after a ResMed
`cpap-parser` import. A machine-local night now reports:

- authoritative usage, AHI, event counts, pressure, leak, and therapy mode;
- scored-event timeline and event list;
- low-rate therapy graphs when `session_metrics` samples exist;
- focused flow/pressure waveforms around an event when `session_waveform`
  samples exist; and
- explicit coverage states for settings, SpO2/pulse, and full-night flow.

The `SessionDetail.data_availability` API field is the source of truth for the
coverage panel. Event, metric, and event-window queries are scoped to the
selected user, machine, and machine-local date so data from two devices on the
same date cannot be mixed.

Current parser limits are shown in the UI rather than inferred from empty
charts. ResMed parser SpO2/pulse is not yet supported, and high-rate flow is
retained only for event windows rather than as a full-night waveform. Re-import
idempotence remains owned by the parser persistence workflow.
