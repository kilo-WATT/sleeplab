import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ImportPlanResponse, ImportRunSummary } from '../api/client'
import OximeterImportSummary from '../components/OximeterImportSummary'
import { shouldDismissImportRunOnNavigation, shouldPollImportRuns } from '../components/importProgress'
import { collectOximeterFilesFromInput } from '../lib/oximeterFiles'
import ImportPage, { ImportProgressCard, LoaderInspectionPanel, SourceInspectedCallout } from './Import'

const { mockGetImportRuns, mockGetImportSettings, mockDiscardSourceUpload } = vi.hoisted(() => ({
  mockGetImportRuns: vi.fn(),
  mockGetImportSettings: vi.fn(),
  mockDiscardSourceUpload: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      getImportRuns: mockGetImportRuns,
      getImportSettings: mockGetImportSettings,
      discardSourceUpload: mockDiscardSourceUpload,
    },
  }
})

function file(name: string) {
  return new File(['data'], name, { type: 'application/octet-stream' })
}

describe('collectOximeterFilesFromInput', () => {
  it('keeps extensionless Viatom filenames', () => {
    const result = collectOximeterFilesFromInput([file('20250115220000'), file('notes.txt'), file('.DS_Store')])

    expect(result.map((entry) => entry.name)).toEqual(['20250115220000'])
  })

  it('keeps common binary export extensions', () => {
    const result = collectOximeterFilesFromInput([file('o2-night.bin'), file('1745080270764.dat'), file('report.csv')])

    expect(result.map((entry) => entry.name)).toEqual(['1745080270764.dat', 'o2-night.bin'])
  })
})

describe('OximeterImportSummary', () => {
  it('renders imported, skipped, unmatched, and failed result states', () => {
    render(
      <OximeterImportSummary
        result={{
          status: 'partial',
          message: 'Some oximeter files could not be imported.',
          imported: 1,
          skipped: 1,
          unmatched: 1,
          failed: 1,
          results: [
            {
              filename: 'imported.bin',
              status: 'imported',
              message: 'Imported oximeter data',
              sample_count: 2,
            },
            {
              filename: 'skipped.bin',
              status: 'skipped',
              message: 'Session already has SpO2 data',
            },
            {
              filename: 'unmatched.bin',
              status: 'unmatched',
              message: 'No existing CPAP session overlaps this oximeter recording',
            },
            {
              filename: 'failed.bin',
              status: 'failed',
              message: 'Unsupported Viatom/Wellue signature',
            },
          ],
        }}
      />,
    )

    expect(screen.getByText('imported.bin')).toBeInTheDocument()
    expect(screen.getByText('Some oximeter files could not be imported.')).toBeInTheDocument()
    expect(screen.getByText('skipped.bin')).toBeInTheDocument()
    expect(screen.getByText('unmatched.bin')).toBeInTheDocument()
    expect(screen.getByText('failed.bin')).toBeInTheDocument()
  })
})

describe('LoaderInspectionPanel', () => {
  it('shows a detected non-ResMed machine without enabling import', () => {
    render(
      <LoaderInspectionPanel
        plan={{
          plan_version: '2.0-alpha-1',
          source_root: 'PHILIPS-SD',
          source_manifest: {
            fingerprint: 'sha256:test-only',
            file_count: 2,
            total_bytes: 64,
            roles: [{ role: 'identity', file_count: 1, size_bytes: 32 }],
          },
          inspection: {
            source_root: 'PHILIPS-SD',
            matched: true,
            ambiguous: false,
            warnings: [],
            devices: [
              {
                adapter_id: 'philips-prs1-v2',
                adapter_version: '0.1',
                device_path: 'P-Series/P012345',
                device_key_hint: 'P012345',
                manufacturer_hint: 'Philips Respironics',
                family_hint: 'PRS1',
                confidence: 'strong',
                requires_user_choice: false,
                competing_adapter_ids: [],
                evidence: [
                  {
                    kind: 'required_path',
                    relative_path: 'P-Series/P012345/PROP.TXT',
                    expected: 'PRS1 machine properties',
                    observed: 'file',
                    weight: 80,
                  },
                ],
                identity: {
                  manufacturer: 'Philips Respironics',
                  family: 'PRS1',
                  model: null,
                  model_number: '560P',
                  serial_number: 'TEST-PRS1',
                  firmware_version: '1.2',
                  data_format_version: null,
                  confidence: 'exact',
                },
                capabilities: {
                  identity: {
                    available: true,
                    validation: 'partial',
                    notes: 'Prototype',
                  },
                  sessions: {
                    available: false,
                    validation: 'unvalidated',
                    notes: 'Prototype',
                  },
                },
                timezone_basis: 'machine_local',
                leak_kinds: ['total'],
                warnings: [],
              },
            ],
          },
          devices: [
            {
              adapter_id: 'philips-prs1-v2',
              device_path: 'P-Series/P012345',
              execution_status: 'blocked',
              execution_backend: null,
              coverage: {
                first_date: null,
                last_date: null,
                therapy_days: 0,
                estimated_session_blocks: 0,
                waveform_files: 0,
                event_files: 0,
                oximetry_files: 0,
                settings_files: 0,
              },
              blockers: ['Detection and planning are available, but this adapter does not implement execution yet.'],
              warnings: [],
            },
          ],
          executable: false,
          blockers: ['Detection and planning are available, but this adapter does not implement execution yet.'],
        }}
      />,
    )

    // The machine name now appears in both the user-facing summary and the device detail section.
    expect(screen.getAllByText('Philips Respironics PRS1').length).toBeGreaterThan(0)
    expect(screen.getByText('P-Series/P012345/PROP.TXT: file')).toBeInTheDocument()
    expect(screen.getByText('*****PRS1')).toBeInTheDocument()
    expect(screen.queryByText('TEST-PRS1')).not.toBeInTheDocument()
    // The import action moved out of the loader result into the inspected callout above it.
    expect(screen.queryByRole('button', { name: 'Import detected data' })).not.toBeInTheDocument()
  })

  it('puts the primary import action above the loader result after inspection', () => {
    const plan: ImportPlanResponse = {
          plan_version: '2.0-alpha-1',
          source_root: 'RESMED-SD',
          source_manifest: {
            fingerprint: 'sha256:resmed-only',
            file_count: 120,
            total_bytes: 4096,
            roles: [{ role: 'waveform', file_count: 82, size_bytes: 2048 }],
          },
          inspection: {
            source_root: 'RESMED-SD',
            matched: true,
            ambiguous: false,
            warnings: [],
            devices: [
              {
                adapter_id: 'resmed-cpap-parser-v1',
                adapter_version: '0.1',
                device_path: '.',
                device_key_hint: null,
                manufacturer_hint: 'ResMed',
                family_hint: 'AirSense',
                confidence: 'exact',
                requires_user_choice: false,
                competing_adapter_ids: [],
                evidence: [],
                identity: {
                  manufacturer: 'ResMed',
                  family: 'AirSense',
                  model: 'AirSense 10 AutoSet',
                  model_number: '370',
                  serial_number: null,
                  firmware_version: '1.0',
                  data_format_version: null,
                  confidence: 'exact',
                },
                capabilities: {},
                timezone_basis: 'machine_local',
                leak_kinds: ['total'],
                warnings: [],
              },
            ],
          },
          devices: [
            {
              adapter_id: 'resmed-cpap-parser-v1',
              device_path: '.',
              execution_status: 'ready',
              execution_backend: 'python',
              coverage: {
                first_date: '2026-01-01',
                last_date: '2026-02-15',
                therapy_days: 46,
                estimated_session_blocks: 46,
                waveform_files: 82,
                event_files: 46,
                oximetry_files: 0,
                settings_files: 1,
              },
              blockers: [],
              warnings: [],
            },
          ],
          executable: true,
          blockers: [],
    }
    render(
      <div>
        <SourceInspectedCallout
          plan={plan}
          canImport={true}
          isImporting={false}
          importStarted={false}
          onImport={() => {}}
          onReinspect={() => {}}
        />
        <LoaderInspectionPanel plan={plan} />
      </div>,
    )

    const importButtons = screen.getAllByRole('button', { name: 'Import detected data' })
    expect(importButtons).toHaveLength(1)
    const importButton = importButtons[0]
    expect(importButton).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Re-inspect card' })).toBeInTheDocument()

    // The primary action must appear before the loader result and technical details in document order.
    const loaderResult = screen.getByText('Loader result')
    const technicalDetails = screen.getByText('Technical details')
    expect(importButton.compareDocumentPosition(loaderResult) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(importButton.compareDocumentPosition(technicalDetails) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('disables import and surfaces blockers when the source is not executable', () => {
    const plan = {
      inspection: { matched: true },
      blockers: ['Detection and planning are available, but this adapter does not implement execution yet.'],
    } as unknown as ImportPlanResponse
    render(
      <SourceInspectedCallout
        plan={plan}
        canImport={false}
        isImporting={false}
        importStarted={false}
        onImport={() => {}}
        onReinspect={() => {}}
      />,
    )

    expect(screen.getByRole('button', { name: 'Import detected data' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Re-inspect card' })).toBeInTheDocument()
    expect(screen.getByText(/does not implement execution yet/i)).toBeInTheDocument()
  })
})

function importRun(status: 'running' | 'success' | 'failed') {
  return {
    id: 'run-1',
    adapter_id: 'resmed-cpap-parser-v1',
    adapter_version: '0.1',
    source_type: 'uploaded_root',
    source_fingerprint: 'sha256:test',
    source_label: 'SD card',
    status,
    validation_status: status === 'failed' ? 'failed' as const : 'partial' as const,
    detected_manufacturer: 'ResMed',
    detected_family: 'AirSense',
    detected_capabilities: {},
    warnings: [],
    errors: [],
    skipped_files: [],
    imported_session_count: status === 'success' ? 3 : 0,
    imported_block_count: 0,
    imported_event_count: 0,
    imported_channel_count: 0,
    started_at: '2026-06-13T12:00:00Z',
    completed_at: status === 'running' ? null : '2026-06-13T12:02:00Z',
    machine_id: null,
    machine_manufacturer: 'ResMed',
    machine_family: 'AirSense',
    machine_model: null,
    machine_product_code: null,
    machine_serial_number: null,
    machine_firmware_version: null,
    machine_support_status: 'experimental' as const,
    machine_validation_status: 'partial' as const,
    source_file_count: 20,
  }
}

describe('ImportProgressCard', () => {
  it('renders active staged progress with a real session percentage', () => {
    render(
      <ImportProgressCard
        now={new Date('2026-06-13T12:01:05Z').getTime()}
        run={{
          ...importRun('running'),
          current_stage: 'building_waveform_chunks',
          current_message: 'Building compressed waveform chunks.',
          sessions_processed: 2,
          sessions_total: 5,
        }}
      />,
    )

    expect(screen.getByText('Synchronizing sleep data')).toBeInTheDocument()
    expect(screen.getByText('Building waveform chunks')).toBeInTheDocument()
    expect(screen.getByText('Elapsed 1:05')).toBeInTheDocument()
    expect(screen.getByText('Sessions')).toBeInTheDocument()
    expect(screen.getByText('2 of 5')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '40')
    expect(screen.getByText('40%')).toBeInTheDocument()
  })

  it('renders success and failure states clearly', () => {
    const { rerender } = render(<ImportProgressCard run={importRun('success')} />)
    expect(screen.getByText('Import complete')).toBeInTheDocument()

    rerender(
      <ImportProgressCard
        run={{
          ...importRun('failed'),
          current_stage: 'failed',
          current_message: 'Parser exited unexpectedly.',
        }}
      />,
    )
    expect(screen.getByText('Import failed')).toBeInTheDocument()
    expect(screen.getByText('Parser exited unexpectedly.')).toBeInTheDocument()
  })

  it('stops polling after a durable import reaches a terminal state', () => {
    expect(shouldPollImportRuns(importRun('running'), true)).toBe(true)
    expect(shouldPollImportRuns(importRun('success'), true)).toBe(false)
    expect(shouldPollImportRuns(importRun('failed'), true)).toBe(false)
    expect(shouldPollImportRuns(undefined, true)).toBe(true)
  })

  it('dismisses completed notices on navigation but keeps active progress visible', () => {
    expect(shouldDismissImportRunOnNavigation(importRun('success'), '/import', '/dashboard')).toBe(true)
    expect(shouldDismissImportRunOnNavigation(importRun('running'), '/import', '/dashboard')).toBe(false)
    expect(shouldDismissImportRunOnNavigation(importRun('success'), '/dashboard', '/dashboard')).toBe(false)
  })
})

function historyRun(): ImportRunSummary {
  return {
    ...importRun('success'),
    id: 'run-history',
    status: 'partial',
    completed_at: '2026-06-15T08:30:00Z',
    imported_session_count: 4,
    summary_only_day_count: 3,
    warnings: [
      {
        code: 'summary_only',
        severity: 'info',
        message: 'STR history day without detailed DATALOG data; imported as summary-only, not deleted.',
        relative_path: null,
        affects: [],
      },
      {
        code: 'clock_drift',
        severity: 'warning',
        message: 'Device clock drift detected during import.',
        relative_path: null,
        affects: [],
      },
    ],
    errors: [{ code: 'event_parse', message: 'Failed to parse one event file.' }],
  }
}

async function renderImportCenter(runs: ImportRunSummary[] = []) {
  mockGetImportRuns.mockResolvedValue(runs)
  mockGetImportSettings.mockResolvedValue({
    local_datalog_path: null,
    last_local_import_at: null,
    last_local_import_status: null,
    sleephq_enabled: false,
  })
  mockDiscardSourceUpload.mockResolvedValue({ status: 'ok' })
  const utils = render(
    <MemoryRouter>
      <ImportPage />
    </MemoryRouter>,
  )
  await screen.findByText('CPAP SD card')
  return utils
}

describe('Import center', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the three import source cards', async () => {
    await renderImportCenter()

    expect(screen.getByText('CPAP SD card')).toBeInTheDocument()
    expect(screen.getByText('O2 Ring')).toBeInTheDocument()
    expect(screen.getByText('SleepHQ')).toBeInTheDocument()
    expect(screen.getByText('Import sources')).toBeInTheDocument()
  })

  it('shows CPAP import controls by default', async () => {
    await renderImportCenter()

    expect(screen.getByText('CPAP SD card import')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Inspect card' })).toBeInTheDocument()
  })

  it('reveals O2 import controls when the O2 Ring source is selected', async () => {
    await renderImportCenter()

    fireEvent.click(screen.getByText('O2 Ring'))

    expect(screen.getByText('O2 Ring import')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Import O2 data' })).toBeInTheDocument()
    expect(screen.queryByText('CPAP SD card import')).not.toBeInTheDocument()
  })

  it('reveals SleepHQ import controls when the SleepHQ source is selected', async () => {
    await renderImportCenter()

    fireEvent.click(screen.getByText('SleepHQ'))

    expect(screen.getByText('SleepHQ import')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Import now' }).length).toBeGreaterThan(0)
  })

  it('renders import history as compact rows collapsed by default', async () => {
    const { container } = await renderImportCenter([historyRun()])

    const details = container.querySelector('details')
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute('open')
  })

  it('keeps parser diagnostics collapsed and replaces summary-only noise with a calm badge', async () => {
    const { container } = await renderImportCenter([historyRun()])

    // The repeated raw DATALOG warning never renders; it is summarized by a calm badge instead.
    expect(screen.queryByText(/without detailed DATALOG data/i)).not.toBeInTheDocument()
    expect(screen.getByText('3 summary-only')).toBeInTheDocument()

    // Full technical diagnostics live inside the collapsed disclosure, available on expand.
    const details = container.querySelector('details')!
    expect(within(details).getByText('Failed to parse one event file.')).toBeInTheDocument()
    expect(within(details).getByText('Summary-only days detected')).toBeInTheDocument()
    expect(within(details).getByText('Device clock drift detected during import.')).toBeInTheDocument()
  })
})
