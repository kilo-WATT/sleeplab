import type { OverviewDailyStat } from '../api/client'

/** Coarse clinical status used for badges, chips, and note flags across the Trends page. */
export type StatusTone = 'good' | 'watch' | 'alert' | 'neutral'

/** Threshold below which residual leak is generally treated as acceptable (ResMed-style). */
export const LEAK_OK_LPM = 24

/** An at-a-glance status chip derived from the loaded data. */
export interface InsightChip {
  label: string
  tone: StatusTone
}

/** A short, factual flag describing something notable about a single night. */
export interface NightNote {
  label: string
  tone: StatusTone
}

/** Mean of the non-null numbers in a list, or null when there are none. */
export function meanOf(values: Array<number | null | undefined>): number | null {
  const present = values.filter((value): value is number => value != null)
  if (present.length === 0) return null
  return present.reduce((sum, value) => sum + value, 0) / present.length
}

/**
 * Derive a short, factual set of status chips from the loaded nights. These summarize
 * the current picture (AHI control, central events, leak, oximetry availability) without
 * overclaiming when a signal is missing.
 */
export function deriveInsightChips(nights: OverviewDailyStat[], avgAhi: number | null): InsightChip[] {
  const chips: InsightChip[] = []

  if (avgAhi != null) {
    if (avgAhi < 5) chips.push({ label: 'AHI controlled', tone: 'good' })
    else if (avgAhi < 15) chips.push({ label: 'AHI mildly elevated', tone: 'watch' })
    else chips.push({ label: 'AHI elevated', tone: 'alert' })
  }

  const avgCentral = meanOf(nights.map((night) => night.central_apnea_index))
  if (avgCentral != null && avgCentral >= 5) {
    chips.push({ label: 'Central apnea elevated', tone: 'alert' })
  }

  const avgLeak = meanOf(nights.map((night) => night.avg_leak))
  const hasLargeLeak = nights.some((night) => (night.large_leak_minutes ?? 0) > 0)
  if (avgLeak != null && avgLeak >= LEAK_OK_LPM) {
    chips.push({ label: 'Leaks high', tone: 'alert' })
  } else if (hasLargeLeak) {
    chips.push({ label: 'Leaks intermittent', tone: 'watch' })
  } else if (avgLeak != null) {
    chips.push({ label: 'Leaks controlled', tone: 'good' })
  }

  const hasSpo2 = nights.some((night) => night.avg_spo2 != null || night.min_spo2 != null)
  if (!hasSpo2) {
    chips.push({ label: 'SpO₂ unavailable', tone: 'neutral' })
  } else {
    const minSpo2 = meanOf(nights.map((night) => night.min_spo2))
    if (minSpo2 != null && minSpo2 < 88) chips.push({ label: 'SpO₂ dips low', tone: 'watch' })
  }

  return chips.slice(0, 4)
}

/** Derive note badges for one night without overclaiming on missing signals. */
export function deriveNightNotes(night: OverviewDailyStat): NightNote[] {
  const notes: NightNote[] = []
  const leak = night.avg_leak
  const ahi = night.ahi
  const cai = night.central_apnea_index

  if (night.usage_hours < 4) notes.push({ label: 'Short session', tone: 'watch' })
  if (leak != null && leak >= LEAK_OK_LPM) notes.push({ label: 'High leak', tone: 'alert' })
  if (cai != null && ahi != null && ahi > 0 && cai / ahi >= 0.5 && cai >= 2) {
    notes.push({ label: 'More CA', tone: 'watch' })
  }
  if (leak != null && leak < 5) notes.push({ label: 'Low leak', tone: 'good' })
  return notes
}
