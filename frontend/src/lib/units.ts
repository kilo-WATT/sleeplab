/** Convert SleepLab's stored leak rate from liters/second to liters/minute. */
export function leakToLpm(
  value: number | null | undefined,
  sourceUnit: string | null | undefined = 'L/s',
): number | null {
  if (value == null) return null
  if (sourceUnit === 'L/s') return value * 60
  if (sourceUnit === 'mL/s') return value * 0.06
  if (sourceUnit === 'L/min') return value
  return null
}
