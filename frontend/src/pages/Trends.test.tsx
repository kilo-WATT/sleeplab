import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AdherenceResponse, SummaryStats } from '../api/client'
import TrendsPage from './Trends'

const {
  mockGetSummary,
  mockGetOverviewStats,
  mockGetAdherence,
  mockGetImportSettings,
  mockGetTrendAISummary,
} = vi.hoisted(() => ({
  mockGetSummary: vi.fn(),
  mockGetOverviewStats: vi.fn(),
  mockGetAdherence: vi.fn(),
  mockGetImportSettings: vi.fn(),
  mockGetTrendAISummary: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      getSummary: mockGetSummary,
      getOverviewStats: mockGetOverviewStats,
      getAdherence: mockGetAdherence,
      getImportSettings: mockGetImportSettings,
      getTrendAISummary: mockGetTrendAISummary,
    },
  }
})

const summary: SummaryStats = {
  total_nights: 90,
  nights_with_data: 78,
  compliance_pct: 73.3,
  avg_ahi: 2.1,
  avg_pressure: 10.4,
  ahi_trend: [],
  event_breakdown: {},
}

function adherenceResponse({
  currentQualifies = true,
  bestQualifies = true,
  daysWithData = 78,
}: {
  currentQualifies?: boolean
  bestQualifies?: boolean
  daysWithData?: number
} = {}): AdherenceResponse {
  const currentCompliant = currentQualifies ? 23 : 18
  const bestCompliant = bestQualifies ? 26 : 20
  const noData = daysWithData === 0

  return {
    policy: {
      qualifying_usage_seconds: 14400,
      required_percent: 70,
      window_days: 30,
      evaluation_days: 90,
    },
    summary: {
      start_date: '2026-03-21',
      end_date: '2026-06-18',
      total_evaluation_days: 90,
      days_with_therapy_data: daysWithData,
      compliant_nights: noData ? 0 : 66,
      missing_nights: noData ? 90 : 12,
      noncompliant_nights_with_data: noData ? 0 : 12,
      compliance_percent: noData ? 0 : 73.3,
    },
    current_window: {
      start_date: '2026-05-20',
      end_date: '2026-06-18',
      compliant_nights: noData ? 0 : currentCompliant,
      total_days: 30,
      compliance_percent: noData ? 0 : Number(((currentCompliant / 30) * 100).toFixed(1)),
      qualifies: noData ? false : currentQualifies,
    },
    best_window: {
      start_date: '2026-04-15',
      end_date: '2026-05-14',
      compliant_nights: noData ? 0 : bestCompliant,
      total_days: 30,
      compliance_percent: noData ? 0 : Number(((bestCompliant / 30) * 100).toFixed(1)),
      qualifies: noData ? false : bestQualifies,
    },
    streaks: {
      current_compliant_nights: noData ? 0 : 4,
      longest_compliant_nights: noData ? 0 : 11,
    },
    daily: Array.from({ length: 30 }, (_, index) => ({
      report_date: `2026-05-${String(index + 1).padStart(2, '0')}`,
      usage_seconds: noData ? null : index < currentCompliant ? 25200 : index < 25 ? 10800 : null,
      status: noData
        ? 'missing' as const
        : index < currentCompliant
          ? 'compliant' as const
          : index < 25
            ? 'noncompliant' as const
            : 'missing' as const,
    })),
    rolling_windows: [],
  }
}

function renderTrends() {
  return render(
    <MemoryRouter>
      <TrendsPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetSummary.mockResolvedValue(summary)
  mockGetOverviewStats.mockResolvedValue({ nights: [] })
  mockGetAdherence.mockResolvedValue(adherenceResponse())
  mockGetImportSettings.mockResolvedValue({ llm_configured: false })
  mockGetTrendAISummary.mockResolvedValue({ error: null })
})

describe('Trends adherence analytics', () => {
  it('renders policy, counts, windows, and streaks from the adherence API', async () => {
    renderTrends()

    expect(await screen.findByRole('heading', { name: 'Adherence' })).toBeInTheDocument()
    expect(screen.getByText('Policy: 4h · 70% · 30 days within 90')).toBeInTheDocument()
    expect(screen.getByText('66')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('4 nights')).toBeInTheDocument()
    expect(screen.getByText('11 nights')).toBeInTheDocument()
    expect(screen.getByText('This is adherence analytics, not insurer certification. Coverage rules vary; verify requirements with your plan or clinician.')).toBeInTheDocument()
  })

  it('shows qualifying status clearly for current and best windows', async () => {
    renderTrends()

    const currentCard = (await screen.findByText('Current 30-day window')).parentElement?.parentElement
    const bestCard = screen.getByText('Best 30-day window').parentElement?.parentElement

    expect(currentCard).not.toBeNull()
    expect(bestCard).not.toBeNull()
    expect(within(currentCard as HTMLElement).getByText('Qualifies')).toBeInTheDocument()
    expect(within(bestCard as HTMLElement).getByText('Qualifies')).toBeInTheDocument()
  })

  it('shows non-qualifying status clearly', async () => {
    mockGetAdherence.mockResolvedValue(adherenceResponse({ currentQualifies: false, bestQualifies: false }))
    renderTrends()

    const currentCard = (await screen.findByText('Current 30-day window')).parentElement?.parentElement
    const bestCard = screen.getByText('Best 30-day window').parentElement?.parentElement

    expect(within(currentCard as HTMLElement).getByText('Does not qualify')).toBeInTheDocument()
    expect(within(bestCard as HTMLElement).getByText('Does not qualify')).toBeInTheDocument()
  })

  it('shows a contained loading state while the rest of Trends remains available', async () => {
    mockGetAdherence.mockReturnValue(new Promise(() => {}))
    renderTrends()

    expect(await screen.findByRole('heading', { name: 'Trends' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Adherence' })).toBeInTheDocument()
    expect(screen.getByText('Loading adherence analytics...')).toBeInTheDocument()
  })

  it('shows a contained error state when adherence cannot load', async () => {
    mockGetAdherence.mockRejectedValue(new Error('network unavailable'))
    renderTrends()

    expect(await screen.findByText('Adherence analytics are temporarily unavailable.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Trends' })).toBeInTheDocument()
  })

  it('explains an empty 90-day evaluation period', async () => {
    mockGetAdherence.mockResolvedValue(adherenceResponse({ daysWithData: 0 }))
    renderTrends()

    expect(await screen.findByText('No therapy data in this evaluation period')).toBeInTheDocument()
    expect(screen.getByText('All 90 calendar days are currently counted as missing.')).toBeInTheDocument()
  })
})
