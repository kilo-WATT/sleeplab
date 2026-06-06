import { describe, expect, it } from 'vitest'

import { leakToLpm } from './units'

describe('leakToLpm', () => {
  it('normalizes supported source units', () => {
    expect(leakToLpm(0.2, 'L/s')).toBe(12)
    expect(leakToLpm(200, 'mL/s')).toBe(12)
    expect(leakToLpm(12, 'L/min')).toBe(12)
  })

  it('does not guess unknown units', () => {
    expect(leakToLpm(12, 'vendor-points')).toBeNull()
  })
})
