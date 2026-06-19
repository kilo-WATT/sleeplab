import type { ImportRunSummary } from '../api/client'

export const IMPORT_STAGE_LABELS: Record<string, string> = {
  preparing_import: 'Preparing import',
  scanning_files: 'Scanning card files',
  selecting_importer: 'Selecting importer',
  parsing_sessions: 'Parsing sessions',
  importing_summaries: 'Importing summaries and events',
  writing_database: 'Writing sessions and events',
  building_waveform_chunks: 'Writing waveform chunks',
  refreshing_aggregates: 'Finalizing',
  complete: 'Complete',
  failed: 'Failed',
}

export function isImportRunActive(run: ImportRunSummary | null | undefined) {
  return run?.status === 'running' || run?.status === 'pending'
}

export function shouldPollImportRuns(
  run: ImportRunSummary | null | undefined,
  importStarted: boolean,
) {
  return isImportRunActive(run) || (!run && importStarted)
}

export function formatImportElapsed(startedAt: string | null, now: number) {
  if (!startedAt) return 'Elapsed time unavailable'
  const seconds = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000))
  const minutes = Math.floor(seconds / 60)
  return `Elapsed ${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

export function shouldDismissImportRunOnNavigation(
  run: ImportRunSummary | null,
  previousPath: string,
  nextPath: string,
) {
  return previousPath !== nextPath && run !== null && !isImportRunActive(run)
}
