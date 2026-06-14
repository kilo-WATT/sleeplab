import { useEffect, useState, type FormEvent } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api } from '../api/client'
import { getDisplayTz } from '../lib/displayTz'
import { leakToLpm } from '../lib/units'
import type { SessionDetail as SessionDetailType, EventRecord, MetricsResponse, SpO2Response, InferredEquipment, WearableData, SessionTherapyContext, MachineSettingsSnapshot, WaveformSignalResponse } from '../api/client'
import FullNightFlowChart from '../components/FullNightFlowChart'
import WearableSleepStageChart from '../components/WearableSleepStageChart'
import { ChevronLeftIcon, ChevronRightIcon } from '../components/icons/ChevronIcons'
import EventTimeline from '../components/EventTimeline'
import MetricsChart from '../components/MetricsChartSplit'
import { computeMetricsDomain, metricsToPoints } from '../components/metricsChartDomain'
import SpO2Chart from '../components/SpO2Chart'
import SessionAICard from '../components/SessionAICard'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import { Input } from '../components/ui/input'
import { Label } from '../components/ui/label'
import { getSessionNavigation, type SessionNavigation } from './sessionNavigation'
import { SESSION_TAGS } from '../lib/constants'

/**
 * Helper function for fmt date.
 */
function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', timeZone: getDisplayTz() })
}

/**
 * Helper function for fmt time.
 */
function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: getDisplayTz() })
}


/**
 * Helper function for ahi badge.
 */
function ahiBadge(ahi: number | null): { label: string; className: string } {
  if (ahi === null) return { label: 'No data', className: 'bg-[var(--surface-soft)] text-[var(--muted-foreground)]' }
  if (ahi < 5)  return { label: 'Good night', className: 'bg-[rgba(106,161,54,0.14)] text-[var(--green-700)]' }
  if (ahi < 15) return { label: 'Mild night', className: 'bg-[rgba(201,183,21,0.14)] text-[var(--yellow-700)]' }
  if (ahi < 30) return { label: 'Rough night', className: 'bg-[rgba(233,120,75,0.14)] text-[var(--orange-700)]' }
  return { label: 'Difficult night', className: 'bg-[var(--danger-soft)] text-[var(--danger-text)]' }
}

function sameTags(a: string[], b: string[]) {
  if (a.length !== b.length) return false
  const left = [...a].sort()
  const right = [...b].sort()
  return left.every((tag, index) => tag === right[index])
}

/**
 * React component or element to render the e v e n t_ c o d e s.
 *
 * @returns The rendered React element.
 */
const EVENT_CODES: Record<string, string> = {
  'Central Apnea': 'CA',
  'Obstructive Apnea': 'OA',
  'Hypopnea': 'H',
  'Apnea': 'A',
  'Arousal': 'RE',
  'Large Leak': 'LL',
}

/**
 * React component or element to render the e v e n t_ c o l o r s.
 *
 * @returns The rendered React element.
 */
const EVENT_COLORS: Record<string, string> = {
  'Central Apnea': '#5251A7',
  'Obstructive Apnea': '#8E3D40',
  'Hypopnea': '#E9784B',
  'Apnea': '#C9B715',
  'Arousal': '#6AA136',
  'Large Leak': '#b8b8b8',
}

/**
 * React component or element to render the session detail.
 *
 * @returns The rendered React element.
 */
export default function SessionDetail() {
  const { date } = useParams<{ date: string }>()
  const navigate = useNavigate()
  const sessionDate = date ?? ''

  const [session, setSession] = useState<SessionDetailType | null>(null)
  const [events, setEvents] = useState<EventRecord[]>([])
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)
  const [fullNightFlow, setFullNightFlow] = useState<WaveformSignalResponse | null>(null)
  const [waveformBounds, setWaveformBounds] = useState<[number, number] | null>(null)
  const [waveformWindow, setWaveformWindow] = useState<[number, number] | null>(null)
  const [waveformLoading, setWaveformLoading] = useState(false)
  const [waveformError, setWaveformError] = useState<string | null>(null)
  const [spo2, setSpo2] = useState<SpO2Response | null>(null)
  const [equipment, setEquipment] = useState<InferredEquipment | null>(null)
  const [therapyContext, setTherapyContext] = useState<SessionTherapyContext | null>(null)
  const [settingsHistory, setSettingsHistory] = useState<MachineSettingsSnapshot[]>([])
  const [loading, setLoading] = useState(true)
  const [sessionNavigation, setSessionNavigation] = useState<SessionNavigation | null>(null)
  const [wearableData, setWearableData] = useState<WearableData | null>(null)
  const [timezoneDraft, setTimezoneDraft] = useState('')
  const [timezoneMessage, setTimezoneMessage] = useState<string | null>(null)
  const [timezoneError, setTimezoneError] = useState<string | null>(null)
  const [isTimezoneSubmitting, setIsTimezoneSubmitting] = useState(false)
  const [isTimezoneEditorOpen, setIsTimezoneEditorOpen] = useState(false)
  const [noteDraft, setNoteDraft] = useState('')
  const [noteMessage, setNoteMessage] = useState<string | null>(null)
  const [noteError, setNoteError] = useState<string | null>(null)
  const [isNoteSubmitting, setIsNoteSubmitting] = useState(false)
  const [tagsDraft, setTagsDraft] = useState<string[]>([])
  const [tagsMessage, setTagsMessage] = useState<string | null>(null)
  const [tagsError, setTagsError] = useState<string | null>(null)
  const [isTagsSubmitting, setIsTagsSubmitting] = useState(false)
  const [selectedEventId, setSelectedEventId] = useState<number | null>(null)

  useEffect(() => {
    // These resets intentionally clear the previous session while the new route loads.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    setSpo2(null)
    setFullNightFlow(null)
    setWaveformBounds(null)
    setWaveformWindow(null)
    setWaveformLoading(false)
    setWaveformError(null)
    setEquipment(null)
    setTherapyContext(null)
    setSettingsHistory([])
    setWearableData(null)
    setTimezoneMessage(null)
    setTimezoneError(null)
    setIsTimezoneEditorOpen(false)
    setNoteMessage(null)
    setNoteError(null)
    setTagsMessage(null)
    setTagsError(null)
    setSelectedEventId(null)
    Promise.all([
      api.getSessionByDate(sessionDate),
    ]).then(([s]) => {
      setWaveformLoading(s.data_availability.full_night_flow_available)
      setSession(s)
      setTimezoneDraft(s.machine_tz ?? '')
      setNoteDraft(s.note ?? '')
      setTagsDraft(s.tags ?? [])
      return Promise.all([
        api.getEvents(s.id),
        api.getMetrics(s.id, 15),
      ]).then(([e, m]) => {
        setEvents(e)
        setMetrics(m)
        setLoading(false)
        if (s.has_spo2) {
          api.getSessionSpo2(s.id).then(setSpo2).catch(() => setSpo2(null))
        }
        api.getInferredEquipment(s.folder_date.toString()).then(setEquipment).catch(() => setEquipment(null))
        api.getSessionTherapyContext(s.id).then((context) => {
          setTherapyContext(context)
          return api.getMachineSettings(context.machine.machine_id)
            .then(setSettingsHistory)
            .catch(() => setSettingsHistory([]))
        }).catch(() => setTherapyContext(null))
        api.getWearableData(s.folder_date).then((data) => {
          if (!data.hr.length && !data.spo2.length && !data.stages.length) {
            setWearableData(null)
            return
          }
          setWearableData(data)
        }).catch(() => setWearableData(null))
      })
    }).catch(() => navigate('/dashboard'))
  }, [navigate, sessionDate])

  useEffect(() => {
    if (!session) return
    api.getSessions({ per_page: 600 }).then(all => {
      setSessionNavigation(getSessionNavigation(all, sessionDate))
    })
  }, [session, sessionDate])

  useEffect(() => {
    if (!session?.data_availability.full_night_flow_available) return

    let cancelled = false
    const params = waveformWindow
      ? {
          start_time: new Date(waveformWindow[0]).toISOString(),
          end_time: new Date(waveformWindow[1]).toISOString(),
          max_points: 6000,
        }
      : { max_points: 4000 }

    api.getWaveform(session.id, 'flow_rate', params).then((flow) => {
      if (cancelled) return
      setFullNightFlow(flow)
      if (!waveformWindow) {
        setWaveformBounds([
          new Date(flow.start_time).getTime(),
          new Date(flow.end_time).getTime(),
        ])
      }
    }).catch((error) => {
      if (cancelled) return
      setFullNightFlow(null)
      setWaveformError(error instanceof Error ? error.message : 'Could not load waveform data')
    }).finally(() => {
      if (!cancelled) setWaveformLoading(false)
    })

    return () => {
      cancelled = true
    }
  }, [session, waveformWindow])

  function clampWaveformWindow(center: number, minutes: number): [number, number] {
    const duration = minutes * 60_000
    const fallbackStart = session ? new Date(session.start_datetime).getTime() : center - duration / 2
    const fallbackEnd = session
      ? new Date(session.end_datetime ?? fallbackStart + session.duration_seconds * 1000).getTime()
      : center + duration / 2
    const [boundStart, boundEnd] = waveformBounds ?? [fallbackStart, fallbackEnd]
    if (boundEnd - boundStart <= duration) return [boundStart, boundEnd]
    const start = Math.min(Math.max(center - duration / 2, boundStart), boundEnd - duration)
    return [start, start + duration]
  }

  function inspectEvent(event: EventRecord) {
    setSelectedEventId(event.id)
    setWaveformLoading(Boolean(session?.data_availability.full_night_flow_available))
    setWaveformError(null)
    setWaveformWindow(clampWaveformWindow(new Date(event.event_datetime).getTime(), 5))
  }

  const selectedEventIndex = selectedEventId == null
    ? -1
    : events.findIndex((event) => event.id === selectedEventId)
  const selectedEvent = selectedEventIndex >= 0 ? events[selectedEventIndex] : null

  function clearSelectedEvent() {
    setSelectedEventId(null)
    setWaveformLoading(Boolean(session?.data_availability.full_night_flow_available))
    setWaveformWindow(null)
  }

  function selectWaveformWindow(minutes: number | null) {
    if (minutes == null && waveformWindow == null) return
    setWaveformLoading(true)
    setWaveformError(null)
    if (minutes == null) {
      setWaveformWindow(null)
      return
    }
    const selectedCenter = selectedEvent
      ? new Date(selectedEvent.event_datetime).getTime()
      : waveformWindow
        ? waveformWindow[0] + (waveformWindow[1] - waveformWindow[0]) / 2
        : waveformBounds
          ? waveformBounds[0] + (waveformBounds[1] - waveformBounds[0]) / 2
          : new Date(session?.start_datetime ?? 0).getTime()
    setWaveformWindow(clampWaveformWindow(selectedCenter, minutes))
  }

  function panWaveformWindow(direction: -1 | 1) {
    if (!waveformWindow) return
    setWaveformLoading(true)
    setWaveformError(null)
    const duration = waveformWindow[1] - waveformWindow[0]
    const center = waveformWindow[0] + duration / 2 + direction * duration * 0.8
    setWaveformWindow(clampWaveformWindow(center, duration / 60_000))
  }

  function setSharedWaveformWindow(window: [number, number]) {
    setWaveformLoading(Boolean(session?.data_availability.full_night_flow_available))
    setWaveformError(null)
    setWaveformWindow(window)
  }

  async function handleTimezoneSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!session) return
    setTimezoneError(null)
    setTimezoneMessage(null)
    setIsTimezoneSubmitting(true)
    try {
      const updated = await api.updateSessionTimezone(session.id, timezoneDraft)
      const [nextEvents, nextMetrics] = await Promise.all([
        api.getEvents(updated.id),
        api.getMetrics(updated.id, 15),
      ])
      setWaveformLoading(updated.data_availability.full_night_flow_available)
      setSession(updated)
      setTimezoneDraft(updated.machine_tz ?? '')
      setEvents(nextEvents)
      setMetrics(nextMetrics)
      setSelectedEventId(null)
      if (updated.has_spo2) {
        api.getSessionSpo2(updated.id).then(setSpo2).catch(() => setSpo2(null))
      } else {
        setSpo2(null)
      }
      setTimezoneMessage('Session timezone updated.')
      setIsTimezoneEditorOpen(false)
    } catch (err) {
      setTimezoneError(err instanceof Error ? err.message : 'Could not update session timezone')
    } finally {
      setIsTimezoneSubmitting(false)
    }
  }

  async function handleNoteSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!session) return
    setNoteError(null)
    setNoteMessage(null)
    setIsNoteSubmitting(true)
    try {
      const updated = await api.updateSessionNote(session.id, noteDraft)
      setSession(updated)
      setNoteDraft(updated.note ?? '')
      setNoteMessage(updated.note ? 'Note saved.' : 'Note cleared.')
    } catch (err) {
      setNoteError(err instanceof Error ? err.message : 'Could not save note')
    } finally {
      setIsNoteSubmitting(false)
    }
  }

  async function handleTagsSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!session) return
    setTagsError(null)
    setTagsMessage(null)
    setIsTagsSubmitting(true)
    try {
      const updated = await api.updateSessionTags(session.id, tagsDraft)
      setSession(updated)
      setTagsDraft(updated.tags ?? [])
      setTagsMessage(updated.tags.length ? 'Tags saved.' : 'Tags cleared.')
    } catch (err) {
      setTagsError(err instanceof Error ? err.message : 'Could not save tags')
    } finally {
      setIsTagsSubmitting(false)
    }
  }

  function toggleTag(tag: string) {
    setTagsDraft((current) => (
      current.includes(tag)
        ? current.filter((item) => item !== tag)
        : [...current, tag]
    ))
    setTagsError(null)
    setTagsMessage(null)
  }

  if (loading) return <div className="rounded-[28px] border border-[var(--border)] bg-[var(--surface-strong)] p-10 text-center text-[var(--muted-foreground)]">Loading session...</div>
  if (!session || !metrics) return null

  const hours = Math.floor(session.duration_seconds / 3600)
  const mins  = Math.floor((session.duration_seconds % 3600) / 60)
  const endTime = session.end_datetime ?? new Date(new Date(session.start_datetime).getTime() + session.duration_seconds * 1000).toISOString()
  const metricsTimeDomain = computeMetricsDomain(metricsToPoints(metrics))
  const sessionTimeDomain: [number, number] = [
    new Date(session.start_datetime).getTime(),
    new Date(endTime).getTime(),
  ]
  const reviewTimeDomain = waveformWindow ?? waveformBounds ?? metricsTimeDomain ?? sessionTimeDomain
  const wholeNightDomain = waveformBounds ?? metricsTimeDomain ?? sessionTimeDomain
  const visibleMetricPoints = metricsToPoints(metrics).filter(
    (point) => point.ts >= reviewTimeDomain[0] && point.ts <= reviewTimeDomain[1],
  )
  const finiteFlow = (fullNightFlow?.values ?? []).filter((value): value is number => value != null)
  const finitePressure = visibleMetricPoints
    .map((point) => point.pressure)
    .filter((value): value is number => value != null)
  const finiteLeak = visibleMetricPoints
    .map((point) => point.leak)
    .filter((value): value is number => value != null)
  const selectedEventStats = selectedEvent ? {
    flowMin: finiteFlow.length ? Math.min(...finiteFlow) : null,
    flowMax: finiteFlow.length ? Math.max(...finiteFlow) : null,
    averagePressure: finitePressure.length
      ? finitePressure.reduce((sum, value) => sum + value, 0) / finitePressure.length
      : null,
    maxLeak: finiteLeak.length ? Math.max(...finiteLeak) : null,
  } : null
  const badge = ahiBadge(session.ahi)
  const tagsChanged = !sameTags(tagsDraft, session.tags ?? [])
  const hasDeviceSettings = Boolean(session.therapy_mode || session.mask_type || session.humidity_level != null || session.temperature_c != null)
  const normalizedSettings = therapyContext?.settings?.normalized_settings
  const hasEquipment = Boolean(equipment && (equipment.cushion || equipment.headgear || equipment.tubing || equipment.humidifier_chamber || equipment.filter))
  const availability = session.data_availability
  const isParserBacked = availability.import_backend === 'cpap-parser'

  const secondaryStatContentClass = 'px-4 pb-4 pt-4 sm:px-5 sm:pb-5 sm:pt-5'

  return (
    <div className="space-y-6">
      {/* Nav row */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Link to="/dashboard" className="inline-flex items-center gap-1 text-sm font-bold text-[var(--accent)] transition hover:text-[var(--accent-hover)]">
          <ChevronLeftIcon className="h-4 w-4" />
          <span>All nights</span>
        </Link>
        <div className="flex gap-2">
          {sessionNavigation?.previousUrl && (
            <Link to={sessionNavigation.previousUrl}>
              <Button variant="outline" size="sm">
                <ChevronLeftIcon className="h-4 w-4" />
                <span>Previous night</span>
              </Button>
            </Link>
          )}
          {sessionNavigation?.nextUrl && (
            <Link to={sessionNavigation.nextUrl}>
              <Button variant="outline" size="sm">
                <span>Next night</span>
                <ChevronRightIcon className="h-4 w-4" />
              </Button>
            </Link>
          )}
        </div>
      </div>

      <section
        data-testid="night-header"
        className="flex flex-col gap-3 border-b border-[var(--border)] pb-5 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between"
      >
        <div>
          <h1 className="text-2xl font-extrabold text-[var(--foreground)] sm:text-3xl">
            {fmtDate(session.folder_date + 'T00:00:00')}
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-[var(--muted-foreground)]">
            <span>{fmtTime(session.start_datetime)} – {fmtTime(endTime)}</span>
            {session.machine_tz ? (
              <span title="CPAP machine timezone used for import">· {session.machine_tz}</span>
            ) : (
              <span title="CPAP machine timezone was not recorded for this import">· timezone not recorded</span>
            )}
            <button
              type="button"
              className="ml-1 rounded-full border border-[var(--border)] px-2 py-0.5 text-xs font-bold text-[var(--accent)] transition hover:border-[var(--accent-border)] hover:text-[var(--accent-hover)]"
              onClick={() => {
                setTimezoneDraft(session.machine_tz ?? '')
                setTimezoneError(null)
                setTimezoneMessage(null)
                setIsTimezoneEditorOpen((current) => !current)
              }}
            >
              {isTimezoneEditorOpen ? 'Cancel edit' : 'Edit'}
            </button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 sm:justify-end">
          <span className={`shrink-0 rounded-full px-3 py-1 text-xs font-bold ${badge.className}`}>
            {badge.label}
          </span>
          {isParserBacked ? (
            <span className="shrink-0 rounded-full bg-[rgba(82,81,167,0.10)] px-3 py-1 text-xs font-bold text-[var(--accent)]">
              ResMed cpap-parser import
            </span>
          ) : null}
        </div>
        {isTimezoneEditorOpen ? (
          <form className="rounded-[14px] border border-[var(--border)] bg-[var(--surface-soft)] p-3 sm:flex sm:basis-full sm:items-end sm:gap-3" onSubmit={handleTimezoneSubmit}>
            <div className="space-y-2 sm:min-w-72">
              <Label htmlFor="sessionMachineTz">Correct machine timezone</Label>
              <Input
                id="sessionMachineTz"
                value={timezoneDraft}
                onChange={(event) => setTimezoneDraft(event.target.value)}
                autoComplete="off"
                placeholder="America/New_York"
              />
            </div>
            <Button className="mt-3 sm:mt-0" type="submit" disabled={isTimezoneSubmitting || !timezoneDraft}>
              {isTimezoneSubmitting ? 'Updating...' : 'Update timezone'}
            </Button>
          </form>
        ) : null}
        {timezoneMessage ? <p className="text-sm font-medium text-[var(--olive-deep)] sm:basis-full">{timezoneMessage}</p> : null}
        {timezoneError ? <p className="text-sm text-[var(--danger-text)] sm:basis-full">{timezoneError}</p> : null}
      </section>

      <section
        data-testid="night-summary"
        aria-label="Night summary"
        className="space-y-3"
      >
        <TherapyScoreCard session={session} />
        <div data-testid="core-metrics" className="grid grid-cols-2 gap-3 md:grid-cols-6 xl:grid-cols-5">
          <NightMetric className="md:col-span-2 xl:col-span-1" label="AHI" value={session.ahi?.toFixed(1) ?? '—'} note="events/hr" tone="accent" />
          <NightMetric
            className="md:col-span-2 xl:col-span-1"
            label="Usage"
            value={`${hours}h ${mins}m`}
            note="therapy usage"
            detail={session.wall_clock_seconds != null && session.gap_seconds != null
              ? `${(session.wall_clock_seconds / 3600).toFixed(1)}h span · ${(session.gap_seconds / 60).toFixed(0)}m gaps`
              : undefined}
            tone="good"
          />
          <NightMetric
            className="md:col-span-2 xl:col-span-1"
            label="Pressure"
            value={session.avg_pressure?.toFixed(1) ?? '—'}
            note={`avg cmH₂O · P95 ${session.p95_pressure?.toFixed(1) ?? '—'}`}
          />
          <NightMetric
            className="md:col-span-3 xl:col-span-1"
            label="Leak"
            value={leakToLpm(session.avg_leak, session.leak_unit)?.toFixed(1) ?? '—'}
            note={`avg L/min · P95 ${leakToLpm(session.p95_leak, session.leak_unit)?.toFixed(1) ?? '—'} L/min`}
          />
          <NightMetric
            className="col-span-2 md:col-span-3 xl:col-span-1"
            label="Events"
            value={String(session.total_ahi_events)}
            note={`CA ${session.central_apnea_count} · OA ${session.obstructive_apnea_count} · H ${session.hypopnea_count}`}
          />
        </div>
      </section>

      <section data-testid="ai-insights">
        <SessionAICard sessionId={session.id} />
      </section>

      <section data-testid="graph-review" aria-labelledby="daily-review-heading" className="space-y-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--accent)]">Daily review</p>
            <h2 id="daily-review-heading" className="mt-1 text-2xl font-extrabold text-[var(--foreground)]">
              Daily Review Graphs
            </h2>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">
              Synchronized event flags and therapy signals for serious nightly review.
            </p>
          </div>
        </div>
        <div className="grid items-start gap-4 lg:grid-cols-[340px_minmax(0,1fr)] xl:grid-cols-[380px_minmax(0,1fr)]">
          <Card className="lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)]">
            <CardHeader className="pb-3">
              <CardTitle>Events</CardTitle>
              <CardDescription>Select an event to center the synchronized graph stack around it.</CardDescription>
            </CardHeader>
            <CardContent>
              <EventTable events={events} selectedEventId={selectedEventId} onSelectEvent={inspectEvent} />
            </CardContent>
          </Card>
          <div className="min-w-0 space-y-4">
            <Card className="min-w-0 overflow-hidden">
              <CardHeader className="pb-3">
                <CardTitle>Whole-night navigator</CardTitle>
                <CardDescription>Events stay visible across the whole night while the outlined box controls every graph track.</CardDescription>
              </CardHeader>
              <CardContent>
                <EventTimeline
                  events={events}
                  durationSeconds={session.duration_seconds}
                  startDatetime={session.start_datetime}
                  wholeNightDomain={wholeNightDomain}
                  selectedTimeDomain={waveformWindow}
                  selectedEventId={selectedEventId}
                  onSelectEvent={inspectEvent}
                  onWindowChange={setSharedWaveformWindow}
                />
              </CardContent>
            </Card>
            {selectedEvent && selectedEventStats ? (
              <SelectedEventReadout
                event={selectedEvent}
                flowMin={selectedEventStats.flowMin}
                flowMax={selectedEventStats.flowMax}
                averagePressure={selectedEventStats.averagePressure}
                maxLeak={selectedEventStats.maxLeak}
                returnedSamples={fullNightFlow?.returned_sample_count ?? 0}
                sourceSamples={fullNightFlow?.sample_count ?? 0}
                onClear={clearSelectedEvent}
              />
            ) : null}
            {fullNightFlow ? (
              <FullNightFlowChart
                waveform={fullNightFlow}
                events={events}
                timeDomain={reviewTimeDomain}
                wholeNight={waveformWindow == null}
                loading={waveformLoading}
                onSelectWindow={selectWaveformWindow}
                onPan={panWaveformWindow}
                onSelectRange={setSharedWaveformWindow}
              />
            ) : waveformLoading ? (
              <UnavailableDataCard
                title="Loading full-night flow"
                description="Reading overlapping compressed waveform chunks and reducing them to a browser-safe point count."
              />
            ) : (
              <UnavailableDataCard
                title="Full-night flow unavailable"
                description={waveformError
                  ? `The stored flow chunks could not be read: ${waveformError}`
                  : isParserBacked && availability.events_available
                    ? 'Re-import this SD card to populate waveform data. This night has parser summaries/events but predates full-night waveform chunk storage.'
                    : 'No full-night flow chunks were stored for this night. SleepLab does not synthesize missing waveform data.'}
              />
            )}
            {metrics.timestamps.length > 0 ? (
              <MetricsChart metrics={metrics} events={events} leakKind={session.leak_kind} timeDomain={reviewTimeDomain} />
            ) : (
              <UnavailableDataCard
                title="Detailed graph tracks unavailable"
                description="Pressure, leak, flow limitation, respiratory rate, minute ventilation, and snore samples were not imported for this night."
              />
            )}
          </div>
        </div>
      </section>

      <Card data-testid="notes-tags">
        <CardHeader className="p-4 pb-2 sm:p-5 sm:pb-2">
          <CardTitle>Notes &amp; tags</CardTitle>
          <CardDescription>Keep personal context and quick night labels together.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6 px-4 pb-4 pt-2 sm:px-5 sm:pb-5 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <section>
            <h3 className="mb-2 text-sm font-bold text-[var(--foreground)]">Notes</h3>
            <form className="space-y-2.5" onSubmit={handleNoteSubmit}>
              <Label htmlFor="sessionNote">Session note</Label>
              <textarea
                id="sessionNote"
                value={noteDraft}
                onChange={(event) => {
                  setNoteDraft(event.target.value)
                  setNoteError(null)
                  setNoteMessage(null)
                }}
                className="min-h-20 w-full resize-y rounded-[14px] border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-2 text-sm text-[var(--foreground)] outline-none transition placeholder:text-[var(--muted-foreground)] focus:border-[var(--accent-border)] focus:ring-2 focus:ring-[rgba(82,81,167,0.16)]"
                placeholder="Tried mouth tape, had a late drink, felt congested..."
              />
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  {noteMessage ? <p className="text-sm font-medium text-[var(--olive-deep)]">{noteMessage}</p> : null}
                  {noteError ? <p className="text-sm text-[var(--danger-text)]">{noteError}</p> : null}
                </div>
                <Button type="submit" disabled={isNoteSubmitting}>
                  {isNoteSubmitting ? 'Saving...' : 'Save note'}
                </Button>
              </div>
            </form>
          </section>

          <section className="border-t border-[var(--border)] pt-5 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
            <h3 className="mb-3 text-sm font-bold text-[var(--foreground)]">Tags</h3>
            <form className="space-y-3" onSubmit={handleTagsSubmit}>
              {tagsDraft.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {tagsDraft.map((tag) => (
                    <span key={tag} className="rounded-full bg-[rgba(82,81,167,0.10)] px-3 py-1 text-xs font-bold text-[var(--accent)]">
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {SESSION_TAGS.map((tag) => {
                  const selected = tagsDraft.includes(tag)
                  return (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => toggleTag(tag)}
                      className={`rounded-full border px-3 py-1.5 text-xs font-bold transition ${
                        selected
                          ? 'border-[var(--accent-border)] bg-[rgba(82,81,167,0.12)] text-[var(--accent)]'
                          : 'border-[var(--border)] bg-[var(--surface-soft)] text-[var(--foreground)] shadow-sm hover:border-[var(--accent-border)] hover:text-[var(--accent)]'
                      }`}
                      aria-pressed={selected}
                    >
                      {tag}
                    </button>
                  )
                })}
              </div>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  {tagsMessage ? <p className="text-sm font-medium text-[var(--olive-deep)]">{tagsMessage}</p> : null}
                  {tagsError ? <p className="text-sm text-[var(--danger-text)]">{tagsError}</p> : null}
                </div>
                <Button type="submit" disabled={isTagsSubmitting || !tagsChanged}>
                  {isTagsSubmitting ? 'Saving...' : 'Save tags'}
                </Button>
              </div>
            </form>
          </section>
        </CardContent>
      </Card>

      <section data-testid="supporting-context" aria-labelledby="supporting-context-heading" className="space-y-4">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--accent)]">Night context</p>
          <h2 id="supporting-context-heading" className="mt-1 text-xl font-extrabold text-[var(--foreground)]">
            Therapy &amp; data details
          </h2>
        </div>
        <div className="grid items-stretch gap-4 lg:grid-cols-2">
          <div className="order-2">
            <NightDataCoverage session={session} />
          </div>
          {therapyContext && (
            <Card data-testid="therapy-machine" className="order-1">
              <CardContent className={secondaryStatContentClass}>
                <p className="mb-1 text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">Therapy and machine</p>
                <p className="mb-3 text-xs text-[var(--muted-foreground)]">
                  {[therapyContext.machine.manufacturer, therapyContext.machine.family, therapyContext.machine.model].filter(Boolean).join(' ') || 'Machine details unavailable'}
                  {` · ${therapyContext.machine.validation_status}`}
                </p>
                <div className="flex flex-wrap gap-x-6 gap-y-1.5 text-sm">
                  {(normalizedSettings?.therapy_mode || session.therapy_mode) && (
                    <span><span className="text-[var(--muted-foreground)]">Mode </span>{String(normalizedSettings?.therapy_mode || session.therapy_mode).toUpperCase()}</span>
                  )}
                  {normalizedSettings?.minimum_pressure_cmh2o != null && normalizedSettings?.maximum_pressure_cmh2o != null && (
                    <span><span className="text-[var(--muted-foreground)]">Pressure </span>{String(normalizedSettings.minimum_pressure_cmh2o)}-{String(normalizedSettings.maximum_pressure_cmh2o)} cmH₂O</span>
                  )}
                  {normalizedSettings?.fixed_pressure_cmh2o != null && (
                    <span><span className="text-[var(--muted-foreground)]">Pressure </span>{String(normalizedSettings.fixed_pressure_cmh2o)} cmH₂O</span>
                  )}
                  {(normalizedSettings?.epr_mode || normalizedSettings?.epr_level_cmh2o != null) && (
                    <span><span className="text-[var(--muted-foreground)]">EPR </span>{String(normalizedSettings?.epr_mode ?? 'unknown')} {normalizedSettings?.epr_level_cmh2o != null ? String(normalizedSettings.epr_level_cmh2o) : ''}</span>
                  )}
                  {normalizedSettings?.ramp_mode && (
                    <span><span className="text-[var(--muted-foreground)]">Ramp </span>{String(normalizedSettings.ramp_mode)}{normalizedSettings.ramp_start_pressure_cmh2o != null ? ` from ${String(normalizedSettings.ramp_start_pressure_cmh2o)} cmH₂O` : ''}</span>
                  )}
                  {(normalizedSettings?.mask_type || session.mask_type) && (
                    <span><span className="text-[var(--muted-foreground)]">Mask </span>{String(normalizedSettings?.mask_type || session.mask_type)}</span>
                  )}
                  {(normalizedSettings?.humidifier_level != null || session.humidity_level != null) && (
                    <span><span className="text-[var(--muted-foreground)]">Humidity </span>{String(normalizedSettings?.humidifier_level ?? session.humidity_level)}</span>
                  )}
                  {(normalizedSettings?.tube_temperature_c != null || session.temperature_c != null) && (
                    <span><span className="text-[var(--muted-foreground)]">Tube </span>{String(normalizedSettings?.heated_tube_mode ?? 'temperature')} · {String(normalizedSettings?.tube_temperature_c ?? session.temperature_c)}°C</span>
                  )}
                </div>
                {therapyContext.blocks.length ? (
                  <div className="mt-4 border-t border-[var(--border)] pt-3">
                    <p className="text-xs font-bold text-[var(--muted-foreground)]">
                      {therapyContext.blocks.length} therapy block{therapyContext.blocks.length === 1 ? '' : 's'} · {session.duration_validation_status}
                    </p>
                    <div className="mt-2 space-y-1 text-xs">
                      {therapyContext.blocks.map((block) => (
                        <p key={block.id}>
                          {fmtTime(block.start_datetime)}-{fmtTime(block.end_datetime)}
                          <span className="text-[var(--muted-foreground)]"> · {Math.round(block.duration_seconds / 60)}m · {block.source_kind.replaceAll('_', ' ')}</span>
                        </p>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p className="mt-3 text-xs text-[var(--muted-foreground)]">Explicit therapy blocks are not available for this night.</p>
                )}
                {settingsHistory.length > 1 ? (
                  <div className="mt-4 border-t border-[var(--border)] pt-3">
                    <p className="text-xs font-bold text-[var(--muted-foreground)]">Recent settings history</p>
                    <div className="mt-2 space-y-1 text-xs">
                      {settingsHistory.slice(0, 4).map((snapshot, index) => {
                        const settings = snapshot.normalized_settings
                        const previous = settingsHistory[index + 1]
                        const changed = previous
                          ? JSON.stringify(settings) !== JSON.stringify(previous.normalized_settings)
                          : false
                        return (
                          <p key={snapshot.id}>
                            {new Date(snapshot.effective_at).toLocaleDateString()}
                            <span className="text-[var(--muted-foreground)]">
                              {` · ${String(settings.therapy_mode ?? 'mode unknown')}`}
                              {settings.minimum_pressure_cmh2o != null && settings.maximum_pressure_cmh2o != null
                                ? ` · ${String(settings.minimum_pressure_cmh2o)}-${String(settings.maximum_pressure_cmh2o)} cmH₂O`
                                : ''}
                              {` · ${snapshot.validation_status}${changed ? ' · changed' : ''}`}
                            </span>
                          </p>
                        )
                      })}
                    </div>
                  </div>
                ) : null}
                {therapyContext.settings?.diagnostics?.length ? (
                  <p className="mt-3 text-xs text-[var(--muted-foreground)]">
                    Some vendor settings were preserved without normalization. See import diagnostics for details.
                  </p>
                ) : null}
              </CardContent>
            </Card>
          )}
          {!therapyContext && (
            <Card data-testid="therapy-machine" className="order-1">
              <CardContent className={secondaryStatContentClass}>
                <p className="mb-1 text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">Therapy and machine</p>
                {hasDeviceSettings ? (
                  <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1.5 text-sm">
                    {session.therapy_mode && (
                      <span><span className="text-[var(--muted-foreground)]">Mode </span>{session.therapy_mode.toUpperCase()}</span>
                    )}
                    {session.mask_type && (
                      <span><span className="text-[var(--muted-foreground)]">Mask </span>{session.mask_type}</span>
                    )}
                    {session.humidity_level != null && (
                      <span><span className="text-[var(--muted-foreground)]">Humidity </span>{session.humidity_level}</span>
                    )}
                    {session.temperature_c != null && (
                      <span><span className="text-[var(--muted-foreground)]">Temp </span>{session.temperature_c}°C</span>
                    )}
                  </div>
                ) : (
                  <p className="mt-2 text-sm leading-6 text-[var(--muted-foreground)]">
                    Detailed therapy settings were not imported for this night.
                  </p>
                )}
              </CardContent>
            </Card>
          )}

          {hasEquipment && equipment && (
            <Card className="order-3 lg:col-span-2">
              <CardContent className="px-4 pb-4 pt-4 sm:px-5 sm:pb-5 sm:pt-5">
                <p className="mb-2 text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">Equipment this night</p>
                <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-1">
                  {([
                    { key: 'cushion', label: 'Cushion' },
                    { key: 'headgear', label: 'Headgear' },
                    { key: 'tubing', label: 'Tubing' },
                    { key: 'humidifier_chamber', label: 'Humidifier' },
                    { key: 'filter', label: 'Filter' },
                  ] as { key: keyof InferredEquipment; label: string }[]).map(({ key, label }) => {
                    const item = equipment[key]
                    if (!item) return null
                    const name = [item.brand, item.model].filter(Boolean).join(' ') || label
                    const category = item.mask_category ? ` · ${item.mask_category}` : ''
                    const age = item.days_in_use != null ? `${item.days_in_use}d` : null
                    const overdue = item.replacement_days != null && item.days_in_use != null
                      && item.days_in_use > item.replacement_days
                    return (
                      <div key={key} className="rounded-[12px] bg-[var(--surface-soft)] px-3 py-2">
                        <p className="text-xs text-[var(--muted-foreground)]">{label}</p>
                        <p className="text-sm font-medium">{name}{category}</p>
                        {age && (
                          <p className={`mt-0.5 text-xs ${overdue ? 'text-[var(--danger-text)]' : 'text-[var(--muted-foreground)]'}`}>
                            {age} old{overdue ? ' · overdue for replacement' : ''}
                          </p>
                        )}
                      </div>
                    )
                  })}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </section>

      {wearableData && wearableData.stages.length > 0 && (
        <WearableSleepStageChart stages={wearableData.stages} />
      )}

      <section data-testid="oximetry-card">
        {spo2 ? (
          <SpO2Chart spo2={spo2} wearable={wearableData} />
        ) : (
          <UnavailableDataCard
            title="Oximetry unavailable"
            centered
            description={isParserBacked
              ? 'This ResMed cpap-parser workflow does not yet claim SpO2 or pulse support. SleepLab leaves those values unavailable instead of estimating them.'
              : 'No SpO2 or pulse samples were imported for this night.'}
          />
        )}
      </section>

    </div>
  )
}

function NightDataCoverage({ session }: { session: SessionDetailType }) {
  const availability = session.data_availability
  const sourceLabel = availability.import_backend === 'cpap-parser'
    ? 'ResMed cpap-parser'
    : availability.import_backend === 'legacy'
      ? 'Legacy importer'
      : 'Unknown importer'
  const items = [
    {
      label: 'Events',
      value: availability.events_available
        ? `${availability.event_count.toLocaleString()} imported`
        : 'None imported',
      available: availability.events_available,
    },
    {
      label: 'Night graphs',
      value: availability.therapy_graphs_available
        ? `${availability.metric_sample_count.toLocaleString()} sample${availability.metric_sample_count === 1 ? '' : 's'}`
        : 'No detailed signals',
      available: availability.therapy_graphs_available,
    },
    {
      label: 'Event waveforms',
      value: availability.event_waveforms_available
        ? `${availability.waveform_sample_count.toLocaleString()} sample${availability.waveform_sample_count === 1 ? '' : 's'}`
        : 'Not available',
      available: availability.event_waveforms_available,
    },
    {
      label: 'Therapy settings',
      value: availability.settings_available ? 'Available' : 'Not imported',
      available: availability.settings_available,
    },
    {
      label: 'SpO2 / pulse',
      value: availability.spo2_available ? 'Available' : 'Not supported',
      available: availability.spo2_available,
    },
    {
      label: 'Full-night flow',
      value: availability.full_night_flow_available ? 'Stored in chunks' : 'Not stored',
      available: availability.full_night_flow_available,
    },
  ]

  return (
    <Card data-testid="nightly-data-coverage" className="h-full">
      <CardHeader>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <CardTitle>Nightly data coverage</CardTitle>
            <CardDescription>Persisted data available for this machine-local night.</CardDescription>
          </div>
          <span className="w-fit rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1 text-xs font-bold text-[var(--foreground)]">
            {sourceLabel}
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <div key={item.label} className="flex items-center justify-between gap-3 rounded-[14px] border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-3">
              <span className="text-sm font-semibold text-[var(--foreground)]">{item.label}</span>
              <span className={`text-right text-xs font-bold ${item.available ? 'text-[var(--green-700)]' : 'text-[var(--muted-foreground)]'}`}>
                {item.value}
              </span>
            </div>
          ))}
        </div>
        {availability.import_backend === 'cpap-parser' ? (
          <p className="mt-3 text-xs leading-5 text-[var(--muted-foreground)]">
            Summary values, scored events, and detailed signals come from the validated parser-backed import path. Unsupported channels are called out explicitly.
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}

function UnavailableDataCard({
  title,
  description,
  centered = false,
}: {
  title: string
  description: string
  centered?: boolean
}) {
  return (
    <Card>
      {/* pt-5/sm:pt-6 re-assert symmetric top padding: cn() has no tailwind-merge, so CardContent's built-in pt-0 would otherwise win the cascade and jam the title against the top edge. */}
      <CardContent className={`p-5 pt-5 sm:p-6 sm:pt-6 ${centered ? 'flex min-h-24 flex-col justify-center' : ''}`}>
        <p className="text-base font-bold text-[var(--foreground)]">{title}</p>
        <p className="mt-1.5 max-w-3xl text-sm leading-6 text-[var(--muted-foreground)]">{description}</p>
      </CardContent>
    </Card>
  )
}

function NightMetric({
  className = '',
  label,
  value,
  note,
  detail,
  tone = 'default',
}: {
  className?: string
  label: string
  value: string
  note: string
  detail?: string
  tone?: 'default' | 'accent' | 'good'
}) {
  const valueClass = tone === 'accent'
    ? 'text-[var(--accent)]'
    : tone === 'good'
      ? 'text-[var(--green-700)]'
      : 'text-[var(--foreground)]'

  return (
    <Card className={`h-full min-w-0 ${className}`}>
      <CardContent className="h-full min-h-32 px-5 pb-5 pt-5 sm:px-6 sm:pb-6 sm:pt-6">
        <div className="w-full">
          <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">{label}</p>
          <p className={`mt-2 text-2xl font-semibold leading-none sm:text-3xl ${valueClass}`}>{value}</p>
          <p className="mt-2 text-xs leading-4 text-[var(--muted-foreground)]">{note}</p>
          {detail ? <p className="mt-2 text-[11px] leading-4 text-[var(--muted-foreground)]">{detail}</p> : null}
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * React component or element to render the event table.
 *
 * @returns The rendered React element.
 */
function EventTable({
  events,
  selectedEventId,
  onSelectEvent,
}: {
  events: EventRecord[]
  selectedEventId: number | null
  onSelectEvent: (event: EventRecord) => void
}) {
  if (!events.length) return null

  return (
    <>
      <div className="-mx-1 mt-4 flex gap-2 overflow-x-auto px-1 pb-2 md:hidden" aria-label="Event picker">
        {events.map((event) => {
          const selected = event.id === selectedEventId
          return (
            <button
              key={event.id}
              type="button"
              className={`min-h-11 shrink-0 rounded-full border px-3 py-2 text-xs font-bold ${
                selected
                  ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent)]'
                  : 'border-[var(--border)] bg-[var(--surface-soft)] text-[var(--foreground)]'
              }`}
              onClick={() => onSelectEvent(event)}
            >
              {EVENT_CODES[event.event_type] ?? event.event_type} {fmtTime(event.event_datetime)}
            </button>
          )
        })}
      </div>
      <div data-testid="desktop-event-selector" className="mt-4 hidden min-h-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-[var(--border)] md:flex">
        <div className="max-h-[65vh] overflow-y-auto overflow-x-hidden">
        <table className="w-full table-fixed border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-[var(--surface-strong)] text-[var(--muted-foreground)]">
            <tr>
              <th className="w-[18%] px-3 py-2 font-bold uppercase tracking-[0.12em]">Code</th>
              <th className="w-[25%] px-3 py-2 font-bold uppercase tracking-[0.12em]">Time</th>
              <th className="w-[20%] px-3 py-2 font-bold uppercase tracking-[0.12em]">Duration</th>
              <th className="w-[37%] px-3 py-2 font-bold uppercase tracking-[0.12em]">Type</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => {
              const selected = event.id === selectedEventId
              const color = EVENT_COLORS[event.event_type] ?? '#8E3D40'
              return (
                <tr
                  key={event.id}
                  title={`${EVENT_CODES[event.event_type] ?? event.event_type} at ${fmtTime(event.event_datetime)}`}
                  className={`cursor-pointer border-t border-[var(--border)] transition ${
                    selected ? 'bg-[var(--accent-soft)]' : 'hover:bg-[var(--surface-soft)]'
                  }`}
                  onClick={() => onSelectEvent(event)}
                >
                  <td className="px-3 py-2">
                    <span
                      className="inline-flex min-w-8 items-center justify-center rounded-full px-2 py-0.5 text-[11px] font-bold text-white"
                      style={{ background: color }}
                    >
                      {EVENT_CODES[event.event_type] ?? event.event_type}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-medium text-[var(--foreground)]">
                    {fmtTime(event.event_datetime)}
                  </td>
                  <td className="break-words px-3 py-2 text-[var(--muted-foreground)]">
                    {event.duration_seconds ? `${event.duration_seconds}s` : '-'}
                  </td>
                  <td className="px-3 py-2 text-[var(--muted-foreground)]">
                    {event.event_type}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      </div>
    </>
  )
}

function SelectedEventReadout({
  event,
  flowMin,
  flowMax,
  averagePressure,
  maxLeak,
  returnedSamples,
  sourceSamples,
  onClear,
}: {
  event: EventRecord
  flowMin: number | null
  flowMax: number | null
  averagePressure: number | null
  maxLeak: number | null
  returnedSamples: number
  sourceSamples: number
  onClear: () => void
}) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--accent)]">Selected event</p>
          <p className="mt-1 font-bold text-[var(--foreground)]">
            {event.event_type} at {fmtTime(event.event_datetime)}
            {event.duration_seconds ? ` · ${event.duration_seconds}s` : ''}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-x-5 gap-y-2 text-xs sm:flex sm:flex-wrap sm:items-center">
          <span aria-label="Selected event flow range"><strong>Flow</strong> {flowMin?.toFixed(2) ?? '—'} to {flowMax?.toFixed(2) ?? '—'} L/s</span>
          <span aria-label="Selected event average pressure"><strong>Avg pressure</strong> {averagePressure?.toFixed(1) ?? '—'} cmH₂O</span>
          <span aria-label="Selected event maximum leak"><strong>Max leak</strong> {maxLeak?.toFixed(1) ?? '—'} L/min</span>
          <span aria-label="Selected event sample quality"><strong>Samples</strong> {returnedSamples.toLocaleString()} / {sourceSamples.toLocaleString()}</span>
        </div>
        <Button variant="outline" size="sm" className="min-h-11 sm:min-h-0" onClick={onClear}>
          Clear selection
        </Button>
      </CardContent>
    </Card>
  )
}

function TherapyScoreCard({ session }: { session: SessionDetailType }) {
  const score = session.therapy_score
  const delta = session.score_vs_30d_avg
  const leakLpm = leakToLpm(session.avg_leak, session.leak_unit)
  const deltaLabel = delta == null
    ? null
    : `${delta > 0 ? '+' : ''}${delta.toFixed(1)} vs 30d avg`

  return (
    <Card
      data-testid="therapy-score-card"
      className="relative z-20 h-full min-w-0 overflow-visible border-[rgba(82,81,167,0.28)] bg-[linear-gradient(145deg,rgba(67,56,202,0.96),rgba(109,63,240,0.92))] text-white"
    >
      <CardContent className="grid gap-4 px-4 pb-4 pt-4 md:grid-cols-[minmax(220px,0.75fr)_minmax(0,2fr)] sm:px-5 sm:pb-5 sm:pt-5">
        <div className="flex flex-col">
          <div className="flex items-center gap-1.5">
            <p className="whitespace-nowrap text-xs font-bold uppercase tracking-[0.14em] text-white/70">Therapy score</p>
            <TherapyScoreHelp />
          </div>

          <div className="mt-5 flex items-center gap-3">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[14px] bg-white/14 text-2xl font-extrabold leading-none text-white shadow-sm">
              {score.total}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-lg font-bold leading-6 text-white">Grade {score.grade}</p>
              {deltaLabel ? <p className="text-xs font-medium text-white/70">{deltaLabel}</p> : null}
              {score.low_confidence ? (
                <span className="mt-1 inline-flex rounded-full bg-white/12 px-2 py-0.5 text-[11px] font-bold text-white/75">
                  Low confidence
                </span>
              ) : null}
            </div>
          </div>

          <p data-testid="therapy-score-callout" className="mt-4 text-xs leading-4 text-white/75">{score.callout}</p>
        </div>

        <div data-testid="therapy-score-components" className="space-y-2">
          <TherapyGauge
            testId="therapy-component-ahi"
            label="AHI"
            value={score.components.ahi ? session.ahi : null}
            max={30}
            target={5}
            targetLabel="Target <5"
            zones={[
              { end: 5, tone: 'good' },
              { end: 15, tone: 'caution' },
              { end: 30, tone: 'poor' },
            ]}
            displayValue={session.ahi == null
              ? 'Unavailable'
              : `${session.ahi.toFixed(1)} ${session.ahi === 1 ? 'event' : 'events'}/hr`}
            tone={session.ahi == null ? 'unavailable' : session.ahi < 5 ? 'good' : session.ahi <= 15 ? 'caution' : 'poor'}
          />
          <TherapyGauge
            testId="therapy-component-leak"
            label="Leak"
            value={score.components.leak ? leakLpm : null}
            max={48}
            target={24}
            targetLabel="Large leak 24 L/min"
            zones={[
              { end: 18, tone: 'good' },
              { end: 24, tone: 'caution' },
              { end: 48, tone: 'poor' },
            ]}
            displayValue={leakLpm == null ? 'Unavailable' : `${leakLpm.toFixed(1)} L/min`}
            tone={leakLpm == null ? 'unavailable' : leakLpm < 18 ? 'good' : leakLpm <= 24 ? 'caution' : 'poor'}
          />
          <TherapyGauge
            testId="therapy-component-duration"
            label="Duration"
            value={score.components.duration ? session.duration_hours : null}
            max={10}
            target={4}
            targetLabel="Compliance 4h · goal 7h"
            secondaryTarget={7}
            zones={[
              { end: 4, tone: 'poor' },
              { end: 7, tone: 'caution' },
              { end: 10, tone: 'good' },
            ]}
            displayValue={score.components.duration
              ? `${session.duration_hours.toFixed(1)} ${session.duration_hours === 1 ? 'hour' : 'hours'}`
              : 'Unavailable'}
            tone={!score.components.duration ? 'unavailable' : session.duration_hours < 4 ? 'poor' : session.duration_hours < 7 ? 'caution' : 'good'}
          />
          <TherapyGauge
            testId="therapy-component-spo2"
            label="SpO2"
            value={null}
            max={100}
            target={90}
            targetLabel="No oximetry data"
            zones={[]}
            displayValue="Unavailable"
            tone="unavailable"
          />
        </div>
      </CardContent>
    </Card>
  )
}

function TherapyGauge({
  testId,
  label,
  value,
  max,
  target,
  secondaryTarget,
  targetLabel,
  zones,
  displayValue,
  tone,
}: {
  testId: string
  label: string
  value: number | null
  max: number
  target: number
  secondaryTarget?: number
  targetLabel: string
  zones: Array<{ end: number; tone: 'good' | 'caution' | 'poor' }>
  displayValue: string
  tone: 'good' | 'caution' | 'poor' | 'unavailable'
}) {
  const valuePercent = value == null ? 0 : Math.min(100, Math.max(0, (value / max) * 100))
  const targetPercent = Math.min(100, Math.max(0, (target / max) * 100))
  const secondaryTargetPercent = secondaryTarget == null
    ? null
    : Math.min(100, Math.max(0, (secondaryTarget / max) * 100))
  const toneClasses = {
    good: {
      row: 'border-[rgba(152,188,105,0.36)] bg-[rgba(152,188,105,0.10)]',
      value: 'text-[#d5edb8]',
      marker: 'bg-[#d5edb8]',
    },
    caution: {
      row: 'border-[rgba(240,214,74,0.40)] bg-[rgba(240,214,74,0.12)]',
      value: 'text-[#fff0a0]',
      marker: 'bg-[#fff0a0]',
    },
    poor: {
      row: 'border-[rgba(255,138,128,0.42)] bg-[rgba(255,138,128,0.13)]',
      value: 'text-[#ffc1bb]',
      marker: 'bg-[#ffc1bb]',
    },
    unavailable: {
      row: 'border-white/10 bg-white/[0.06]',
      value: 'text-white/60',
      marker: 'bg-white/45',
    },
  }[tone]

  return (
    <div
      data-testid={testId}
      data-score-tone={tone}
      className={`rounded-[10px] border px-3 py-2 ${toneClasses.row}`}
    >
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="font-bold text-white">{label}</span>
        <span className={`truncate text-right font-semibold ${toneClasses.value}`}>
          {displayValue}
        </span>
      </div>
      <div className="relative mt-2 h-3 rounded-full bg-white/18">
        {tone !== 'unavailable' ? (
          <div className="absolute inset-0 flex overflow-hidden rounded-full">
            {zones.map((zone, index) => {
              const start = index === 0 ? 0 : zones[index - 1].end
              const width = ((zone.end - start) / max) * 100
              const zoneClass = zone.tone === 'good'
                ? 'bg-[#829f62]'
                : zone.tone === 'caution'
                  ? 'bg-[#c7ad55]'
                  : 'bg-[#c47775]'
              return <span key={`${zone.tone}-${zone.end}`} className={zoneClass} style={{ width: `${width}%` }} />
            })}
          </div>
        ) : null}
        {tone !== 'unavailable' ? (
          <>
            <span
              aria-label={`${label} target`}
              className="absolute inset-y-[-3px] w-0.5 -translate-x-1/2 bg-white/90 shadow-[0_0_0_1px_rgba(38,36,96,0.45)]"
              style={{ left: `${targetPercent}%` }}
            />
            {secondaryTargetPercent != null ? (
              <span
                aria-label={`${label} goal`}
                className="absolute inset-y-[-3px] w-px -translate-x-1/2 bg-white/75"
                style={{ left: `${secondaryTargetPercent}%` }}
              />
            ) : null}
            <span
              aria-label={`${label} value`}
              className={`absolute -top-1.5 h-6 w-1.5 -translate-x-1/2 rounded-full border border-[rgba(38,36,96,0.55)] shadow-[0_1px_4px_rgba(24,22,72,0.45)] ${toneClasses.marker}`}
              style={{ left: `${valuePercent}%` }}
            />
          </>
        ) : null}
      </div>
      <p className="mt-1.5 text-[10px] font-medium text-white/55">{targetLabel}</p>
    </div>
  )
}

function TherapyScoreHelp() {
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label="About therapy score"
        aria-describedby="therapy-score-help"
        className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface-strong)] text-[11px] font-semibold text-[var(--accent)] transition hover:bg-[var(--accent-soft)] hover:text-[var(--accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(82,81,167,0.22)]"
      >
        i
      </button>
      <span
        id="therapy-score-help"
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-7 z-[100] hidden w-[min(20rem,calc(100vw-2rem))] -translate-x-1/2 rounded-[18px] border border-[var(--border)] bg-[var(--popover-surface)] p-3 text-left text-xs font-medium normal-case leading-5 tracking-normal text-[var(--muted-foreground)] shadow-[0_16px_48px_rgba(0,0,0,0.16)] group-hover:block group-focus-within:block sm:left-0 sm:translate-x-0"
      >
        Therapy Score is a 0-100 summary based on AHI, leak, usage duration, and SpO2 when available. Default weights are AHI 40, leak 25, duration 20, and SpO2 15. If a component is unavailable, its weight is redistributed. Low confidence means the session was not parser-validated.
      </span>
    </span>
  )
}
