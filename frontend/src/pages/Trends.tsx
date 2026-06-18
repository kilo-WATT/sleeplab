import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import type { AdherenceResponse, OverviewDailyStat, SummaryStats, TrendAISummaryResponse } from '../api/client'
import { api } from '../api/client'
import GlossaryText from '../components/GlossaryText'
import InfoPopover from '../components/InfoPopover'
import Sparkline from '../components/Sparkline'
import { Card, CardContent } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { IMPORT_COMPLETED_EVENT } from '../lib/aiSummaryCache'
import type { InsightChip, StatusTone } from '../lib/trendInsights'
import { deriveInsightChips, deriveNightNotes, LEAK_OK_LPM, meanOf } from '../lib/trendInsights'
import { leakToLpm } from '../lib/units'

/** Soft badge styling per status tone. All tokens adapt to light/dark mode. */
const STATUS_BADGE: Record<StatusTone, string> = {
  good: 'bg-[var(--green-100)] text-[var(--green-700)]',
  watch: 'bg-[var(--yellow-100)] text-[var(--yellow-700)]',
  alert: 'bg-[var(--orange-100)] text-[var(--orange-700)]',
  neutral: 'bg-[var(--surface-muted)] text-[var(--muted-foreground)]',
}

/** Accent color for sparklines / dots per status tone. */
const STATUS_ACCENT: Record<StatusTone, string> = {
  good: 'var(--green-500)',
  watch: 'var(--yellow-500)',
  alert: 'var(--orange-500)',
  neutral: 'var(--muted-foreground)',
}

/** Nights at the end of the range that get visually distinguished as "recent". */
const RECENT_NIGHTS = 7

const TREND_FLAG_COLORS = {
  good: {
    dot: 'bg-[var(--green-500)]',
    badge: 'bg-[rgba(106,161,54,0.12)] text-[var(--green-700)]',
    border: 'border-[rgba(106,161,54,0.35)]',
  },
  watch: {
    dot: 'bg-[var(--orange-500)]',
    badge: 'bg-[rgba(233,120,75,0.12)] text-[var(--orange-700)]',
    border: 'border-[rgba(233,120,75,0.35)]',
  },
  alert: {
    dot: 'bg-[var(--danger-text)]',
    badge: 'bg-[var(--danger-soft)] text-[var(--danger-text)]',
    border: 'border-[var(--accent-border)]',
  },
} as const

/**
 * React component or element to render the t r e n d_ d i r e c t i o n_ l a b e l.
 *
 * @returns The rendered React element.
 */
const TREND_DIRECTION_LABEL: Record<string, string> = {
  improving: 'Improving',
  stable: 'Stable',
  worsening: 'Worsening',
  variable: 'Variable',
}

/**
 * Type definition for the metric key.
 */
type MetricKey = keyof Pick<
  OverviewDailyStat,
  | 'ahi'
  | 'central_apnea_index'
  | 'obstructive_apnea_index'
  | 'hypopnea_index'
  | 'apnea_index'
  | 'arousal_index'
  | 'usage_hours'
  | 'session_start_hour'
  | 'session_end_hour'
  | 'avg_pressure'
  | 'p95_pressure'
  | 'avg_leak'
  | 'large_leak_minutes'
  | 'avg_flow_lim'
  | 'avg_tidal_vol'
  | 'avg_min_vent'
  | 'avg_resp_rate'
  | 'min_spo2'
  | 'avg_spo2'
  | 'avg_pulse'
  | 'equipment_age_days'
>

/**
 * Properties and structure for the trend metric.
 */
interface TrendMetric {
  key: MetricKey
  label: string
  shortLabel: string
  unit: string
  chart: 'line' | 'bar'
  guidance: {
    range: string
    detail: string
    source: string
    sourceUrl: string
  }
  domain?: [number | 'auto', number | 'auto']
  referenceLines?: Array<{ value: number; label: string; color: string; note?: string }>
  precision?: number
  secondaryKey?: MetricKey
  secondaryLabel?: string
}

/**
 * React component or element to render the t r e n d_ m e t r i c s.
 *
 * @returns The rendered React element.
 */
const TREND_METRICS: TrendMetric[] = [
  {
    key: 'ahi',
    label: 'AHI',
    shortLabel: 'AHI',
    unit: 'events/hr',
    chart: 'line',
    guidance: {
      range: 'Under 5 events/hr is generally considered normal; 5-14 mild, 15-29 moderate, and 30+ severe.',
      detail: 'AHI is the combined count of apneas and hypopneas per hour. On treatment, many people aim to keep residual AHI below 5 unless their clinician gives a different target.',
      source: 'Cleveland Clinic',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/apnea-hypopnea-index-ahi',
    },
    referenceLines: [
      { value: 5, label: '5', color: '#6AA136', note: 'controlled' },
      { value: 15, label: '15', color: '#E9784B', note: 'moderate' },
    ],
    precision: 1,
  },
  {
    key: 'central_apnea_index',
    label: 'Central apnea index',
    shortLabel: 'CAI',
    unit: 'events/hr',
    chart: 'bar',
    precision: 1,
    guidance: {
      range: 'A central apnea/hypopnea index around 5 or more per hour is commonly used as a clinical flag for central sleep apnea patterns.',
      detail: 'Small numbers can occur on PAP. Persistent or rising central events are worth discussing with a sleep clinician, especially if they make up a large share of AHI.',
      source: 'AASM / Cleveland Clinic',
      sourceUrl: 'https://pubmed.ncbi.nlm.nih.gov/40820608/',
    },
  },
  {
    key: 'obstructive_apnea_index',
    label: 'Obstructive apnea index',
    shortLabel: 'OAI',
    unit: 'events/hr',
    chart: 'bar',
    precision: 1,
    guidance: {
      range: 'Lower is better. AHI under 5 events/hr is the usual treated-breathing benchmark, and obstructive events are one component of that total.',
      detail: 'If OAI is the main contributor to AHI, it can point toward residual airway obstruction, mask leak, position, or pressure settings to review.',
      source: 'Cleveland Clinic',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/apnea-hypopnea-index-ahi',
    },
  },
  {
    key: 'hypopnea_index',
    label: 'Hypopnea index',
    shortLabel: 'HI',
    unit: 'events/hr',
    chart: 'bar',
    precision: 1,
    guidance: {
      range: 'Lower is better. Hypopneas count toward AHI, where under 5 events/hr is generally considered normal.',
      detail: 'A higher hypopnea index means partial breathing reductions are driving more of the nightly AHI.',
      source: 'Cleveland Clinic',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/apnea-hypopnea-index-ahi',
    },
  },
  {
    key: 'apnea_index',
    label: 'Apnea index',
    shortLabel: 'AI',
    unit: 'events/hr',
    chart: 'bar',
    precision: 1,
    guidance: {
      range: 'Lower is better. This is one component of AHI, and AHI under 5 events/hr is the usual normal benchmark.',
      detail: 'Use this beside CAI/OAI to see whether full pauses are central, obstructive, or mixed in pattern.',
      source: 'Cleveland Clinic',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/apnea-hypopnea-index-ahi',
    },
  },
  {
    key: 'arousal_index',
    label: 'Arousal index',
    shortLabel: 'Arousal',
    unit: 'events/hr',
    chart: 'bar',
    precision: 1,
    guidance: {
      range: 'There is not one universal home-CPAP cutoff for a good arousal index.',
      detail: 'Watch the trend and context. Arousals can fragment sleep even when AHI looks controlled, but interpretation depends on how events were scored.',
      source: 'AASM',
      sourceUrl: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC5337595/',
    },
  },
  {
    key: 'usage_hours',
    label: 'Usage',
    shortLabel: 'Usage',
    unit: 'hours',
    chart: 'line',
    domain: [0, 'auto'],
    precision: 2,
    guidance: {
      range: 'A common adherence benchmark is at least 4 hours per night on at least 70% of nights.',
      detail: 'More full-night use is usually more informative than meeting the minimum insurance-style threshold.',
      source: 'AASM',
      sourceUrl: 'https://aasm.org/resources/pdf/responsetocmsjan.pdf',
    },
  },
  {
    key: 'session_start_hour',
    label: 'Session times',
    shortLabel: 'Times',
    unit: 'clock',
    chart: 'line',
    guidance: {
      range: 'There is no universal good bedtime or wake time in CPAP data.',
      detail: 'Look for consistency, short nights, or schedule changes that line up with AHI, leak, oxygen, or daytime symptoms.',
      source: 'SleepLab trend context',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/apnea-hypopnea-index-ahi',
    },
    domain: [0, 24],
    precision: 2,
    secondaryKey: 'session_end_hour',
    secondaryLabel: 'End',
  },
  {
    key: 'avg_pressure',
    label: 'Average pressure',
    shortLabel: 'Avg pressure',
    unit: 'cmH2O',
    chart: 'line',
    precision: 1,
    guidance: {
      range: 'There is no single good pressure number; useful pressure depends on prescription, machine mode, and airway needs.',
      detail: 'Watch for pressure changes that line up with residual events, leak, comfort problems, or awakenings.',
      source: 'AASM PAP guidance',
      sourceUrl: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC6374094/',
    },
  },
  {
    key: 'p95_pressure',
    label: '95th pressure',
    shortLabel: 'P95 pressure',
    unit: 'cmH2O',
    chart: 'line',
    precision: 1,
    guidance: {
      range: 'There is no universal good 95th pressure; it is a context marker for where pressure spends the high end of the night.',
      detail: 'A rising P95 can reflect more obstruction, position/REM effects, leak response, or pressure range behavior.',
      source: 'AASM PAP guidance',
      sourceUrl: 'https://pmc.ncbi.nlm.nih.gov/articles/PMC6374094/',
    },
  },
  {
    key: 'avg_leak',
    label: 'Leak',
    shortLabel: 'Leak',
    unit: 'L/min',
    chart: 'line',
    precision: 1,
    guidance: {
      range: 'For ResMed-style unintentional leak, staying below about 24 L/min is commonly treated as acceptable.',
      detail: 'Short leak spikes matter less than sustained large leak, especially if events rise or therapy feels worse.',
      source: 'ResMed',
      sourceUrl: 'https://document.resmed.com/documents/us/10114280r1_ResMed_Therapy_Handbook_AMER_Eng_Digital_SinglePages.pdf',
    },
  },
  {
    key: 'large_leak_minutes',
    label: 'Large leak time',
    shortLabel: 'Large leak',
    unit: 'min',
    chart: 'bar',
    domain: [0, 'auto'],
    precision: 1,
    guidance: {
      range: 'Less is better. SleepLab currently counts minutes above 24 L/min as large leak time.',
      detail: 'Sustained large leak can make event detection and delivered pressure less reliable.',
      source: 'ResMed',
      sourceUrl: 'https://document.resmed.com/documents/us/10114280r1_ResMed_Therapy_Handbook_AMER_Eng_Digital_SinglePages.pdf',
    },
  },
  {
    key: 'avg_flow_lim',
    label: 'Flow limitation',
    shortLabel: 'Flow lim',
    unit: '',
    chart: 'line',
    precision: 3,
    guidance: {
      range: 'Lower is generally better, but device-reported flow limitation does not have a universal medical cutoff.',
      detail: 'Use it as a trend: increases can suggest partial airway restriction even when AHI remains low.',
      source: 'SleepLab trend context',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/apnea-hypopnea-index-ahi',
    },
  },
  {
    key: 'avg_tidal_vol',
    label: 'Tidal volume',
    shortLabel: 'Tidal vol',
    unit: 'mL',
    chart: 'line',
    precision: 0,
    guidance: {
      range: 'There is no single good tidal volume in CPAP trend data.',
      detail: 'Interpret in context with minute ventilation, respiratory rate, leak, body size, and sleep stage.',
      source: 'SleepLab trend context',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/10881-vital-signs',
    },
  },
  {
    key: 'avg_min_vent',
    label: 'Minute ventilation',
    shortLabel: 'Min vent',
    unit: 'L/min',
    chart: 'line',
    precision: 1,
    guidance: {
      range: 'There is no universal good minute-ventilation target in home CPAP trend data.',
      detail: 'Look for night-to-night shifts alongside respiratory rate, tidal volume, leak, and oxygen saturation.',
      source: 'SleepLab trend context',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/10881-vital-signs',
    },
  },
  {
    key: 'avg_resp_rate',
    label: 'Respiratory rate',
    shortLabel: 'Resp rate',
    unit: 'breaths/min',
    chart: 'line',
    precision: 1,
    guidance: {
      range: 'Cleveland Clinic lists a normal adult resting respiratory rate around 12-18 breaths per minute.',
      detail: 'Sleeping values can vary. Watch persistent shifts or changes paired with oxygen drops, leaks, or symptoms.',
      source: 'Cleveland Clinic',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/10881-vital-signs',
    },
  },
  {
    key: 'min_spo2',
    label: 'SpO2 minimum',
    shortLabel: 'Min SpO2',
    unit: '%',
    chart: 'line',
    domain: [70, 100],
    precision: 0,
    guidance: {
      range: 'Cleveland Clinic lists 95%-100% as normal for most pulse-oximeter readings.',
      detail: 'A brief minimum can be artifact, but repeated or sustained drops below normal are worth reviewing with a clinician.',
      source: 'Cleveland Clinic',
      sourceUrl: 'https://my.clevelandclinic.org/health/diagnostics/22447-blood-oxygen-level',
    },
  },
  {
    key: 'avg_spo2',
    label: 'SpO2 average',
    shortLabel: 'Avg SpO2',
    unit: '%',
    chart: 'line',
    domain: [70, 100],
    precision: 1,
    guidance: {
      range: 'For most people, pulse-oximeter SpO2 of 95%-100% is considered normal.',
      detail: 'Average SpO2 should be interpreted with minimum SpO2 and time spent low, not as a standalone diagnosis.',
      source: 'Cleveland Clinic',
      sourceUrl: 'https://my.clevelandclinic.org/health/diagnostics/22447-blood-oxygen-level',
    },
  },
  {
    key: 'avg_pulse',
    label: 'Pulse',
    shortLabel: 'Pulse',
    unit: 'bpm',
    chart: 'line',
    precision: 0,
    guidance: {
      range: 'Cleveland Clinic lists normal adult resting pulse around 60-100 bpm.',
      detail: 'Sleep pulse is often lower than daytime resting pulse. Trends are most useful beside SpO2, events, medications, and symptoms.',
      source: 'Cleveland Clinic',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/10881-vital-signs',
    },
  },
  {
    key: 'equipment_age_days',
    label: 'Equipment age',
    shortLabel: 'Equipment',
    unit: 'days',
    chart: 'line',
    domain: [0, 'auto'],
    precision: 0,
    guidance: {
      range: 'Replacement timing depends on the part, wear, insurance schedule, and manufacturer guidance.',
      detail: 'Use this to spot whether leak or comfort worsens as masks, cushions, tubing, filters, or chambers age.',
      source: 'SleepLab equipment context',
      sourceUrl: 'https://my.clevelandclinic.org/health/articles/apnea-hypopnea-index-ahi',
    },
  },
]

const TREND_METRIC_GROUPS = [
  {
    label: 'Events',
    keys: ['ahi', 'central_apnea_index', 'obstructive_apnea_index', 'hypopnea_index', 'apnea_index', 'arousal_index'],
  },
  {
    label: 'Therapy',
    keys: ['usage_hours', 'session_start_hour', 'avg_pressure', 'p95_pressure', 'avg_leak', 'large_leak_minutes'],
  },
  {
    label: 'Breathing',
    keys: ['avg_flow_lim', 'avg_tidal_vol', 'avg_min_vent', 'avg_resp_rate'],
  },
  {
    label: 'Oximetry',
    keys: ['min_spo2', 'avg_spo2', 'avg_pulse'],
  },
  {
    label: 'Equipment',
    keys: ['equipment_age_days'],
  },
] satisfies Array<{ label: string; keys: MetricKey[] }>

const RANGE_OPTIONS = [
  { label: '90D', days: 90 },
  { label: '180D', days: 180 },
  { label: '1Y', days: 365 },
  { label: 'All', days: 3650 },
]

/**
 * Helper function for get metric.
 */
function getMetric(key: MetricKey) {
  return TREND_METRICS.find((metric) => metric.key === key) ?? TREND_METRICS[0]
}

/** Ordered (oldest → newest) non-null values of one metric across nights, for sparklines. */
function seriesOf(nights: OverviewDailyStat[], key: MetricKey): number[] {
  return nights.map((night) => metricNumber(night[key])).filter((value): value is number => value != null)
}

/**
 * React component or element to render the trend a i card.
 *
 * @returns The rendered React element.
 */
function TrendAICard({ chips }: { chips: InsightChip[] }) {
  const [data, setData] = useState<TrendAISummaryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [aiConfigured, setAiConfigured] = useState<boolean | null>(null)
  const [refreshState, setRefreshState] = useState({ token: 0, force: false })
  const [showFull, setShowFull] = useState(false)

  useEffect(() => {
    api.getImportSettings()
      .then((settings) => setAiConfigured(settings.llm_configured))
      .catch(() => setAiConfigured(false))
  }, [])

  useEffect(() => {
    if (aiConfigured !== true) {
      return
    }
    // Reset the AI trend card before fetching fresh analysis for the selected refresh state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    api
      .getTrendAISummary(refreshState.force)
      .then(setData)
      .finally(() => setLoading(false))
  }, [aiConfigured, refreshState])

  if (aiConfigured !== true) {
    return null
  }

  const flag = (data?.flag ?? 'watch') as keyof typeof TREND_FLAG_COLORS
  const colors = TREND_FLAG_COLORS[flag] ?? TREND_FLAG_COLORS.watch
  const directionLabel = data?.trend_direction ? TREND_DIRECTION_LABEL[data.trend_direction] ?? data.trend_direction : null
  const hasDetail = Boolean(
    (data?.high_confidence_observations?.length ?? data?.anomalies?.length ?? 0) > 0 ||
    (data?.possible_patterns?.length ?? 0) > 0 ||
    (data?.things_to_review?.length ?? 0) > 0 ||
    (data?.missing_or_uncertain?.length ?? 0) > 0,
  )

  return (
    <Card className="overflow-hidden border-[var(--border)] bg-[radial-gradient(circle_at_top_left,_rgba(82,81,167,0.10),_transparent_28%),radial-gradient(circle_at_90%_18%,_rgba(106,161,54,0.10),_transparent_20%),var(--surface-strong)]">
      <CardContent className="!p-6 sm:!p-8">
        <div className="flex min-h-10 items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <span className={`inline-block h-2 w-2 rounded-full ${loading ? 'bg-[var(--accent)] animate-pulse' : colors.dot}`} />
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--accent)]">AI Trend Analysis</p>
          </div>
          {!loading && data && !data.error && directionLabel && (
            <div className="flex shrink-0 items-center gap-2">
              <div className={`rounded-full px-3 py-1 text-xs font-bold ${colors.badge}`}>
                {data.cached ? 'Cached' : directionLabel}
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 px-3 text-xs"
                onClick={() => setRefreshState((current) => ({ token: current.token + 1, force: true }))}
              >
                Regenerate
              </Button>
            </div>
          )}
        </div>

        {loading ? (
          <div className="mt-4 space-y-2.5">
            <div className="h-5 w-3/4 animate-pulse rounded bg-[var(--accent-soft)]" />
            <div className="h-4 w-full animate-pulse rounded bg-[var(--accent-soft)]" />
            <div className="h-4 w-5/6 animate-pulse rounded bg-[var(--accent-soft)]" />
          </div>
        ) : data?.error ? (
          <p className="mt-4 text-sm text-[var(--muted-foreground)]">{data.error}</p>
        ) : data?.headline ? (
          <>
            <p className="mt-3 text-lg font-extrabold leading-7 text-[var(--foreground)]">
              <GlossaryText text={data.headline} />
            </p>
            {data.therapy_quality && (
              <p className="mt-3 text-sm leading-6 text-[var(--muted-foreground)]">
                <GlossaryText text={data.therapy_quality} />
              </p>
            )}
            {chips.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {chips.map((chip) => (
                  <span
                    key={chip.label}
                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold ${STATUS_BADGE[chip.tone]}`}
                  >
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: STATUS_ACCENT[chip.tone] }} />
                    {chip.label}
                  </span>
                ))}
              </div>
            )}
            {hasDetail && (
              <>
                {!showFull && (
                  <button
                    type="button"
                    className="mt-4 text-sm font-bold text-[var(--accent)] hover:text-[var(--accent-hover)]"
                    onClick={() => setShowFull(true)}
                    aria-expanded={false}
                  >
                    Read full analysis
                  </button>
                )}
                {showFull && (
                  <div className="mt-4 space-y-5">
                    {(data.high_confidence_observations ?? data.anomalies) && (
                      <div>
                        <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">What we see</p>
                        <ul className={`mt-2 space-y-2 border-l-2 pl-3 ${colors.border}`}>
                          {(data.high_confidence_observations ?? data.anomalies ?? []).map((item) => (
                            <li key={item} className="text-sm leading-6 text-[var(--muted-foreground)]">
                              <GlossaryText text={item} />
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {data.possible_patterns && data.possible_patterns.length > 0 && (
                      <div>
                        <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">What changed recently</p>
                        <ul className={`mt-2 space-y-2 border-l-2 pl-3 ${colors.border}`}>
                          {data.possible_patterns.map((item) => (
                            <li key={item} className="text-sm leading-6 text-[var(--muted-foreground)]">
                              <GlossaryText text={item} />
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {data.things_to_review && data.things_to_review.length > 0 && (
                      <div>
                        <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">Things to review</p>
                        <ul className="mt-2 space-y-2">
                          {data.things_to_review.map((item) => (
                            <li key={item} className="flex items-start gap-2 text-sm leading-6 text-[var(--foreground)]">
                              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent)]" />
                              <GlossaryText text={item} />
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {data.missing_or_uncertain && data.missing_or_uncertain.length > 0 && (
                      <div className="border-l-2 border-[var(--border)] pl-3">
                        <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">Uncertain / missing data</p>
                        <ul className="mt-2 space-y-1.5 text-sm leading-6 text-[var(--muted-foreground)]">
                          {data.missing_or_uncertain.map((item) => (
                            <li key={item}>
                              <GlossaryText text={item} />
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <button
                      type="button"
                      className="text-sm font-bold text-[var(--accent)] hover:text-[var(--accent-hover)]"
                      onClick={() => setShowFull(false)}
                      aria-expanded
                    >
                      Show less
                    </button>
                  </div>
                )}
              </>
            )}
            <p className="mt-5 text-xs text-[var(--muted-foreground)]">
              AI-generated. Not medical advice. Discuss any concerns with your doctor or sleep specialist.
            </p>
          </>
        ) : (
          <p className="mt-4 text-sm text-[var(--muted-foreground)]">AI trend analysis unavailable.</p>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Helper function for ahi tone.
 */
function ahiTone(ahi: number | null) {
  if (ahi == null) return 'text-[var(--muted-foreground)]'
  if (ahi < 5) return 'text-[var(--green-700)]'
  if (ahi < 15) return 'text-[var(--yellow-700)]'
  return 'text-[var(--orange-700)]'
}

/**
 * A single clinical status card: big value, status badge, helper text, and an
 * optional sparkline of recent history. Used for the top-of-page hero metrics.
 */
function HeroMetricCard({
  label,
  value,
  unit,
  helper,
  badge,
  tone,
  series,
}: {
  label: string
  value: string
  unit?: string
  helper: string
  badge: string
  tone: StatusTone
  series: number[]
}) {
  return (
    <Card className="bg-[var(--surface-strong)]">
      <CardContent className="!p-5 sm:!p-6">
        <div className="flex items-start justify-between gap-3">
          <p className="text-sm font-bold text-[var(--foreground)]">{label}</p>
          <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-bold ${STATUS_BADGE[tone]}`}>{badge}</span>
        </div>
        <div className="mt-3 flex items-end justify-between gap-3">
          <p className="text-3xl font-semibold leading-none text-[var(--foreground)] sm:text-4xl">
            {value}
            {unit ? <span className="ml-1 text-base font-bold text-[var(--muted-foreground)]">{unit}</span> : null}
          </p>
          {series.length >= 2 ? (
            <Sparkline values={series} color={STATUS_ACCENT[tone]} className="h-8 w-24 shrink-0" />
          ) : null}
        </div>
        <p className="mt-3 text-xs leading-5 text-[var(--muted-foreground)]">{helper}</p>
      </CardContent>
    </Card>
  )
}

/**
 * The four top-of-page clinical status cards: AHI, compliance, leak, and pressure.
 * Values come from the summary where available and are otherwise derived from the
 * loaded nights (leak is normalized to L/min upstream).
 */
function HeroMetricCards({ summary, nights }: { summary: SummaryStats; nights: OverviewDailyStat[] }) {
  const avgLeak = meanOf(nights.map((night) => night.avg_leak))

  const ahiCardTone: StatusTone = summary.avg_ahi == null ? 'neutral' : summary.avg_ahi < 5 ? 'good' : summary.avg_ahi < 15 ? 'watch' : 'alert'
  const ahiBadge = summary.avg_ahi == null ? 'No data' : summary.avg_ahi < 5 ? 'Controlled' : summary.avg_ahi < 15 ? 'Watch' : 'Elevated'

  const complianceTone: StatusTone = summary.compliance_pct >= 70 ? 'good' : 'watch'
  const complianceBadge = summary.compliance_pct >= 90 ? 'Excellent' : summary.compliance_pct >= 70 ? 'Good' : 'Below target'

  const leakTone: StatusTone = avgLeak == null ? 'neutral' : avgLeak < LEAK_OK_LPM ? 'good' : 'alert'
  const leakBadge = avgLeak == null ? 'No data' : avgLeak < LEAK_OK_LPM ? 'Good' : 'High'

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <HeroMetricCard
        label="Average AHI"
        value={summary.avg_ahi?.toFixed(1) ?? '—'}
        unit={summary.avg_ahi == null ? undefined : '/hr'}
        helper="Below 5 is generally considered controlled."
        badge={ahiBadge}
        tone={ahiCardTone}
        series={seriesOf(nights, 'ahi')}
      />
      <HeroMetricCard
        label="Usage / Compliance"
        value={`${summary.compliance_pct}`}
        unit="%"
        helper="Share of nights meeting the usual 4-hour adherence mark."
        badge={complianceBadge}
        tone={complianceTone}
        series={seriesOf(nights, 'usage_hours')}
      />
      <HeroMetricCard
        label="Average Leak"
        value={avgLeak?.toFixed(1) ?? '—'}
        unit={avgLeak == null ? undefined : 'L/min'}
        helper="Large leaks can reduce therapy accuracy and event detection."
        badge={leakBadge}
        tone={leakTone}
        series={seriesOf(nights, 'avg_leak')}
      />
      <HeroMetricCard
        label="Average Pressure"
        value={summary.avg_pressure?.toFixed(1) ?? '—'}
        unit={summary.avg_pressure == null ? undefined : 'cmH₂O'}
        helper="Typical delivered pressure across recent nights."
        badge="Stable"
        tone="neutral"
        series={seriesOf(nights, 'avg_pressure')}
      />
    </div>
  )
}

function AdherenceStatusBadge({ qualifies, overall = false }: { qualifies: boolean; overall?: boolean }) {
  return (
    <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-bold ${STATUS_BADGE[qualifies ? 'good' : 'watch']}`}>
      {qualifies ? (overall ? 'At benchmark' : 'Qualifies') : (overall ? 'Below benchmark' : 'Does not qualify')}
    </span>
  )
}

function AdherenceStatusCard({
  label,
  percentage,
  detail,
  qualifies,
  overall = false,
}: {
  label: string
  percentage: number
  detail: string
  qualifies: boolean
  overall?: boolean
}) {
  return (
    <div className="rounded-[18px] border border-[var(--border)] bg-[var(--surface-soft)] p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-bold text-[var(--foreground)]">{label}</p>
        <AdherenceStatusBadge qualifies={qualifies} overall={overall} />
      </div>
      <p className="mt-3 text-3xl font-semibold text-[var(--foreground)]">{percentage.toFixed(1)}%</p>
      <p className="mt-2 text-xs leading-5 text-[var(--muted-foreground)]">{detail}</p>
    </div>
  )
}

function AdherenceSection({
  data,
  loading,
  error,
}: {
  data: AdherenceResponse | null
  loading: boolean
  error: string | null
}) {
  if (loading) {
    return (
      <Card aria-labelledby="adherence-heading">
        <CardContent className="px-6 pb-6 pt-7">
          <h2 id="adherence-heading" className="text-lg font-extrabold text-[var(--foreground)]">Adherence</h2>
          <p className="mt-3 text-sm text-[var(--muted-foreground)]">Loading adherence analytics...</p>
        </CardContent>
      </Card>
    )
  }

  if (error || !data) {
    return (
      <Card aria-labelledby="adherence-heading" className="border-[var(--accent-border)]">
        <CardContent className="px-6 pb-6 pt-7">
          <h2 id="adherence-heading" className="text-lg font-extrabold text-[var(--foreground)]">Adherence</h2>
          <p className="mt-3 text-sm font-semibold text-[var(--danger-text)]">Adherence analytics are temporarily unavailable.</p>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">The rest of your trend data is still available. Try refreshing this page.</p>
        </CardContent>
      </Card>
    )
  }

  const { policy, summary, current_window: currentWindow, best_window: bestWindow, streaks } = data
  const overallAtBenchmark = summary.compliance_percent >= policy.required_percent
  const currentDays = data.daily.slice(-policy.window_days)
  const belowThresholdDays = currentDays.filter((day) => day.status === 'noncompliant').length
  const missingDays = currentDays.filter((day) => day.status === 'missing').length
  const policyHours = policy.qualifying_usage_seconds / 3600

  return (
    <Card aria-labelledby="adherence-heading" className="overflow-hidden">
      <CardContent className="px-4 pb-5 pt-5 sm:px-6 sm:pb-6 sm:pt-6">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 id="adherence-heading" className="text-lg font-extrabold text-[var(--foreground)]">Adherence</h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-[var(--muted-foreground)]">
              See how consistently therapy met the fixed usage benchmark across the latest {policy.evaluation_days} calendar days.
            </p>
          </div>
          <span className="w-fit rounded-full bg-[var(--surface-muted)] px-3 py-1.5 text-xs font-bold text-[var(--muted-foreground)]">
            Policy: {policyHours}h · {policy.required_percent}% · {policy.window_days} days within {policy.evaluation_days}
          </span>
        </div>

        {summary.days_with_therapy_data === 0 ? (
          <div className="mt-5 rounded-[18px] border border-dashed border-[var(--border)] bg-[var(--surface-soft)] px-5 py-6 text-center">
            <p className="text-sm font-bold text-[var(--foreground)]">No therapy data in this evaluation period</p>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">All {summary.total_evaluation_days} calendar days are currently counted as missing.</p>
          </div>
        ) : (
          <>
            <div className="mt-5 grid gap-3 lg:grid-cols-3">
              <AdherenceStatusCard
                label={`${policy.evaluation_days}-day adherence`}
                percentage={summary.compliance_percent}
                detail={`${summary.compliant_nights} compliant nights across ${summary.total_evaluation_days} calendar days`}
                qualifies={overallAtBenchmark}
                overall
              />
              <AdherenceStatusCard
                label={`Current ${policy.window_days}-day window`}
                percentage={currentWindow.compliance_percent}
                detail={`${currentWindow.compliant_nights} of ${currentWindow.total_days} nights met ${policyHours} hours`}
                qualifies={currentWindow.qualifies}
              />
              <AdherenceStatusCard
                label={`Best ${policy.window_days}-day window`}
                percentage={bestWindow.compliance_percent}
                detail={`${bestWindow.compliant_nights} of ${bestWindow.total_days} nights · ${bestWindow.start_date} to ${bestWindow.end_date}`}
                qualifies={bestWindow.qualifies}
              />
            </div>

            <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ['Compliant nights', summary.compliant_nights],
                ['Missing nights', summary.missing_nights],
                ['Current streak', `${streaks.current_compliant_nights} nights`],
                ['Longest streak', `${streaks.longest_compliant_nights} nights`],
              ].map(([label, value]) => (
                <div key={label} className="rounded-[16px] bg-[var(--surface-soft)] px-4 py-3">
                  <dt className="text-xs font-bold text-[var(--muted-foreground)]">{label}</dt>
                  <dd className="mt-1 text-lg font-extrabold text-[var(--foreground)]">{value}</dd>
                </div>
              ))}
            </dl>

            <div className="mt-5">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <p className="text-sm font-bold text-[var(--foreground)]">Current window, night by night</p>
                  <p className="text-xs text-[var(--muted-foreground)]">{belowThresholdDays} below {policyHours}h · {missingDays} missing</p>
                </div>
                <div className="flex gap-3 text-xs font-semibold text-[var(--muted-foreground)]">
                  <span>Green: compliant</span>
                  <span>Orange: below {policyHours}h</span>
                  <span>Gray: missing</span>
                </div>
              </div>
              <div
                className="mt-3 flex gap-1"
                role="img"
                aria-label={`Current ${policy.window_days}-day adherence: ${currentWindow.compliant_nights} compliant, ${belowThresholdDays} below threshold, ${missingDays} missing`}
              >
                {currentDays.map((day) => (
                  <span
                    key={day.report_date}
                    className={`h-8 min-w-0 flex-1 rounded-sm ${
                      day.status === 'compliant'
                        ? 'bg-[var(--green-500)]'
                        : day.status === 'noncompliant'
                          ? 'bg-[var(--orange-500)]'
                          : 'border border-[var(--border)] bg-[var(--surface-muted)]'
                    }`}
                    title={`${day.report_date}: ${day.status}${day.usage_seconds == null ? '' : ` (${(day.usage_seconds / 3600).toFixed(1)}h)`}`}
                  />
                ))}
              </div>
            </div>
          </>
        )}

        <p className="mt-5 border-t border-[var(--border)] pt-4 text-xs leading-5 text-[var(--muted-foreground)]">
          This is adherence analytics, not insurer certification. Coverage rules vary; verify requirements with your plan or clinician.
        </p>
      </CardContent>
    </Card>
  )
}

/**
 * Helper function for humanize event type.
 */
function humanizeEventType(eventType: string) {
  return eventType
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

/**
 * Helper function for format metric value.
 */
function formatMetricValue(value: number | null | undefined, metric: TrendMetric) {
  if (value == null) return '-'
  // Leak is already normalized to L/min at ingestion (see normalizeNightLeak).
  if (metric.unit === 'clock') return formatClockHour(value)
  const precision = metric.precision ?? 1
  const formatted = value.toFixed(precision)
  return metric.unit ? `${formatted} ${metric.unit}` : formatted
}

/**
 * Helper function for format metric range.
 */
function formatMetricRange(low: number, high: number, metric: TrendMetric) {
  if (metric.unit === 'clock') {
    return `${formatClockHour(low)} / ${formatClockHour(high)}`
  }

  // Leak is already normalized to L/min at ingestion (see normalizeNightLeak).
  const precision = metric.precision ?? 1
  const lowValue = low.toFixed(precision)
  const highValue = high.toFixed(precision)
  return metric.unit ? `${lowValue} / ${highValue} ${metric.unit}` : `${lowValue} / ${highValue}`
}

/**
 * Helper function for format metric delta.
 */
function formatMetricDelta(value: number | null | undefined, metric: TrendMetric) {
  if (value == null) return '-'
  const sign = value >= 0 ? '+' : ''
  if (metric.unit === 'clock') return `${sign}${value.toFixed(2)} hr`
  return `${sign}${formatMetricValue(value, metric)}`
}

/**
 * Helper function for rounds to zero.
 */
function roundsToZero(value: number, metric: TrendMetric) {
  const precision = metric.precision ?? 1
  return Number(value.toFixed(precision)) === 0
}

/**
 * Helper function for format clock hour.
 */
function formatClockHour(hour: number) {
  const normalized = ((hour % 24) + 24) % 24
  const wholeHours = Math.floor(normalized)
  const minutes = Math.round((normalized - wholeHours) * 60)
  const displayHours = minutes === 60 ? (wholeHours + 1) % 24 : wholeHours
  const displayMinutes = minutes === 60 ? 0 : minutes
  const suffix = displayHours >= 12 ? 'PM' : 'AM'
  const hour12 = displayHours % 12 || 12
  return `${hour12}:${String(displayMinutes).padStart(2, '0')} ${suffix}`
}

/**
 * Helper function for get active session id.
 */
function getActiveSessionId(payload: unknown) {
  if (!payload || typeof payload !== 'object' || !('activePayload' in payload)) {
    return null
  }
  const activePayload = (payload as { activePayload?: Array<{ payload?: { sessionId?: unknown } }> }).activePayload
  const sessionId = activePayload?.[0]?.payload?.sessionId
  return typeof sessionId === 'string' ? sessionId : null
}

/**
 * Helper function for metric number.
 */
function metricNumber(value: OverviewDailyStat[MetricKey]) {
  return typeof value === 'number' ? value : null
}

/**
 * In-chart caption for the recent-nights highlight band. Recharts clones this element
 * with the band's `viewBox`, so we anchor the text to the top-right edge of the band
 * (where it overlaps the empty top of the plot) and keep it small to avoid hiding the line.
 */
function RecentBandLabel({ viewBox }: { viewBox?: { x?: number; y?: number; width?: number; height?: number } }) {
  const { x = 0, y = 0, width = 0 } = viewBox ?? {}
  return (
    <text x={x + width - 6} y={y + 12} textAnchor="end" fontSize={10} fontWeight={700} fill="var(--accent)">
      Recent {RECENT_NIGHTS} nights
    </text>
  )
}

/**
 * Normalize a night's leak to L/min using its own leak_unit, so every downstream
 * aggregate (average, min/max, chart, table) works in a single unit. Legacy nights
 * are stored in L/s and parser nights in L/min; converting here once avoids the old
 * blanket leakToLpm() that assumed L/s and inflated parser nights 60x.
 */
function normalizeNightLeak(night: OverviewDailyStat): OverviewDailyStat {
  return { ...night, avg_leak: leakToLpm(night.avg_leak, night.leak_unit), leak_unit: 'L/min' }
}

/**
 * Helper function for calculate metric summary.
 */
function calculateMetricSummary(nights: OverviewDailyStat[], metric: TrendMetric) {
  const points = nights
    .map((night) => ({
      night,
      value: metricNumber(night[metric.key]),
    }))
    .filter((point): point is { night: OverviewDailyStat; value: number } => point.value != null)

  if (points.length === 0) {
    return null
  }

  const average = points.reduce((sum, point) => sum + point.value, 0) / points.length
  const latest = points[points.length - 1]
  const lowest = points.reduce((best, point) => point.value < best.value ? point : best, points[0])
  const highest = points.reduce((best, point) => point.value > best.value ? point : best, points[0])
  const recent = points.slice(-7)
  const previous = points.slice(Math.max(0, points.length - 14), Math.max(0, points.length - 7))
  const recentAverage = recent.reduce((sum, point) => sum + point.value, 0) / recent.length
  const previousAverage = previous.length > 0
    ? previous.reduce((sum, point) => sum + point.value, 0) / previous.length
    : null
  const change = previousAverage == null ? null : recentAverage - previousAverage

  return { average, latest, lowest, highest, change }
}

/**
 * React component or element to render the metric summary cards.
 *
 * @returns The rendered React element.
 */
function MetricSummaryCards({ nights, metric }: { nights: OverviewDailyStat[]; metric: TrendMetric }) {
  const summary = calculateMetricSummary(nights, metric)

  if (!summary) {
    return (
      <div className="mb-5 rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-4 text-sm text-[var(--muted-foreground)]">
        No values are available for this metric in the selected range.
      </div>
    )
  }

  const changeLabel = summary.change == null
    ? 'Not enough history'
    : roundsToZero(summary.change, metric)
      ? 'No meaningful change'
      : `${formatMetricDelta(summary.change, metric)} vs prior 7 nights`

  return (
    <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <div className="rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-4">
        <p className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">Latest</p>
        <p className="mt-2 text-lg font-extrabold text-[var(--foreground)]">{formatMetricValue(summary.latest.value, metric)}</p>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">{summary.latest.night.folder_date}</p>
      </div>
      <div className="rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-4">
        <p className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">Average</p>
        <p className="mt-2 text-lg font-extrabold text-[var(--foreground)]">{formatMetricValue(summary.average, metric)}</p>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">{nights.length} nights selected</p>
      </div>
      <div className="rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-4">
        <p className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">Low / High</p>
        <p className="mt-2 text-lg font-extrabold text-[var(--foreground)]">
          {formatMetricRange(summary.lowest.value, summary.highest.value, metric)}
        </p>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">
          {summary.lowest.night.folder_date} / {summary.highest.night.folder_date}
        </p>
      </div>
      <div className="rounded-[16px] border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-4">
        <p className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">Recent shift</p>
        <p className="mt-2 text-lg font-extrabold text-[var(--foreground)]">{changeLabel}</p>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">7-night average comparison</p>
      </div>
    </div>
  )
}

/**
 * React component or element to render the overview chart.
 *
 * @returns The rendered React element.
 */
function OverviewChart({
  nights,
  metric,
}: {
  nights: OverviewDailyStat[]
  metric: TrendMetric
}) {
  const navigate = useNavigate()
  const data = nights.map((night) => ({
    ...night,
    date: night.folder_date,
    primary: night[metric.key],
    secondary: metric.secondaryKey ? night[metric.secondaryKey] : null,
  }))
  const recentStart = data.length > RECENT_NIGHTS ? data[data.length - RECENT_NIGHTS].date : null
  const lastDate = data.length > 0 ? data[data.length - 1].date : null
  const hasLegend = (metric.referenceLines?.length ?? 0) > 0 || recentStart != null

  return (
    <Card id="long-range-overview">
      <CardContent className="px-4 pb-5 pt-5 sm:px-6 sm:pt-6">
        <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <p className="text-base font-bold text-[var(--foreground)]">{metric.label}</p>
              <InfoPopover title={`${metric.label} guidance`}>
                <div className="space-y-2">
                  <p>{metric.guidance.range}</p>
                  <p>{metric.guidance.detail}</p>
                  <a
                    href={metric.guidance.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex font-bold text-[var(--accent)] hover:text-[var(--accent-hover)]"
                  >
                    Source: {metric.guidance.source}
                  </a>
                  <p className="text-xs leading-5">General education only. Use your clinician's target when it differs.</p>
                </div>
              </InfoPopover>
            </div>
            <p className="text-sm text-[var(--muted-foreground)]">{nights.length} nights in the selected range</p>
          </div>
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--accent)]">{metric.unit || 'Index'}</p>
        </div>
        <MetricSummaryCards nights={nights} metric={metric} />
        <ResponsiveContainer width="100%" height={320}>
          <ComposedChart
            data={data}
            margin={{ top: 12, right: 16, bottom: 0, left: 0 }}
            onClick={(payload) => {
              const sessionId = getActiveSessionId(payload)
              if (sessionId) {
                navigate(`/sessions/${sessionId}`)
              }
            }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="var(--neutral-200)" />
            <XAxis
              dataKey="date"
              tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }}
              tickFormatter={(value: string) => value.slice(5)}
              minTickGap={24}
            />
            <YAxis
              tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }}
              domain={metric.domain ?? [0, 'auto']}
              tickFormatter={(value) => metric.unit === 'clock' ? formatClockHour(Number(value)).replace(':00 ', ' ') : String(value)}
              width={54}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--popover-surface)',
                border: '1px solid var(--border)',
                borderRadius: 14,
                color: 'var(--foreground)',
              }}
              labelStyle={{ color: 'var(--foreground)' }}
              formatter={(value, name) => {
                const label = name === 'secondary' ? (metric.secondaryLabel ?? 'End') : metric.shortLabel
                const numericValue = typeof value === 'number' ? value : null
                const formattedValue = formatMetricValue(numericValue, metric)
                return [formattedValue, label]
              }}
            />
            {recentStart && lastDate ? (
              <ReferenceArea
                x1={recentStart}
                x2={lastDate}
                fill="var(--accent-soft)"
                fillOpacity={1}
                stroke="var(--accent)"
                strokeOpacity={0.3}
                strokeDasharray="3 3"
                label={<RecentBandLabel />}
              />
            ) : null}
            {metric.referenceLines?.map((line) => (
              <ReferenceLine
                key={line.value}
                y={line.value}
                stroke={line.color}
                strokeDasharray="4 4"
                label={{ value: line.label, fill: line.color, fontSize: 10 }}
              />
            ))}
            {metric.chart === 'bar' ? (
              <Bar dataKey="primary" name={metric.shortLabel} fill="#5251A7" radius={[6, 6, 0, 0]} maxBarSize={28} />
            ) : (
              <Line
                type="monotone"
                dataKey="primary"
                name={metric.shortLabel}
                stroke="#5251A7"
                dot={false}
                strokeWidth={2}
                connectNulls
              />
            )}
            {metric.secondaryKey ? (
              <Line
                type="monotone"
                dataKey="secondary"
                name={metric.secondaryLabel ?? 'End'}
                stroke="#6AA136"
                dot={false}
                strokeWidth={2}
                connectNulls
              />
            ) : null}
          </ComposedChart>
        </ResponsiveContainer>
        {hasLegend && (
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-[var(--muted-foreground)]">
            {metric.referenceLines?.map((line) => (
              <span key={line.value} className="inline-flex items-center gap-1.5">
                <span className="inline-block h-0 w-4 border-t-2 border-dashed" style={{ borderColor: line.color }} />
                {metric.shortLabel} {line.value}{line.note ? ` · ${line.note}` : ''}
              </span>
            ))}
            {recentStart && (
              <span className="inline-flex items-center gap-1.5">
                <span
                  className="inline-block h-3 w-3 rounded-sm border border-dashed bg-[var(--accent-soft)]"
                  style={{ borderColor: 'var(--accent-border)' }}
                />
                Recent {RECENT_NIGHTS} nights
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * React component or element to render the recent overview table.
 *
 * @returns The rendered React element.
 */
function RecentOverviewTable({ nights }: { nights: OverviewDailyStat[] }) {
  const navigate = useNavigate()
  const recent = nights.slice(-10).reverse()
  const leakMetric = getMetric('avg_leak')

  return (
    <Card id="overview-table">
      <CardContent className="px-0 pb-2 pt-5 sm:pt-6">
        <div className="px-5 sm:px-6">
          <p className="text-sm font-bold text-[var(--foreground)]">Recent nights</p>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">Most recent nights with key event and therapy values. Select a row to open the night.</p>
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[680px] border-collapse text-sm">
            <thead>
              <tr className="border-y border-[var(--border)] bg-[var(--surface-soft)] text-left text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
                <th className="px-5 py-3 sm:px-6">Date</th>
                <th className="px-5 py-3 sm:px-6">AHI</th>
                <th className="px-5 py-3 sm:px-6">CAI</th>
                <th className="px-5 py-3 sm:px-6">OAI</th>
                <th className="px-5 py-3 sm:px-6">Usage</th>
                <th className="px-5 py-3 sm:px-6">Leak</th>
                <th className="px-5 py-3 sm:px-6">Notes</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((night) => {
                const notes = deriveNightNotes(night)
                return (
                  <tr
                    key={night.folder_date}
                    className="cursor-pointer border-b border-[var(--border)] transition-colors last:border-b-0 hover:bg-[var(--surface-soft)] focus-visible:bg-[var(--surface-soft)] focus-visible:outline-none"
                    tabIndex={0}
                    role="link"
                    aria-label={`Open night ${night.folder_date}`}
                    onClick={() => navigate(`/sessions/${night.session_id}`)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        navigate(`/sessions/${night.session_id}`)
                      }
                    }}
                  >
                    <td className="px-5 py-3 font-bold text-[var(--foreground)] sm:px-6">{night.folder_date}</td>
                    <td className={`px-5 py-3 font-bold sm:px-6 ${ahiTone(night.ahi)}`}>{night.ahi == null ? '—' : night.ahi.toFixed(1)}</td>
                    <td className="px-5 py-3 text-[var(--muted-foreground)] sm:px-6">{night.central_apnea_index == null ? '—' : night.central_apnea_index.toFixed(1)}</td>
                    <td className="px-5 py-3 text-[var(--muted-foreground)] sm:px-6">{night.obstructive_apnea_index == null ? '—' : night.obstructive_apnea_index.toFixed(1)}</td>
                    <td className="px-5 py-3 text-[var(--muted-foreground)] sm:px-6">{night.usage_hours.toFixed(2)} h</td>
                    <td className="px-5 py-3 text-[var(--muted-foreground)] sm:px-6">{formatMetricValue(night.avg_leak, leakMetric)}</td>
                    <td className="px-5 py-3 sm:px-6">
                      {notes.length === 0 ? (
                        <span className="text-[var(--muted-foreground)]">—</span>
                      ) : (
                        <span className="flex flex-wrap gap-1.5">
                          {notes.map((note) => (
                            <span key={note.label} className={`rounded-full px-2 py-0.5 text-xs font-bold ${STATUS_BADGE[note.tone]}`}>
                              {note.label}
                            </span>
                          ))}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * React component or element to render the respiratory event breakdown as ranked bars,
 * each showing the count and its percentage share of all detected events.
 */
function EventBreakdown({ breakdown }: { breakdown: Array<[string, number]> }) {
  const total = breakdown.reduce((sum, [, count]) => sum + count, 0)
  const max = breakdown.reduce((best, [, count]) => Math.max(best, count), 0)

  return (
    <Card id="event-breakdown">
      <CardContent className="!p-6 sm:!p-8">
        <p className="text-sm font-bold text-[var(--foreground)]">Respiratory event breakdown</p>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">Counts of the breathing-event types detected across your imported nights.</p>
        {breakdown.length === 0 ? (
          <p className="mt-4 text-sm text-[var(--muted-foreground)]">No respiratory events were detected in the imported nights.</p>
        ) : (
          <ul className="mt-5 space-y-4">
            {breakdown.map(([eventType, count]) => {
              const pct = total > 0 ? (count / total) * 100 : 0
              const width = max > 0 ? (count / max) * 100 : 0
              return (
                <li key={eventType}>
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="text-sm font-bold text-[var(--foreground)]">{humanizeEventType(eventType)}</p>
                    <p className="text-sm text-[var(--muted-foreground)]">
                      <span className="font-bold text-[var(--foreground)]">{count.toLocaleString()}</span>
                      <span className="ml-2">{pct.toFixed(1)}%</span>
                    </p>
                  </div>
                  <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-[var(--surface-soft)]">
                    <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${width}%` }} />
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * React component or element to render the trends page.
 *
 * @returns The rendered React element.
 */
export default function TrendsPage() {
  const [summary, setSummary] = useState<SummaryStats | null>(null)
  const [overview, setOverview] = useState<OverviewDailyStat[]>([])
  const [adherence, setAdherence] = useState<AdherenceResponse | null>(null)
  const [adherenceLoading, setAdherenceLoading] = useState(true)
  const [adherenceError, setAdherenceError] = useState<string | null>(null)
  const [rangeDays, setRangeDays] = useState(180)
  const [metricKey, setMetricKey] = useState<MetricKey>('ahi')
  const [showAllMetrics, setShowAllMetrics] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const metric = getMetric(metricKey)

  useEffect(() => {
    async function loadTrends() {
      try {
        const [data, overviewData] = await Promise.all([
          api.getSummary(),
          api.getOverviewStats(rangeDays),
        ])
        setSummary(data)
        setOverview(overviewData.nights.map(normalizeNightLeak))
        setError(null)
      } catch (err) {
        setError(String(err))
      } finally {
        setLoading(false)
      }
    }

    void loadTrends()

    function handleImportCompleted() {
      setLoading(true)
      void loadTrends()
    }

    window.addEventListener(IMPORT_COMPLETED_EVENT, handleImportCompleted)
    return () => window.removeEventListener(IMPORT_COMPLETED_EVENT, handleImportCompleted)
  }, [rangeDays])

  useEffect(() => {
    let cancelled = false

    async function loadAdherence() {
      setAdherenceLoading(true)
      try {
        const data = await api.getAdherence()
        if (!cancelled) {
          setAdherence(data)
          setAdherenceError(null)
        }
      } catch {
        if (!cancelled) {
          setAdherence(null)
          setAdherenceError('Could not load adherence analytics')
        }
      } finally {
        if (!cancelled) setAdherenceLoading(false)
      }
    }

    void loadAdherence()

    function handleImportCompleted() {
      void loadAdherence()
    }

    window.addEventListener(IMPORT_COMPLETED_EVENT, handleImportCompleted)
    return () => {
      cancelled = true
      window.removeEventListener(IMPORT_COMPLETED_EVENT, handleImportCompleted)
    }
  }, [])

  if (loading) {
    return <div className="rounded-[22px] border border-[var(--border)] bg-[var(--surface-strong)] p-10 text-center text-[var(--muted-foreground)]">Loading trends...</div>
  }

  if (error || !summary) {
    return <div className="rounded-[22px] border border-[var(--accent-border)] bg-[var(--danger-soft)] p-10 text-center text-[var(--danger-text)]">Error loading trends: {error ?? 'Unknown error'}</div>
  }

  const sortedBreakdown = Object.entries(summary.event_breakdown)
    .sort((left, right) => right[1] - left[1])

  const chips = deriveInsightChips(overview, summary.avg_ahi)
  const rangeLabel = RANGE_OPTIONS.find((option) => option.days === rangeDays)?.label ?? `${rangeDays}D`
  const firstNight = overview[0]?.folder_date ?? null
  const lastNight = overview[overview.length - 1]?.folder_date ?? null

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-[var(--foreground)] sm:text-3xl">Trends</h1>
          <p className="mt-1 text-sm text-[var(--muted-foreground)] sm:text-base">
            Long-range view of your PAP therapy over the last {rangeDays} days.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs font-bold text-[var(--muted-foreground)]">
          <span className="rounded-full bg-[var(--surface-soft)] px-3 py-1">{overview.length} nights selected</span>
          {firstNight && lastNight && (
            <span className="rounded-full bg-[var(--surface-soft)] px-3 py-1">{firstNight} → {lastNight}</span>
          )}
          <span className="rounded-full bg-[var(--surface-soft)] px-3 py-1">{summary.nights_with_data} nights with data</span>
          <span className="rounded-full bg-[var(--surface-soft)] px-3 py-1">Range: {rangeLabel}</span>
        </div>
      </header>

      <TrendAICard chips={chips} />

      <HeroMetricCards summary={summary} nights={overview} />

      <AdherenceSection data={adherence} loading={adherenceLoading} error={adherenceError} />

      <Card>
        <CardContent className="px-4 pb-5 pt-5 sm:px-6 sm:pt-6">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="text-sm font-bold text-[var(--foreground)]">Long-range overview</p>
              <p className="mt-1 text-sm text-[var(--muted-foreground)]">Pick a range and metric to scan nightly therapy patterns over time.</p>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-end">
              <div className="grid grid-cols-4 rounded-full border border-[var(--border)] bg-[var(--surface-soft)] p-1" role="group" aria-label="Date range">
                {RANGE_OPTIONS.map((option) => (
                  <button
                    key={option.days}
                    type="button"
                    className={`rounded-full px-3 py-2 text-sm font-bold transition ${
                      rangeDays === option.days
                        ? 'bg-[var(--surface-strong)] text-[var(--accent)]'
                        : 'text-[var(--muted-foreground)] hover:text-[var(--foreground)]'
                    }`}
                    onClick={() => {
                      setLoading(true)
                      setRangeDays(option.days)
                    }}
                    aria-pressed={rangeDays === option.days}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <select
                className="h-11 min-w-56 rounded-full border border-[var(--border)] bg-[var(--surface-strong)] px-4 text-sm font-bold text-[var(--foreground)] outline-none focus:border-[var(--accent-border)]"
                value={metricKey}
                onChange={(event) => setMetricKey(event.target.value as MetricKey)}
                aria-label="Trend metric"
                title="Select a trend metric"
              >
                {TREND_METRIC_GROUPS.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.keys.map((key) => {
                      const option = getMetric(key)
                      return <option key={option.key} value={option.key}>{option.label}</option>
                    })}
                  </optgroup>
                ))}
              </select>
              <Button
                variant="outline"
                size="sm"
                className="h-11"
                onClick={() => setShowAllMetrics((open) => !open)}
                aria-expanded={showAllMetrics}
              >
                {showAllMetrics ? 'Hide metrics' : 'More metrics'}
              </Button>
            </div>
          </div>
          {showAllMetrics && (
            <div className="mt-5 grid gap-4 lg:grid-cols-[1.1fr_1fr] xl:grid-cols-[1.1fr_1fr_0.75fr]">
              {TREND_METRIC_GROUPS.map((group) => (
                <div key={group.label} className="min-w-0">
                  <p className="mb-2 text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">{group.label}</p>
                  <div className="flex flex-wrap gap-2">
                    {group.keys.map((key) => {
                      const option = getMetric(key)
                      return (
                        <Button
                          key={option.key}
                          variant={metricKey === option.key ? 'default' : 'outline'}
                          size="sm"
                          className="h-8 px-3 text-xs sm:h-9 sm:text-sm"
                          onClick={() => setMetricKey(option.key)}
                        >
                          {option.shortLabel}
                        </Button>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {overview.length > 0 ? (
        <>
          <OverviewChart nights={overview} metric={metric} />
          <RecentOverviewTable nights={overview} />
        </>
      ) : (
        <Card>
          <CardContent className="px-6 pb-6 pt-7 text-center text-sm text-[var(--muted-foreground)]">
            No trend rows are available for this range.
          </CardContent>
        </Card>
      )}

      <EventBreakdown breakdown={sortedBreakdown} />
    </div>
  )
}
