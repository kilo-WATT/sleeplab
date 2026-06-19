import type { Meta, StoryObj } from '@storybook/react'
import CalendarPage from './Calendar'
import { api } from '../api/client'
import type { Equipment, SessionSummary } from '../api/client'

function makeSession(overrides: Partial<SessionSummary> & Pick<SessionSummary, 'folder_date'>): SessionSummary {
  return {
    id: `id-${overrides.folder_date}`,
    session_id: `s-${overrides.folder_date}`,
    block_index: 0,
    start_datetime: `${overrides.folder_date}T22:00:00Z`,
    end_datetime: null,
    duration_seconds: 28800,
    duration_hours: 8,
    ahi: 4,
    central_apnea_count: 1,
    obstructive_apnea_count: 4,
    hypopnea_count: 8,
    apnea_count: 5,
    arousal_count: 12,
    total_ahi_events: 20,
    avg_pressure: 10,
    p95_pressure: 12,
    avg_leak: 0.08,
    leak_unit: 'L/s',
    has_spo2: true,
    machine_tz: 'America/New_York',
    ...overrides,
  }
}

// A spread of nights across October 2023 with a deliberate gap (Oct 9–13 missing),
// a short-usage night, a high-leak night, a severe-AHI night, and a summary-only
// night so every marker, filter, and severity color is exercised.
const mockSessions: SessionSummary[] = [
  makeSession({ folder_date: '2023-10-01', ahi: 2.5 }),
  makeSession({ folder_date: '2023-10-02', ahi: 8.5 }),
  makeSession({ folder_date: '2023-10-03', ahi: 18, total_ahi_events: 110 }),
  makeSession({ folder_date: '2023-10-04', ahi: 35, total_ahi_events: 260 }),
  makeSession({ folder_date: '2023-10-05', ahi: 1.2, duration_hours: 2.5, duration_seconds: 9000 }),
  makeSession({ folder_date: '2023-10-06', ahi: 3.1, avg_leak: 0.6 }),
  makeSession({ folder_date: '2023-10-07', ahi: null, avg_pressure: null, avg_leak: null, total_ahi_events: 0 }),
  makeSession({ folder_date: '2023-10-08', ahi: 4.2 }),
  // gap: Oct 9 – Oct 13
  makeSession({ folder_date: '2023-10-14', ahi: 5.6 }),
  makeSession({ folder_date: '2023-10-15', ahi: 3.3 }),
  makeSession({ folder_date: '2023-10-16', ahi: 12.1 }),
  makeSession({ folder_date: '2023-10-17', ahi: 6.7 }),
]

const mockEquipment: Equipment[] = [
  {
    id: 'eq-1',
    equipment_type: 'cushion',
    start_date: '2023-10-14',
    replacement_days: 30,
    mask_category: 'nasal',
    brand: 'ResMed',
    model: 'AirFit N20',
    notes: null,
    days_in_use: 3,
    is_default: true,
    created_at: '2023-10-14T00:00:00Z',
    updated_at: '2023-10-14T00:00:00Z',
  },
]

const meta: Meta<typeof CalendarPage> = {
  title: 'Pages/Calendar',
  component: CalendarPage,
  tags: ['autodocs', 'ai-generated'],
  decorators: [
    (Story, context) => {
      const { mockGetSessions, mockListEquipment, mockGetSessionByDate } = context.parameters
      if (mockGetSessions) api.getSessions = mockGetSessions
      api.listEquipment = mockListEquipment ?? (async () => mockEquipment)
      api.getSessionByDate =
        mockGetSessionByDate ??
        (async (date: string) =>
          ({
            ...mockSessions.find((s) => s.folder_date === date),
            note: '',
            tags: [],
            equipment_overrides: {},
            mask_type: 'Nasal',
            therapy_mode: 'APAP',
            machine_family: 'AirSense',
            machine_model: '11',
            p95_leak: 0.1,
            data_availability: {
              import_backend: 'cpap-parser',
              event_count: 20,
              metric_sample_count: 1000,
              waveform_sample_count: 5000,
              events_available: true,
              therapy_graphs_available: true,
              event_waveforms_available: true,
              event_waveform_source: 'chunks',
              full_night_flow_available: true,
              spo2_available: true,
              settings_available: true,
            },
          }) as never)
      return <Story />
    },
  ],
}

export default meta
type Story = StoryObj<typeof CalendarPage>

export const Populated: Story = {
  parameters: {
    mockGetSessions: async () => mockSessions,
  },
}

export const Loading: Story = {
  parameters: {
    mockGetSessions: () => new Promise(() => {}),
  },
}

export const ErrorState: Story = {
  parameters: {
    mockGetSessions: () => Promise.reject(new Error('Failed to load calendar data. Please check your network connection.')),
  },
}

export const Empty: Story = {
  parameters: {
    mockGetSessions: async () => [],
  },
}

export const SingleDay: Story = {
  parameters: {
    mockGetSessions: async () => [mockSessions[0]],
  },
}
