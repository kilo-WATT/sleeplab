import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Equipment } from '../api/client'
import EquipmentCatalog from './EquipmentCatalog'

const apiMock = vi.hoisted(() => ({
  listEquipment: vi.fn(),
  createEquipment: vi.fn(),
  updateEquipment: vi.fn(),
  setDefaultEquipment: vi.fn(),
  deleteEquipment: vi.fn(),
}))

vi.mock('../api/client', () => ({
  api: apiMock,
}))

function equipment(overrides: Partial<Equipment>): Equipment {
  return {
    id: 'id',
    equipment_type: 'cushion',
    start_date: '2025-06-01',
    replacement_days: 15,
    mask_category: null,
    brand: null,
    model: null,
    notes: null,
    days_in_use: 5,
    is_default: false,
    created_at: '2025-06-01T00:00:00Z',
    updated_at: '2025-06-01T00:00:00Z',
    ...overrides,
  }
}

const ITEMS: Equipment[] = [
  // On-track so the mask appears only in the hero, not in "Needs attention".
  equipment({ id: 'mask', model: 'AirFit P10', mask_category: 'Nasal Pillows', is_default: true, replacement_days: 90, days_in_use: 20 }),
  equipment({ id: 'filter', equipment_type: 'filter', model: 'Standard filter', replacement_days: 30, days_in_use: 42 }),
  equipment({ id: 'tubing', equipment_type: 'tubing', model: 'ClimateLineAir', replacement_days: 90, days_in_use: 85 }),
]

describe('EquipmentCatalog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.listEquipment.mockResolvedValue(ITEMS)
  })

  it('shows the active mask in the current-setup hero', async () => {
    render(<EquipmentCatalog />)
    expect(await screen.findByText('Current setup')).toBeInTheDocument()
    expect(screen.getByText('AirFit P10')).toBeInTheDocument()
    // Color-independent status text for the due-soon tubing.
    expect(screen.getAllByText(/Due in 5 days/).length).toBeGreaterThan(0)
  })

  it('summarizes due-soon and overdue counts and lists every category', async () => {
    render(<EquipmentCatalog />)
    await screen.findByText('AirFit P10')

    // Overdue filter (42d used of 30d) surfaces in Needs attention.
    expect(screen.getByText('Needs attention')).toBeInTheDocument()
    expect(screen.getAllByText(/Overdue by 12 days/).length).toBeGreaterThan(0)

    // All categories render even when empty.
    for (const label of ['Cushion / Pillow', 'Headgear', 'Tubing', 'Water Chamber', 'Filter']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    // Empty categories offer a clear, working "Add <type>" affordance.
    expect(screen.getByText(/Add headgear/)).toBeInTheDocument()
  })

  it('gives every summary card a real secondary line', async () => {
    render(<EquipmentCatalog />)
    await screen.findByText('AirFit P10')

    // Renamed, clearer label for the most-recent-activity card.
    expect(screen.getByText('Last equipment log')).toBeInTheDocument()

    // All four cards carry an accurate secondary line (no top-heavy blanks).
    expect(screen.getByText('1 in use')).toBeInTheDocument()
    expect(screen.getByText('upcoming replacement')).toBeInTheDocument()
    expect(screen.getByText('needs replacing now')).toBeInTheDocument()
    expect(screen.getByText('latest activity')).toBeInTheDocument()
  })

  it('logs a replacement that preserves the model and starts today', async () => {
    apiMock.createEquipment.mockResolvedValue(equipment({ id: 'new-mask', model: 'AirFit P10', is_default: false }))
    apiMock.setDefaultEquipment.mockResolvedValue(equipment({ id: 'new-mask', is_default: true }))
    render(<EquipmentCatalog />)
    await screen.findByText('AirFit P10')

    // Open the replacement flow from the hero.
    fireEvent.click(screen.getAllByRole('button', { name: 'Log replacement' })[0])
    expect(await screen.findByText(/Replacing/)).toBeInTheDocument()

    // Submit the form (last matching button is the form's submit).
    const submits = screen.getAllByRole('button', { name: 'Log replacement' })
    fireEvent.click(submits[submits.length - 1])

    await waitFor(() => expect(apiMock.createEquipment).toHaveBeenCalledTimes(1))
    const payload = apiMock.createEquipment.mock.calls[0][0]
    expect(payload.equipment_type).toBe('cushion')
    expect(payload.model).toBe('AirFit P10')
    expect(payload.start_date).toBe(new Date().toISOString().slice(0, 10))
  })
})
