import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionSummary } from '../api/client'
import CalendarPage from './Calendar'

const apiMock = vi.hoisted(() => ({
  getSessions: vi.fn(),
  listEquipment: vi.fn(),
  getSessionByDate: vi.fn(),
  updateSessionNote: vi.fn(),
}))

vi.mock('../api/client', () => ({
  api: apiMock,
}))

function session(overrides: Partial<SessionSummary> & Pick<SessionSummary, 'folder_date'>): SessionSummary {
  return {
    id: `id-${overrides.folder_date}`,
    session_id: `s-${overrides.folder_date}`,
    block_index: 0,
    start_datetime: `${overrides.folder_date}T22:00:00Z`,
    end_datetime: null,
    duration_seconds: 28800,
    duration_hours: 8,
    ahi: 4,
    central_apnea_count: 0,
    obstructive_apnea_count: 0,
    hypopnea_count: 0,
    apnea_count: 0,
    arousal_count: 0,
    total_ahi_events: 10,
    avg_pressure: 10,
    p95_pressure: 12,
    avg_leak: 0.1,
    leak_unit: 'L/s',
    has_spo2: false,
    machine_tz: 'UTC',
    ...overrides,
  }
}

const sessions: SessionSummary[] = [
  session({ folder_date: '2023-10-04', ahi: 35, total_ahi_events: 260 }),
  session({ folder_date: '2023-10-05', ahi: 1.2 }),
]

function detailFor(date: string) {
  return {
    ...sessions.find((s) => s.folder_date === date),
    note: '',
    tags: [],
    equipment_overrides: {},
    mask_type: 'Nasal',
    therapy_mode: 'APAP',
    p95_leak: 0.1,
    data_availability: {
      import_backend: 'cpap-parser',
      event_count: 10,
      metric_sample_count: 100,
      waveform_sample_count: 500,
      events_available: true,
      therapy_graphs_available: true,
      event_waveforms_available: true,
      event_waveform_source: 'chunks',
      full_night_flow_available: true,
      spo2_available: false,
      settings_available: true,
    },
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <CalendarPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  apiMock.getSessions.mockResolvedValue(sessions)
  apiMock.listEquipment.mockResolvedValue([])
  apiMock.getSessionByDate.mockImplementation(async (date: string) => detailFor(date))
  apiMock.updateSessionNote.mockReset()
})

describe('CalendarPage', () => {
  it('renders the utility bar and a calendar grid', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('night-grid')).toBeInTheDocument())
    expect(screen.getByText('Nights with data')).toBeInTheDocument()
    expect(screen.getByTestId('night-detail-empty')).toBeInTheDocument()
  })

  it('selects a night and lazily loads its detail', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('night-grid')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /2023-10-04/ }))

    await waitFor(() => expect(screen.getByTestId('night-detail')).toBeInTheDocument())
    expect(apiMock.getSessionByDate).toHaveBeenCalledWith('2023-10-04')
    expect(screen.getByTestId('night-detail-severity')).toHaveTextContent('Severe')
    expect(screen.getByRole('button', { name: 'Open session' })).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Open Event Inspector' })).toBeInTheDocument(),
    )
  })

  it('switches the active metric', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('night-grid')).toBeInTheDocument())
    const usageToggle = screen.getByRole('button', { name: 'Usage', pressed: false })
    fireEvent.click(usageToggle)
    expect(screen.getByRole('button', { name: 'Usage', pressed: true })).toBeInTheDocument()
  })

  it('filters the matching-nights list', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('night-grid')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: 'High AHI' }))

    const recent = screen.getByTestId('recent-nights')
    expect(within(recent).getByText('Matching nights')).toBeInTheDocument()
    // Only the severe night (Oct 4) qualifies as high AHI.
    expect(within(recent).getByText('Oct 4')).toBeInTheDocument()
    expect(within(recent).queryByText('Oct 5')).not.toBeInTheDocument()
  })

  it('shows the empty state when nothing is imported', async () => {
    apiMock.getSessions.mockResolvedValue([])
    renderPage()
    await waitFor(() => expect(screen.getByText('No nights imported yet')).toBeInTheDocument())
    expect(screen.getByRole('link', { name: 'Go to import' })).toBeInTheDocument()
  })
})
