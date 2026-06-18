import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type ReactNode } from 'react'

import { api } from '../api/client'
import type { ImportPlanResponse, ImportRunSummary, OximeterImportResponse } from '../api/client'
import {
  ActivityIcon,
  CheckCircleIcon,
  EquipmentIcon,
  SparklesIcon,
} from '../components/icons/ChevronIcons'
import { ImportProgressPanel } from '../components/ImportProgressPanel'
import { IMPORT_STAGE_LABELS, shouldPollImportRuns } from '../components/importProgress'
import OximeterImportSummary from '../components/OximeterImportSummary'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import { collectOximeterFilesFromInput } from '../lib/oximeterFiles'
import { notifyImportStarted } from '../lib/aiSummaryCache'
import { Link } from 'react-router-dom'

/**
 * Type definition for the selected import file.
 */
type SelectedImportFile = {
  file: File
  relativePath: string
}

/**
 * Type definition for the upload phase.
 */
type UploadPhase = 'idle' | 'uploading' | 'complete'

/**
 * Identifies which import source workflow is currently active.
 */
type ImportSource = 'cpap' | 'o2' | 'sleephq'

/**
 * React component or element to render the import.
 *
 * @returns The rendered React element.
 */
export default function Import() {
  const directoryInputRef = useRef<HTMLInputElement | null>(null)
  const oximeterInputRef = useRef<HTMLInputElement | null>(null)
  const [rootName, setRootName] = useState<string | null>(null)
  const [selectedFiles, setSelectedFiles] = useState<SelectedImportFile[]>([])
  const [folderLabel, setFolderLabel] = useState('No folder selected')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [uploadPhase, setUploadPhase] = useState<UploadPhase>('idle')
  const [sourceUploadId, setSourceUploadId] = useState<string | null>(null)
  const [importPlan, setImportPlan] = useState<ImportPlanResponse | null>(null)
  const [importRuns, setImportRuns] = useState<ImportRunSummary[]>([])
  const [sourceImportMessage, setSourceImportMessage] = useState<string | null>(null)
  const [progressNow, setProgressNow] = useState(0)

  // Which import source the workflow area is showing.
  const [selectedSource, setSelectedSource] = useState<ImportSource>('cpap')

  // SleepHQ import state
  const [sleepHqEnabled, setSleepHqEnabled] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)
  const [syncError, setSyncError] = useState<string | null>(null)
  const [uploadedFiles, setUploadedFiles] = useState(0)
  const [totalFiles, setTotalFiles] = useState(0)

  // Local server import state
  const [localPath, setLocalPath] = useState<string | null>(null)
  const [localLastAt, setLocalLastAt] = useState<string | null>(null)
  const [localLastStatus, setLocalLastStatus] = useState<string | null>(null)
  const [isLocalImporting, setIsLocalImporting] = useState(false)
  const [localMessage, setLocalMessage] = useState<string | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)

  // Oximeter import state
  const [oximeterFiles, setOximeterFiles] = useState<File[]>([])
  const [oximeterLabel, setOximeterLabel] = useState('No O2 files selected')
  const [isOximeterImporting, setIsOximeterImporting] = useState(false)
  const [oximeterOverwrite, setOximeterOverwrite] = useState(false)
  const [oximeterResult, setOximeterResult] = useState<OximeterImportResponse | null>(null)
  const [oximeterError, setOximeterError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getImportSettings()
      .then((s) => {
        setLocalPath(s.local_datalog_path)
        setLocalLastAt(s.last_local_import_at)
        setLocalLastStatus(s.last_local_import_status)
        setSleepHqEnabled(Boolean(s.sleephq_enabled))
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    void api.getImportRuns(10).then(setImportRuns).catch(() => {})
  }, [])

  useEffect(() => {
    if (!shouldPollImportRuns(importRuns[0], Boolean(sourceImportMessage))) return

    const timer = window.setInterval(() => {
      setProgressNow(Date.now())
      void api.getImportRuns(10).then(setImportRuns).catch(() => {})
    }, 2000)
    return () => window.clearInterval(timer)
  }, [importRuns, sourceImportMessage])

  useEffect(() => {
    return () => {
      if (sourceUploadId) {
        void api.discardSourceUpload(sourceUploadId).catch(() => {})
      }
    }
  }, [sourceUploadId])

  async function handleSelectFolder() {
    setError(null)
    setUploadPhase('idle')
    setUploadedFiles(0)
    setTotalFiles(0)
    setImportPlan(null)
    setSourceUploadId(null)
    setSourceImportMessage(null)

    if (!supportsDirectorySelection()) {
      setError('This browser does not support folder import. Try Chrome or Edge, or use the desktop app.')
      return
    }

    if (!('showDirectoryPicker' in window)) {
      directoryInputRef.current?.click()
      return
    }

    try {
      const showDirectoryPicker = window.showDirectoryPicker
      if (!showDirectoryPicker) {
        directoryInputRef.current?.click()
        return
      }

      const directoryHandle = await showDirectoryPicker()
      const files = await collectSourceFiles(directoryHandle)
      applySelectedFiles(directoryHandle.name, files)
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return
      }
      setError(err instanceof Error ? err.message : 'Could not read selected folder')
    }
  }

  function handleDirectoryInputChange(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? [])
    if (files.length === 0) {
      return
    }

    setError(null)
    setUploadPhase('idle')
    setUploadedFiles(0)
    setTotalFiles(0)

    const root = getInputRootName(files)
    const selected = collectSourceFilesFromInput(files, root)
    applySelectedFiles(root, selected)
    event.target.value = ''
  }

  function applySelectedFiles(root: string, files: SelectedImportFile[]) {
    if (sourceUploadId) {
      void api.discardSourceUpload(sourceUploadId).catch(() => {})
    }
    setRootName(root)
    setSelectedFiles(files)
    setImportPlan(null)
    setSourceUploadId(null)
    setSourceImportMessage(null)
    setFolderLabel(files.length > 0 ? `${root} (${files.length} files)` : `${root} (no files found)`)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!rootName || selectedFiles.length === 0) {
      setError('Select the SD card or extracted archive root first')
      return
    }

    setError(null)
    setUploadPhase('uploading')
    setUploadedFiles(0)
    setTotalFiles(selectedFiles.length)
    setIsSubmitting(true)
    let uploadId: string | null = null
    try {
      const { upload_id } = await api.startSourceUpload(rootName)
      uploadId = upload_id
      setSourceUploadId(upload_id)
      const batchSize = 200

      for (let index = 0; index < selectedFiles.length; index += batchSize) {
        const batch = selectedFiles.slice(index, index + batchSize)
        await api.uploadSourceBatch(upload_id, batch)
        setUploadedFiles(Math.min(index + batch.length, selectedFiles.length))
      }

      const result = await api.inspectSourceUpload(upload_id)
      setImportPlan(result)
      setUploadPhase('complete')
    } catch (err) {
      if (uploadId) {
        void api.discardSourceUpload(uploadId).catch(() => {})
        setSourceUploadId(null)
      }
      setUploadPhase('idle')
      setError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setIsSubmitting(false)
    }
  }

  const uploadPercent = totalFiles > 0 ? Math.round((uploadedFiles / totalFiles) * 100) : 0
  const canImportDetectedSource = importPlan?.executable === true && sourceUploadId !== null

  async function handleDetectedImport() {
    if (!sourceUploadId || !canImportDetectedSource) {
      return
    }
    setIsSubmitting(true)
    setError(null)
    try {
      const result = await api.finishSourceImport(sourceUploadId)
      setSourceUploadId(null)
      if (result.status === 'unchanged') {
        setSourceImportMessage(result.message)
        const runs = await api.getImportRuns(10)
        setImportRuns(runs)
        return
      }
      notifyImportStarted()
      setSourceImportMessage(
        result.import_run_id
          ? `Import started. Run ${result.import_run_id.slice(0, 8)} is now processing in the background.`
          : result.message,
      )
      const runs = await api.getImportRuns(10)
      setImportRuns(runs)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleLocalImport() {
    setLocalError(null)
    setLocalMessage(null)
    setIsLocalImporting(true)
    try {
      const result = await api.triggerLocalImport()
      notifyImportStarted()
      setLocalMessage(result.message || 'Import started. New sessions will appear shortly.')
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setIsLocalImporting(false)
    }
  }

  async function handleSleepHQSync() {
    setSyncError(null)
    setSyncMessage(null)
    setIsSyncing(true)
    try {
      const result = await api.triggerSleepHQImport()
      notifyImportStarted()
      setSyncMessage(result.message || 'Sync started. New sessions will appear shortly.')
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : 'Sync failed')
    } finally {
      setIsSyncing(false)
    }
  }

  function handleOximeterInputChange(event: ChangeEvent<HTMLInputElement>) {
    const files = collectOximeterFilesFromInput(Array.from(event.target.files ?? []))
    setOximeterFiles(files)
    setOximeterResult(null)
    setOximeterError(null)
    setOximeterLabel(
      files.length > 0
        ? `${files.length} O2 file${files.length === 1 ? '' : 's'} selected`
        : 'No compatible O2 files selected',
    )
    event.target.value = ''
  }

  async function handleOximeterImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!oximeterFiles.length) {
      setOximeterError('Select one or more Wellue/Viatom binary files first')
      return
    }

    setIsOximeterImporting(true)
    setOximeterError(null)
    setOximeterResult(null)
    try {
      const result = await api.uploadOximeterFiles(oximeterFiles, {
        overwrite: oximeterOverwrite,
      })
      setOximeterResult(result)
    } catch (err) {
      setOximeterError(err instanceof Error ? err.message : 'O2 import failed')
    } finally {
      setIsOximeterImporting(false)
    }
  }

  const latestRun = importRuns[0] ?? null
  const showActiveProgress = Boolean(
    latestRun && (latestRun.status === 'running' || latestRun.status === 'pending' || sourceImportMessage),
  )
  const lastCpapImportAt = importRuns.find((run) => run.completed_at)?.completed_at ?? latestRun?.started_at ?? null
  const cpapStatus = lastCpapImportAt ? `Last import: ${formatImportDate(lastCpapImportAt)}` : 'No imports yet'
  const o2Status = oximeterResult
    ? `Imported ${oximeterResult.imported} recording${oximeterResult.imported === 1 ? '' : 's'}`
    : 'No imports yet'
  const sleepHqStatus = sleepHqEnabled ? 'Ready to pull recent sessions' : 'Not configured'

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div className="space-y-1">
        <h1 className="text-2xl font-extrabold tracking-tight text-[var(--foreground)]">Import Data</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Bring CPAP, oximeter, and SleepHQ data into SleepLab. Pick a source to start, then review recent activity below.
        </p>
      </div>

      <section className="space-y-3">
        <SectionLabel>Import sources</SectionLabel>
        <div className="grid gap-3 sm:grid-cols-3">
          <SourceTile
            active={selectedSource === 'cpap'}
            icon={<EquipmentIcon className="h-5 w-5" />}
            title="CPAP SD card"
            description="Import detailed therapy data from your CPAP machine."
            status={cpapStatus}
            actionLabel="Import from SD card"
            onSelect={() => setSelectedSource('cpap')}
            onAction={() => {
              setSelectedSource('cpap')
              void handleSelectFolder()
            }}
          />
          <SourceTile
            active={selectedSource === 'o2'}
            icon={<ActivityIcon className="h-5 w-5" />}
            title="O2 Ring"
            description="Upload Wellue / ViHealth O2 files."
            status={o2Status}
            actionLabel="Import O2 files"
            onSelect={() => setSelectedSource('o2')}
            onAction={() => {
              setSelectedSource('o2')
              oximeterInputRef.current?.click()
            }}
          />
          <SourceTile
            active={selectedSource === 'sleephq'}
            icon={<SparklesIcon className="h-5 w-5" />}
            title="SleepHQ"
            description="Pull recent sessions from SleepHQ."
            status={sleepHqStatus}
            statusTone={sleepHqEnabled ? 'default' : 'muted'}
            actionLabel="Import now"
            onSelect={() => setSelectedSource('sleephq')}
            onAction={() => {
              setSelectedSource('sleephq')
              void handleSleepHQSync()
            }}
          />
        </div>
      </section>

      <input
        ref={directoryInputRef}
        hidden
        multiple
        type="file"
        onChange={handleDirectoryInputChange}
        {...DIRECTORY_INPUT_ATTRIBUTES}
      />
      <input ref={oximeterInputRef} hidden multiple type="file" onChange={handleOximeterInputChange} />

      {selectedSource === 'cpap' ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">CPAP SD card import</CardTitle>
            <CardDescription>
              Select the <span className="font-bold text-[var(--foreground)]">SD card or root folder</span>. SleepLab
              inspects its structure, identifies the machine, and shows what the loader can read before importing.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {supportsDirectorySelection() ? null : (
              <div className="mb-5 rounded-[16px] border border-[rgba(233,120,75,0.28)] bg-[rgba(233,120,75,0.08)] px-4 py-3 text-sm text-[var(--orange-700)]">
                <span className="font-bold">Browser not supported.</span> Folder import requires either the Chromium
                directory picker or a browser that supports directory uploads.
              </div>
            )}
            <form className="space-y-5" onSubmit={handleSubmit}>
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-[20px] border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-3">
                <p className="min-w-0 truncate text-sm text-[var(--foreground)]">{folderLabel}</p>
                <Button type="button" size="sm" variant="outline" onClick={handleSelectFolder} disabled={isSubmitting}>
                  Select folder
                </Button>
              </div>
              {uploadPhase === 'uploading' ? (
                <div className="space-y-3 rounded-[20px] border border-[var(--accent-border)] bg-[var(--surface-soft)] p-4">
                  <div className="flex items-center justify-between gap-4 text-sm">
                    <p className="font-bold text-[var(--foreground)]">Preparing source inspection</p>
                    <p className="font-bold text-[var(--accent)]">{uploadPercent}%</p>
                  </div>
                  <div
                    aria-label="Upload progress"
                    aria-valuemax={totalFiles}
                    aria-valuemin={0}
                    aria-valuenow={uploadedFiles}
                    className="h-3 overflow-hidden rounded-full bg-[var(--border)]"
                    role="progressbar"
                  >
                    <div
                      className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-300 ease-out"
                      style={{ width: `${uploadPercent}%` }}
                    />
                  </div>
                  <p className="text-sm font-medium text-[var(--muted-foreground)]">
                    Uploaded {uploadedFiles} of {totalFiles} files
                  </p>
                </div>
              ) : null}
              {uploadPhase === 'complete' ? (
                <div className="flex items-start gap-3 rounded-[20px] border border-[var(--accent-border)] bg-[var(--accent-soft)] p-4 text-[var(--accent)]">
                  <div className="space-y-1">
                    <p className="text-sm font-bold">Source inspected</p>
                    <p className="text-sm font-medium text-[var(--muted-foreground)]">
                      Detection is complete. Review the loader result below before importing.
                    </p>
                  </div>
                </div>
              ) : null}
              {error ? <p className="text-sm text-[var(--danger-text)]">{error}</p> : null}
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? 'Inspecting...' : importPlan ? 'Re-inspect card' : 'Inspect card'}
              </Button>
            </form>
            {importPlan ? (
              <>
                <LoaderInspectionPanel
                  plan={importPlan}
                  canImport={Boolean(canImportDetectedSource)}
                  isImporting={isSubmitting}
                  importStarted={sourceImportMessage !== null}
                  onImport={handleDetectedImport}
                />
                {sourceImportMessage ? (
                  <div className="mt-4 rounded-[16px] border border-[rgba(106,161,54,0.24)] bg-[rgba(106,161,54,0.1)] px-4 py-3 text-sm font-medium text-[var(--olive-deep)]">
                    {sourceImportMessage}
                  </div>
                ) : null}
              </>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {selectedSource === 'o2' ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">O2 Ring import</CardTitle>
            <CardDescription>
              Upload ViHealth or O2 Insight Pro binary files from Wellue/Viatom oximeters. SleepLab matches each
              recording to an existing CPAP session by time.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-5" onSubmit={handleOximeterImport}>
              <div className="rounded-[20px] border border-[var(--border)] bg-[var(--surface-soft)] p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="min-w-0 truncate text-sm text-[var(--foreground)]">{oximeterLabel}</p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => oximeterInputRef.current?.click()}
                    disabled={isOximeterImporting}
                  >
                    Select O2 files
                  </Button>
                </div>
                {oximeterFiles.length > 0 ? (
                  <div className="mt-3 max-h-24 overflow-auto rounded-[14px] border border-[var(--border)] bg-[var(--surface-strong)] px-3 py-2">
                    {oximeterFiles.map((file) => (
                      <p
                        key={`${file.name}-${file.size}`}
                        className="truncate text-xs font-medium text-[var(--muted-foreground)]"
                      >
                        {file.name}
                      </p>
                    ))}
                  </div>
                ) : null}
              </div>
              <label className="flex items-start gap-3 rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-3 text-sm">
                <input
                  className="mt-1 h-4 w-4"
                  type="checkbox"
                  checked={oximeterOverwrite}
                  onChange={(event) => setOximeterOverwrite(event.target.checked)}
                />
                <span>
                  <span className="block font-bold text-[var(--foreground)]">Replace existing SpO2 data</span>
                  <span className="text-[var(--muted-foreground)]">
                    Leave this off to skip sessions that already have oximeter data.
                  </span>
                </span>
              </label>
              {oximeterResult ? <OximeterImportSummary result={oximeterResult} /> : null}
              {oximeterError ? <p className="text-sm text-[var(--danger-text)]">{oximeterError}</p> : null}
              <Button type="submit" disabled={isOximeterImporting}>
                {isOximeterImporting ? 'Importing...' : 'Import O2 data'}
              </Button>
            </form>
          </CardContent>
        </Card>
      ) : null}

      {selectedSource === 'sleephq' ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">SleepHQ import</CardTitle>
            <CardDescription>
              Run a one-time import of recent CPAP sessions from your SleepHQ account. Configure your credentials in{' '}
              <Link className="font-medium text-[var(--foreground)] underline underline-offset-2" to="/settings">
                Settings
              </Link>{' '}
              first, then re-run this whenever you want to pull in newer sessions.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {syncMessage ? (
              <div className="flex items-start gap-3 rounded-[20px] border border-[rgba(106,161,54,0.24)] bg-[rgba(106,161,54,0.1)] p-4 text-[var(--olive-deep)]">
                <CheckCircleIcon className="mt-0.5 h-5 w-5 shrink-0" />
                <p className="text-sm font-medium">{syncMessage}</p>
              </div>
            ) : null}
            {syncError ? <p className="text-sm text-[var(--danger-text)]">{syncError}</p> : null}
            <Button onClick={handleSleepHQSync} disabled={isSyncing}>
              {isSyncing ? 'Importing...' : 'Import now'}
            </Button>
            {localPath ? (
              <div className="space-y-3 border-t border-[var(--border)] pt-4">
                <div>
                  <p className="text-sm font-bold text-[var(--foreground)]">Local server import</p>
                  <p className="text-sm text-[var(--muted-foreground)]">
                    Trigger an import from the server path configured in{' '}
                    <Link className="font-medium text-[var(--foreground)] underline underline-offset-2" to="/settings">
                      Settings
                    </Link>
                    .
                  </p>
                </div>
                <div className="rounded-[16px] border border-[var(--border)] px-4 py-3 text-sm space-y-1">
                  <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                    Server path
                  </p>
                  <p className="break-all font-mono text-[var(--foreground)]">{localPath}</p>
                  {localLastAt ? (
                    <p className="text-[var(--muted-foreground)]">
                      Last import {new Date(localLastAt).toLocaleString()}
                      {localLastStatus ? (
                        <span
                          className={
                            localLastStatus.startsWith('ok')
                              ? ' text-[var(--olive-deep)]'
                              : ' text-[var(--danger-text)]'
                          }
                        >
                          {' · '}
                          {localLastStatus}
                        </span>
                      ) : null}
                    </p>
                  ) : null}
                </div>
                {localMessage ? (
                  <div className="flex items-start gap-3 rounded-[16px] border border-[rgba(106,161,54,0.24)] bg-[rgba(106,161,54,0.1)] p-3 text-[var(--olive-deep)]">
                    <CheckCircleIcon className="mt-0.5 h-5 w-5 shrink-0" />
                    <p className="text-sm font-medium">{localMessage}</p>
                  </div>
                ) : null}
                {localError ? <p className="text-sm text-[var(--danger-text)]">{localError}</p> : null}
                <Button variant="outline" onClick={handleLocalImport} disabled={isLocalImporting}>
                  {isLocalImporting ? 'Importing...' : 'Import from server path'}
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <section className="space-y-3">
        <SectionLabel>Recent import status</SectionLabel>
        {showActiveProgress && latestRun ? (
          <ImportProgressCard run={latestRun} now={progressNow} />
        ) : latestRun ? (
          <ImportProgressPanel run={latestRun} compact />
        ) : (
          <div className="rounded-[18px] border border-dashed border-[var(--border)] bg-[var(--surface-soft)] px-4 py-5 text-sm text-[var(--muted-foreground)]">
            No imports yet. Start one from a source above and progress will appear here.
          </div>
        )}
      </section>

      <ImportHistory runs={importRuns} />
    </div>
  )
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <h2 className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--muted-foreground)]">{children}</h2>
  )
}

function SourceTile({
  active,
  icon,
  title,
  description,
  status,
  statusTone = 'default',
  actionLabel,
  onSelect,
  onAction,
}: {
  active: boolean
  icon: ReactNode
  title: string
  description: string
  status: string
  statusTone?: 'default' | 'muted'
  actionLabel: string
  onSelect: () => void
  onAction: () => void
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={active}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect()
        }
      }}
      className={`flex h-full cursor-pointer flex-col gap-3 rounded-[20px] border p-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-border)] ${
        active
          ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]'
          : 'border-[var(--border)] bg-[var(--surface-soft)] hover:border-[var(--accent-border)]'
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--surface-strong)] text-[var(--accent)]">
          {icon}
        </span>
        <p className="font-bold text-[var(--foreground)]">{title}</p>
        {active ? (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-[var(--surface-strong)] px-2 py-0.5 text-[11px] font-bold text-[var(--accent)]">
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
            Selected
          </span>
        ) : null}
      </div>
      <p className="text-sm text-[var(--muted-foreground)]">{description}</p>
      <p
        className={`text-xs font-bold ${statusTone === 'muted' ? 'text-[var(--orange-700)]' : 'text-[var(--foreground)]'}`}
      >
        {status}
      </p>
      <div className="mt-auto pt-1">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={(event) => {
            event.stopPropagation()
            onAction()
          }}
        >
          {actionLabel}
        </Button>
      </div>
    </div>
  )
}

export function ImportProgressCard({ run, now }: { run: ImportRunSummary; now?: number }) {
  return <ImportProgressPanel run={run} now={now} />
}

const HISTORY_PAGE_SIZE = 5

function ImportHistory({ runs }: { runs: ImportRunSummary[] }) {
  const [showAll, setShowAll] = useState(false)
  const visibleRuns = showAll ? runs : runs.slice(0, HISTORY_PAGE_SIZE)
  const hiddenCount = runs.length - visibleRuns.length

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Import history</CardTitle>
        <CardDescription>
          Reviewed CPAP card imports, newest first. Open a row for full diagnostics. Detection-only cards are not listed
          because no import ran.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {runs.length === 0 ? (
          <p className="text-sm text-[var(--muted-foreground)]">No 2.0 import runs have been recorded yet.</p>
        ) : (
          <>
            <div className="hidden grid-cols-[auto_1fr_auto_auto_auto] gap-3 px-3 pb-1 text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)] sm:grid">
              <span>Status</span>
              <span>Device · date</span>
              <span className="text-right">Sessions</span>
              <span className="text-right">Detail</span>
              <span className="text-right">Notes</span>
            </div>
            {visibleRuns.map((run) => (
              <ImportRunRow key={run.id} run={run} />
            ))}
            {runs.length > HISTORY_PAGE_SIZE ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="mt-1"
                onClick={() => setShowAll((value) => !value)}
              >
                {showAll ? 'Show fewer' : `View older imports (${hiddenCount})`}
              </Button>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  )
}

const SUMMARY_ONLY_PATTERN = /summary-only|datalog data/i

function statusBadgeClass(status: ImportRunSummary['status']) {
  if (status === 'success' || status === 'partial') {
    return 'border-[rgba(106,161,54,0.3)] bg-[rgba(106,161,54,0.12)] text-[var(--olive-deep)]'
  }
  if (status === 'failed' || status === 'cancelled') {
    return 'border-[rgba(176,58,46,0.3)] bg-[rgba(176,58,46,0.1)] text-[var(--danger-text)]'
  }
  return 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent)]'
}

function ImportRunRow({ run }: { run: ImportRunSummary }) {
  const machineName =
    [
      run.machine_manufacturer || run.detected_manufacturer,
      run.machine_model || run.machine_family || run.detected_family,
    ]
      .filter(Boolean)
      .join(' ') || 'Unresolved CPAP machine'
  const summaryOnlyCount = run.summary_only_day_count ?? 0
  // The repeated "STR history day without detailed DATALOG data" warnings are represented by the
  // calm Summary-only badge/count instead of being dumped loudly on every row.
  const warningMessages = run.warnings
    .map((warning) => warning.message)
    .filter((message) => !SUMMARY_ONLY_PATTERN.test(message))
  const errorMessages = run.errors.map((error) => error.message)
  const warningCount = warningMessages.length
  const errorCount = errorMessages.length
  const stage = run.current_stage
    ? IMPORT_STAGE_LABELS[run.current_stage] ?? run.current_stage.replaceAll('_', ' ')
    : null

  return (
    <details className="group rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] open:bg-[var(--surface-strong)]">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-3 sm:grid sm:grid-cols-[auto_1fr_auto_auto_auto] [&::-webkit-details-marker]:hidden">
        <span
          className={`inline-flex shrink-0 items-center rounded-full border px-2.5 py-0.5 text-xs font-bold capitalize ${statusBadgeClass(run.status)}`}
        >
          {run.status}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate font-bold text-[var(--foreground)]">{machineName}</span>
          <span className="block truncate text-xs text-[var(--muted-foreground)]">
            {formatImportDateTime(run.completed_at ?? run.started_at)}
          </span>
        </span>
        <span className="hidden text-right text-sm text-[var(--foreground)] sm:block">
          {run.imported_session_count}
        </span>
        <span className="hidden text-right text-xs font-medium capitalize text-[var(--muted-foreground)] sm:block">
          {run.validation_status}
        </span>
        <span className="ml-auto flex items-center gap-1.5 sm:ml-0 sm:justify-end">
          {summaryOnlyCount > 0 ? (
            <span className="rounded-full border border-[rgba(233,120,75,0.3)] bg-[rgba(233,120,75,0.1)] px-2 py-0.5 text-[11px] font-bold text-[var(--orange-700)]">
              {summaryOnlyCount} summary-only
            </span>
          ) : null}
          {errorCount > 0 ? (
            <span className="rounded-full border border-[rgba(176,58,46,0.3)] bg-[rgba(176,58,46,0.1)] px-2 py-0.5 text-[11px] font-bold text-[var(--danger-text)]">
              {errorCount} error{errorCount === 1 ? '' : 's'}
            </span>
          ) : null}
          <span className="text-xs font-bold text-[var(--accent)] group-open:hidden">View details</span>
          <span className="hidden text-xs font-bold text-[var(--accent)] group-open:inline">Hide</span>
        </span>
      </summary>
      <div className="space-y-3 border-t border-[var(--border)] px-3 py-3 text-sm">
        <p className="text-xs text-[var(--muted-foreground)]">
          {run.adapter_id} · {run.source_file_count} source files
        </p>
        {stage || run.current_message ? (
          <div className="rounded-[14px] border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-2">
            {stage ? <p className="font-bold text-[var(--foreground)]">{stage}</p> : null}
            {run.current_message ? (
              <p className="mt-0.5 text-[var(--muted-foreground)]">{run.current_message}</p>
            ) : null}
          </div>
        ) : null}
        <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <InspectionValue label="Validation" value={run.validation_status} />
          <InspectionValue label="Sessions" value={String(run.imported_session_count)} />
          <InspectionValue label="Blocks" value={String(run.imported_block_count)} />
          <InspectionValue label="Events" value={String(run.imported_event_count)} />
          <InspectionValue label="Settings" value={String(run.imported_settings_count ?? 0)} />
          <InspectionValue label="Summary-only" value={String(summaryOnlyCount)} />
        </dl>
        {summaryOnlyCount > 0 ? (
          <div className="rounded-[14px] border border-[rgba(233,120,75,0.28)] bg-[rgba(233,120,75,0.08)] px-3 py-2 text-[var(--orange-700)]">
            <p className="font-bold">Summary-only days detected</p>
            <p className="mt-0.5 text-[var(--muted-foreground)]">
              Some STR history days had no matching detailed DATALOG data. SleepLab kept them as summary-only nights
              instead of deleting them.
            </p>
          </div>
        ) : null}
        {run.capability_status && Object.keys(run.capability_status).length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {Object.entries(run.capability_status).map(([name, status]) => (
              <span
                key={name}
                className="rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-2.5 py-1 text-xs"
              >
                {name.replaceAll('_', ' ')}: {String(status)}
              </span>
            ))}
          </div>
        ) : null}
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
            Source fingerprint
          </p>
          <p className="mt-1 truncate font-mono text-xs text-[var(--muted-foreground)]" title={run.source_fingerprint}>
            {run.source_fingerprint}
          </p>
        </div>
        {errorCount > 0 || warningCount > 0 ? (
          <div className="space-y-1">
            <p className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
              Parser diagnostics
            </p>
            {errorMessages.map((message, index) => (
              <p key={`error-${message}-${index}`} className="text-[var(--danger-text)]">
                {message}
              </p>
            ))}
            {warningMessages.map((message, index) => (
              <p key={`warning-${message}-${index}`} className="text-[var(--orange-700)]">
                {message}
              </p>
            ))}
          </div>
        ) : summaryOnlyCount === 0 ? (
          <p className="text-xs text-[var(--muted-foreground)]">No warnings or errors recorded.</p>
        ) : null}
      </div>
    </details>
  )
}

async function collectSourceFiles(
  directoryHandle: FileSystemDirectoryHandle,
  prefix = '',
): Promise<SelectedImportFile[]> {
  const entries: SelectedImportFile[] = []
  const iterableDirectory = directoryHandle as FileSystemDirectoryHandle & {
    entries(): AsyncIterableIterator<[string, FileSystemHandle]>
  }

  for await (const [name, handle] of iterableDirectory.entries()) {
    if (handle.kind === 'file') {
      const file = await (handle as FileSystemFileHandle).getFile()
      entries.push({
        file,
        relativePath: prefix ? `${prefix}/${name}` : name,
      })
      continue
    }

    const nested = await collectSourceFiles(
      handle as FileSystemDirectoryHandle,
      prefix ? `${prefix}/${name}` : name,
    )
    entries.push(...nested)
  }

  return entries.sort((left, right) => left.relativePath.localeCompare(right.relativePath))
}

/**
 * Helper function for collect edf files from input.
 */
function collectSourceFilesFromInput(files: File[], rootName: string): SelectedImportFile[] {
  return files
    .map((file) => ({
      file,
      relativePath: getRelativePathFromInput(file, rootName),
    }))
    .sort((left, right) => left.relativePath.localeCompare(right.relativePath))
}

/** Helper function for get input root name. */
function getInputRootName(files: File[]) {
  const firstWithPath = files.find((file) => getRelativePathFromInput(file).includes('/'))
  if (firstWithPath) {
    return getRelativePathFromInput(firstWithPath).split('/')[0]
  }

  return 'CPAP-SD'
}

export function LoaderInspectionPanel({
  plan,
  canImport,
  isImporting,
  importStarted,
  onImport,
}: {
  plan: ImportPlanResponse
  canImport: boolean
  isImporting: boolean
  importStarted: boolean
  onImport: () => void
}) {
  const [showSerial, setShowSerial] = useState(false)
  const inspection = plan.inspection
  const detectedMachine =
    inspection.devices
      .map((device) =>
        [device.identity.manufacturer || device.manufacturer_hint, device.identity.model || device.family_hint]
          .filter(Boolean)
          .join(' '),
      )
      .filter(Boolean)
      .join(', ') || 'Machine detected'
  const coverageTotals = plan.devices.reduce(
    (acc, device) => {
      acc.therapyDays += device.coverage.therapy_days
      acc.sessions += device.coverage.estimated_session_blocks
      acc.waveformFiles += device.coverage.waveform_files
      return acc
    },
    { therapyDays: 0, sessions: 0, waveformFiles: 0 },
  )
  const detailLevel = coverageTotals.waveformFiles > 0 ? 'Detailed (with waveforms)' : 'Summary only'
  return (
    <div className="mt-6 space-y-4 border-t border-[var(--border)] pt-6">
      <div>
        <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">Loader result</p>
        <h3 className="mt-1 text-lg font-bold text-[var(--foreground)]">
          {inspection.matched
            ? `${inspection.devices.length} machine${inspection.devices.length === 1 ? '' : 's'} detected`
            : 'Source not recognized'}
        </h3>
      </div>
      {inspection.matched ? (
        <dl className="grid gap-3 rounded-[20px] border border-[var(--border)] bg-[var(--surface-soft)] p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <InspectionValue label="Detected machine" value={detectedMachine} />
          <InspectionValue label="Therapy days" value={String(coverageTotals.therapyDays)} />
          <InspectionValue label="Sessions (est.)" value={String(coverageTotals.sessions)} />
          <InspectionValue label="Waveform files" value={String(coverageTotals.waveformFiles)} />
          <InspectionValue label="Import detail level" value={detailLevel} />
        </dl>
      ) : null}
      <details className="group rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)]">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-bold text-[var(--foreground)] [&::-webkit-details-marker]:hidden">
          <span>Technical details</span>
          <span className="text-xs font-bold text-[var(--accent)] group-open:hidden">Show</span>
          <span className="hidden text-xs font-bold text-[var(--accent)] group-open:inline">Hide</span>
        </summary>
        <div className="space-y-3 border-t border-[var(--border)] px-4 py-3">
          <dl className="grid gap-3 text-sm sm:grid-cols-3">
            <InspectionValue label="Source size" value={formatBytes(plan.source_manifest.total_bytes)} />
            <InspectionValue label="Plan version" value={plan.plan_version} />
            <InspectionValue label="Files staged" value={String(plan.source_manifest.file_count)} />
          </dl>
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
              Source fingerprint
            </p>
            <p
              className="mt-1 truncate font-mono text-xs text-[var(--foreground)]"
              title={plan.source_manifest.fingerprint}
            >
              {plan.source_manifest.fingerprint}
            </p>
          </div>
          {plan.source_manifest.roles.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {plan.source_manifest.roles.map((role) => (
                <span
                  key={role.role}
                  className="rounded-full border border-[var(--border)] bg-[var(--surface-strong)] px-2.5 py-1 text-xs text-[var(--foreground)]"
                >
                  {role.role.replaceAll('_', ' ')}: {role.file_count}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </details>
      {inspection.devices.map((device, index) => {
        const devicePlan = findDevicePlan(plan, device.adapter_id, device.device_path)
        return (
          <div
            key={`${device.adapter_id}-${device.device_path}-${index}`}
            className="space-y-4 rounded-[20px] border border-[var(--border)] bg-[var(--surface-soft)] p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="font-bold text-[var(--foreground)]">
                  {device.identity.manufacturer || device.manufacturer_hint || 'Unknown manufacturer'}
                  {device.identity.model || device.family_hint ? ` ${device.identity.model || device.family_hint}` : ''}
                </p>
                <p className="text-sm text-[var(--muted-foreground)]">
                  {device.adapter_id} | {device.confidence} confidence
                </p>
              </div>
              <span className="rounded-full border border-[var(--accent-border)] bg-[var(--accent-soft)] px-3 py-1 text-xs font-bold text-[var(--accent)]">
                {device.device_path === '.' ? 'Card root' : device.device_path}
              </span>
            </div>
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">Serial</dt>
                <dd className="mt-1 flex items-center gap-2 break-all text-[var(--foreground)]">
                  <span>
                    {showSerial
                      ? device.identity.serial_number || 'Not available'
                      : maskSerial(device.identity.serial_number)}
                  </span>
                  {device.identity.serial_number ? (
                    <button
                      className="text-xs font-bold text-[var(--accent)] underline underline-offset-2"
                      type="button"
                      onClick={() => setShowSerial((value) => !value)}
                    >
                      {showSerial ? 'Hide' : 'Show'}
                    </button>
                  ) : null}
                </dd>
              </div>
              <InspectionValue label="Model number" value={device.identity.model_number} />
              <InspectionValue label="Firmware" value={device.identity.firmware_version} />
              <InspectionValue label="Timezone basis" value={device.timezone_basis} />
            </dl>
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
                Detection evidence
              </p>
              <ul className="mt-2 space-y-1 text-sm text-[var(--foreground)]">
                {device.evidence.map((evidence) => (
                  <li key={`${evidence.kind}-${evidence.relative_path}`}>
                    {evidence.relative_path}: {evidence.observed}
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(device.capabilities).map(([name, capability]) => (
                <span
                  key={name}
                  className="rounded-full border border-[var(--border)] bg-[var(--surface-strong)] px-2.5 py-1 text-xs text-[var(--foreground)]"
                >
                  {name.replaceAll('_', ' ')}: {capability.available ? capability.validation : 'unavailable'}
                </span>
              ))}
            </div>
            {device.warnings.map((warning) => (
              <p key={warning.code} className="text-sm text-[var(--orange-700)]">
                {warning.message}
              </p>
            ))}
            {devicePlan ? <CoveragePanel device={devicePlan} /> : null}
          </div>
        )
      })}
      {inspection.warnings.map((warning) => (
        <p
          key={warning.code}
          className="rounded-[16px] border border-[rgba(233,120,75,0.28)] bg-[rgba(233,120,75,0.08)] px-4 py-3 text-sm text-[var(--orange-700)]"
        >
          {warning.message}
        </p>
      ))}
      {inspection.matched ? (
        <div className="space-y-2">
          <Button onClick={onImport} disabled={!canImport || isImporting}>
            {isImporting ? 'Starting import...' : importStarted ? 'Import started' : 'Import detected data'}
          </Button>
          {!canImport ? (
            <div className="space-y-1 text-sm text-[var(--muted-foreground)]">
              {plan.blockers.map((blocker) => (
                <p key={blocker}>{blocker}</p>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function findDevicePlan(plan: ImportPlanResponse, adapterId: string, devicePath: string) {
  return plan.devices.find((device) => device.adapter_id === adapterId && device.device_path === devicePath)
}

function CoveragePanel({ device }: { device: ImportPlanResponse['devices'][number] }) {
  const coverage = device.coverage
  return (
    <div className="space-y-3 border-t border-[var(--border)] pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">Import coverage</p>
        <span
          className={
            device.execution_status === 'ready'
              ? 'rounded-full bg-[var(--accent-soft)] px-2.5 py-1 text-xs font-bold text-[var(--accent)]'
              : 'rounded-full bg-[rgba(233,120,75,0.1)] px-2.5 py-1 text-xs font-bold text-[var(--orange-700)]'
          }
        >
          {device.execution_status}
        </span>
      </div>
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <InspectionValue
          label="Date range"
          value={coverage.first_date && coverage.last_date ? `${coverage.first_date} to ${coverage.last_date}` : null}
        />
        <InspectionValue label="Therapy days" value={String(coverage.therapy_days)} />
        <InspectionValue label="Estimated blocks" value={String(coverage.estimated_session_blocks)} />
        <InspectionValue label="Event files" value={String(coverage.event_files)} />
        <InspectionValue label="Waveform files" value={String(coverage.waveform_files)} />
        <InspectionValue label="Oximetry files" value={String(coverage.oximetry_files)} />
      </dl>
      {device.execution_backend ? (
        <p className="text-xs text-[var(--muted-foreground)]">Execution backend: {device.execution_backend}</p>
      ) : null}
      {device.blockers.map((blocker) => (
        <p key={blocker} className="text-sm text-[var(--orange-700)]">
          {blocker}
        </p>
      ))}
    </div>
  )
}

function InspectionValue({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">{label}</dt>
      <dd className="mt-1 break-all text-[var(--foreground)]">{value || 'Not available'}</dd>
    </div>
  )
}

function maskSerial(serial: string | null) {
  if (!serial) {
    return 'Not available'
  }
  const visible = serial.slice(-4)
  return `${'*'.repeat(Math.max(4, serial.length - visible.length))}${visible}`
}

function formatImportDate(iso: string | null) {
  if (!iso) return 'Unknown date'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Unknown date'
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatImportDateTime(iso: string | null) {
  if (!iso) return 'Date unavailable'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Date unavailable'
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatBytes(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Helper function for get relative path from input.
 */
function getRelativePathFromInput(file: File, rootName?: string) {
  const rawPath = file.webkitRelativePath || file.name
  if (!rootName) {
    return rawPath
  }

  const prefix = `${rootName}/`
  return rawPath.startsWith(prefix) ? rawPath.slice(prefix.length) : rawPath
}

/**
 * Helper function for supports directory selection.
 */
function supportsDirectorySelection() {
  return 'showDirectoryPicker' in window || supportsWebkitDirectoryInput()
}

/**
 * Helper function for supports webkit directory input.
 */
function supportsWebkitDirectoryInput() {
  return 'webkitdirectory' in document.createElement('input')
}

const DIRECTORY_INPUT_ATTRIBUTES = {
  webkitdirectory: '',
  directory: '',
} as const

declare global {
  interface Window {
    showDirectoryPicker?: () => Promise<FileSystemDirectoryHandle>
  }
}
