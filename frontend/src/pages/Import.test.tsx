import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import OximeterImportSummary from '../components/OximeterImportSummary'
import { collectOximeterFilesFromInput } from '../lib/oximeterFiles'
import { LoaderInspectionPanel } from './Import'

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
        canImport={false}
        isImporting={false}
        importStarted={false}
        onImport={() => {}}
      />,
    )

    expect(screen.getByText('Philips Respironics PRS1')).toBeInTheDocument()
    expect(screen.getByText('P-Series/P012345/PROP.TXT: file')).toBeInTheDocument()
    expect(screen.getByText('*****PRS1')).toBeInTheDocument()
    expect(screen.queryByText('TEST-PRS1')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Import detected data' })).toBeDisabled()
    expect(screen.getAllByText(/does not implement execution yet/i)[0]).toBeInTheDocument()
  })
})
