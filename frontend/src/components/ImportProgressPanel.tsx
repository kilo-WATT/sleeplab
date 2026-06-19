import type { ImportRunSummary } from '../api/client'
import { formatImportElapsed, IMPORT_STAGE_LABELS, isImportRunActive } from './importProgress'

export function ImportProgressPanel({
  run,
  now,
  compact = false,
}: {
  run: ImportRunSummary
  now?: number
  compact?: boolean
}) {
  const active = isImportRunActive(run)
  const failed = run.status === 'failed'
  const stage = run.current_stage ?? (active ? 'parsing_sessions' : failed ? 'failed' : 'complete')
  const label = IMPORT_STAGE_LABELS[stage] ?? stage.replaceAll('_', ' ')
  const sessionTotal = run.sessions_total ?? 0
  const sessionProcessed = Math.min(run.sessions_processed ?? 0, sessionTotal)
  const determinate = active && sessionTotal > 0
  const percentage = determinate ? Math.round((sessionProcessed / sessionTotal) * 100) : null
  const fileProgress = run.files_total ? `${run.files_processed ?? 0} of ${run.files_total}` : null
  const sessionProgress = sessionTotal ? `${sessionProcessed} of ${sessionTotal}` : null
  const elapsedNow = now ?? new Date(run.completed_at ?? run.started_at ?? 0).getTime()
  const title = failed ? 'Import failed' : active ? 'Synchronizing sleep data' : 'Import complete'
  const importerLabel = run.importer_mode === 'legacy'
    ? 'Legacy/native fallback'
    : run.importer_mode === 'cpap-parser' || run.adapter_id === 'resmed-cpap-parser-v1'
      ? 'Recommended cpap-parser'
      : 'Importer not recorded'

  return (
    <div
      aria-live="polite"
      className={`rounded-[18px] border ${
        failed
          ? 'border-[rgba(176,58,46,0.28)] bg-[rgba(176,58,46,0.08)]'
          : active
            ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]'
            : 'border-[rgba(106,161,54,0.24)] bg-[rgba(106,161,54,0.1)]'
      } ${compact ? 'px-4 py-3' : 'p-4 sm:p-5'}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={`font-bold ${compact ? 'text-sm' : 'text-base'} text-[var(--foreground)]`}>{title}</p>
          <p className="mt-1 text-sm font-medium text-[var(--muted-foreground)]">
            {run.current_message ?? (failed ? 'The importer stopped before completion.' : active ? 'Importing parser-backed sleep data.' : 'Import completed successfully.')}
          </p>
        </div>
        <span className={`rounded-full bg-[var(--surface-strong)] px-3 py-1 text-xs font-bold ${
          failed ? 'text-[var(--danger-text)]' : active ? 'text-[var(--accent)]' : 'text-[var(--green-700)]'
        }`}>
          {label}
        </span>
      </div>
      {active ? (
        <div
          className="mt-3 h-2 overflow-hidden rounded-full bg-[var(--border)]"
          role="progressbar"
          aria-label="Import activity"
          aria-valuemin={determinate ? 0 : undefined}
          aria-valuemax={determinate ? 100 : undefined}
          aria-valuenow={percentage ?? undefined}
        >
          <div
            className={`h-full rounded-full bg-[var(--accent)] ${determinate ? '' : 'w-1/3 animate-pulse'}`}
            style={determinate ? { width: `${percentage}%` } : undefined}
          />
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--muted-foreground)]">
        <span className="font-bold text-[var(--foreground)]">{importerLabel}</span>
        <span>{formatImportElapsed(run.started_at, elapsedNow)}</span>
        {fileProgress ? (
          <span>
            <span className="font-bold text-[var(--foreground)]">Files</span> {fileProgress}
          </span>
        ) : null}
        {sessionProgress ? (
          <span>
            <span className="font-bold text-[var(--foreground)]">Sessions</span> {sessionProgress}
          </span>
        ) : null}
        {percentage != null ? (
          <span>
            <span className="font-bold text-[var(--foreground)]">Progress</span> {percentage}%
          </span>
        ) : null}
      </div>
      {!active && !failed ? <ImportResultSummary run={run} /> : null}
      {active && !compact ? (
        <p className="mt-3 text-xs leading-5 text-[var(--muted-foreground)]">
          Large first imports that include full-night waveforms may take several minutes. You can continue using SleepLab while this finishes.
        </p>
      ) : null}
    </div>
  )
}

function ImportResultSummary({ run }: { run: ImportRunSummary }) {
  const values = [
    run.sessions_added_count == null ? null : `${run.sessions_added_count} added`,
    run.sessions_updated_count == null ? null : `${run.sessions_updated_count} updated`,
    run.sessions_skipped_count == null ? null : `${run.sessions_skipped_count} already present`,
    `${run.imported_event_count} events`,
    run.waveform_chunk_count == null ? null : `${run.waveform_chunk_count} waveform chunks`,
  ].filter(Boolean)
  const noOp = run.sessions_added_count === 0 && run.sessions_updated_count === 0

  return (
    <div className="mt-3 border-t border-[var(--border)] pt-3 text-xs text-[var(--muted-foreground)]">
      <p>{values.join(' · ')}</p>
      {noOp ? <p className="mt-1 font-bold text-[var(--foreground)]">No new sessions were needed.</p> : null}
    </div>
  )
}
