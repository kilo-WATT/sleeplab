import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api } from '../api/client'
import { getDisplayTz } from '../lib/displayTz'
import { leakToLpm } from '../lib/units'
import type { SessionDetail as SessionDetailType, EventRecord, MetricsResponse, SpO2Response, InferredEquipment, WearableData, EventWindowResponse, TherapyScoreComponent, SessionTherapyContext, MachineSettingsSnapshot, WaveformSignalResponse } from '../api/client'
import FullNightFlowChart from '../components/FullNightFlowChart'
import WearableSleepStageChart from '../components/WearableSleepStageChart'
import { ChevronLeftIcon, ChevronRightIcon } from '../components/icons/ChevronIcons'
import EventInspector from '../components/EventInspector'
import EventTimeline from '../components/EventTimeline'
import InfoPopover from '../components/InfoPopover'
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

const EVENT_WINDOW_PRESETS: Record<number, { before: number; after: number }> = {
  1: { before: 30, after: 30 },
  3: { before: 60, after: 120 },
  5: { before: 120, after: 180 },
}

/**
 * React component or element to render the e v e n t_ w i n d o w_ d o w n s a m p l e.
 *
 * @returns The rendered React element.
 */
const EVENT_WINDOW_DOWNSAMPLE: Record<number, number> = {
  1: 2,
  3: 4,
  5: 6,
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

const THERAPY_COMPONENTS: Array<{ key: keyof SessionDetailType['therapy_score']['components']; label: string }> = [
  { key: 'ahi', label: 'AHI' },
  { key: 'leak', label: 'Leak' },
  { key: 'duration', label: 'Duration' },
  { key: 'spo2', label: 'SpO2' },
]

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
  const [isEventInspectorOpen, setIsEventInspectorOpen] = useState(false)
  const [eventWindow, setEventWindow] = useState<EventWindowResponse | null>(null)
  const [eventWindowLoading, setEventWindowLoading] = useState(false)
  const [eventWindowMinutes, setEventWindowMinutes] = useState(3)
  const eventWindowCacheRef = useRef<Map<string, EventWindowResponse>>(new Map())
  const eventInspectorRef = useRef<HTMLDivElement>(null)

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
    setIsEventInspectorOpen(false)
    setEventWindow(null)
    setEventWindowLoading(false)
    eventWindowCacheRef.current.clear()
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

  useEffect(() => {
    if (!session || !selectedEventId) return
    const preset = EVENT_WINDOW_PRESETS[eventWindowMinutes] ?? EVENT_WINDOW_PRESETS[3]
    const waveformDownsample = EVENT_WINDOW_DOWNSAMPLE[eventWindowMinutes] ?? 2
    const cacheKey = `${selectedEventId}:${eventWindowMinutes}`
    const cached = eventWindowCacheRef.current.get(cacheKey)
    if (cached) {
      setEventWindow(cached)
      setEventWindowLoading(false)
      return
    }

    let cancelled = false
    setEventWindowLoading(true)
    api.getEventWindow(session.id, selectedEventId, {
      before_seconds: preset.before,
      after_seconds: preset.after,
      waveform_downsample: waveformDownsample,
    }).then((data) => {
      eventWindowCacheRef.current.set(cacheKey, data)
      if (!cancelled) setEventWindow(data)
    }).catch(() => {
      if (!cancelled) setEventWindow(null)
    }).finally(() => {
      if (!cancelled) setEventWindowLoading(false)
    })

    return () => {
      cancelled = true
    }
  }, [eventWindowMinutes, selectedEventId, session])

  useEffect(() => {
    if (!isEventInspectorOpen) return
    window.requestAnimationFrame(() => {
      eventInspectorRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    })
  }, [isEventInspectorOpen, selectedEventId])

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
    setIsEventInspectorOpen(true)
    setWaveformLoading(true)
    setWaveformError(null)
    setWaveformWindow(clampWaveformWindow(new Date(event.event_datetime).getTime(), 5))
  }

  const selectedEventIndex = selectedEventId == null
    ? -1
    : events.findIndex((event) => event.id === selectedEventId)
  const selectedEvent = selectedEventIndex >= 0 ? events[selectedEventIndex] : null

  function inspectRelativeEvent(delta: -1 | 1) {
    if (selectedEventIndex < 0) return
    const nextEvent = events[selectedEventIndex + delta]
    if (nextEvent) {
      inspectEvent(nextEvent)
    }
  }

  function clearSelectedEvent() {
    setSelectedEventId(null)
    setIsEventInspectorOpen(false)
    setEventWindow(null)
    setWaveformLoading(true)
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
      setIsEventInspectorOpen(false)
      setEventWindow(null)
      eventWindowCacheRef.current.clear()
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
  const badge = ahiBadge(session.ahi)
  const tagsChanged = !sameTags(tagsDraft, session.tags ?? [])
  const hasDeviceSettings = Boolean(session.therapy_mode || session.mask_type || session.humidity_level != null || session.temperature_c != null)
  const normalizedSettings = therapyContext?.settings?.normalized_settings
  const hasEquipment = Boolean(equipment && (equipment.cushion || equipment.headgear || equipment.tubing || equipment.humidifier_chamber || equipment.filter))
  const availability = session.data_availability
  const isParserBacked = availability.import_backend === 'cpap-parser'

  const statHelp = {
    ahi: 'AHI means apnea-hypopnea index: the average number of breathing events per hour during this session.',
    usage: 'How long the machine was running and delivering therapy during this session.',
    pressure: 'Average pressure is the typical treatment pressure delivered during the night. The 95th percentile shows the pressure level stayed at or below for most of the session.',
    events: 'This is the total count of breathing disturbances during this night. CA means central apnea, OA means obstructive apnea, and H means hypopnea.',
    leak: 'Leak shows how much air escaped from the mask or mouth. Lower leaks usually mean the machine can treat events more effectively.',
    respRate: 'Respiratory rate is the average number of breaths per minute recorded by the machine during the session.',
    tidalVolume: 'Tidal volume is the estimated amount of air moved in a normal breath. It is one more signal of breathing pattern, not usually something patients tune directly.',
  }
  const secondaryStatContentClass = 'px-4 pb-4 pt-4 sm:px-5 sm:pb-5 sm:pt-5'
  const secondaryStatLabelClass = 'text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)] sm:text-xs sm:tracking-[0.14em]'
  const secondaryStatValueClass = 'text-2xl font-semibold text-[var(--foreground)] sm:text-3xl'
  const secondaryStatNoteClass = 'mt-1.5 text-[11px] leading-4 text-[var(--muted-foreground)] sm:text-xs'

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

      {/* Session header card */}
      <Card className="overflow-hidden bg-[radial-gradient(circle_at_top_right,_rgba(106,161,54,0.10),_transparent_30%),radial-gradient(circle_at_top_left,_rgba(82,81,167,0.08),_transparent_24%),var(--surface-strong)]">
        <CardContent className="px-4 pb-5 pt-4 sm:px-6 sm:pb-6 sm:pt-5">
          <div className="flex justify-end">
            <div className="flex flex-wrap justify-end gap-2">
              {isParserBacked ? (
                <span className="shrink-0 rounded-full bg-[rgba(82,81,167,0.10)] px-3 py-1 text-xs font-bold text-[var(--accent)]">
                  ResMed cpap-parser import
                </span>
              ) : null}
              <span className={`shrink-0 rounded-full px-3 py-1 text-xs font-bold ${badge.className}`}>
                {badge.label}
              </span>
            </div>
          </div>

          {/* Date + time */}
          <h1 className="mt-2 text-2xl font-extrabold text-[var(--foreground)] sm:text-3xl">
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

          {isTimezoneEditorOpen ? (
            <form className="mt-3 flex flex-col gap-3 rounded-[14px] border border-[var(--border)] bg-[var(--surface-soft)] p-3 sm:flex-row sm:items-end" onSubmit={handleTimezoneSubmit}>
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
              <Button type="submit" disabled={isTimezoneSubmitting || !timezoneDraft}>
                {isTimezoneSubmitting ? 'Updating...' : 'Update timezone'}
              </Button>
            </form>
          ) : null}
          {timezoneMessage ? <p className="mt-2 text-sm font-medium text-[var(--olive-deep)]">{timezoneMessage}</p> : null}
          {timezoneError ? <p className="mt-2 text-sm text-[var(--danger-text)]">{timezoneError}</p> : null}

          {/* Three primary stat pills */}
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="rounded-[18px] bg-[rgba(82,81,167,0.08)] px-3 py-4 text-center sm:px-4">
              <p className={secondaryStatLabelClass}>AHI</p>
              <p className="mt-1.5 text-3xl font-semibold text-[var(--accent)]">
                {session.ahi?.toFixed(1) ?? '—'}
              </p>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">events/hr</p>
            </div>
            <div className="rounded-[18px] bg-[rgba(106,161,54,0.08)] px-3 py-4 text-center sm:px-4">
              <p className={secondaryStatLabelClass}>Usage</p>
              <p className="mt-1.5 text-3xl font-semibold text-[var(--green-700)]">
                {hours}h {mins}m
              </p>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">therapy usage</p>
              {session.wall_clock_seconds != null && session.gap_seconds != null && (
                <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                  {(session.wall_clock_seconds / 3600).toFixed(1)}h span · {(session.gap_seconds / 60).toFixed(0)}m gaps
                </p>
              )}
            </div>
            <div className="rounded-[18px] bg-[var(--surface-soft)] px-3 py-4 text-center sm:px-4">
              <p className={secondaryStatLabelClass}>Pressure</p>
              <p className="mt-1.5 text-3xl font-semibold text-[var(--foreground)]">
                {session.avg_pressure?.toFixed(1) ?? '—'}
              </p>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                avg cmH₂O · P95 {session.p95_pressure?.toFixed(1) ?? '—'}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <NightDataCoverage session={session} />

      <SessionAICard sessionId={session.id} />

      {/* Secondary stats */}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Card>
          <CardContent className={secondaryStatContentClass}>
            <p className={secondaryStatLabelClass}>Events</p>
            <div className="mt-2 flex items-end gap-2">
              <span className={secondaryStatValueClass}>{session.total_ahi_events}</span>
              <InfoPopover title="Events">{statHelp.events}</InfoPopover>
            </div>
            <p className={secondaryStatNoteClass}>
              CA {session.central_apnea_count} · OA {session.obstructive_apnea_count} · H {session.hypopnea_count}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className={secondaryStatContentClass}>
            <p className={secondaryStatLabelClass}>Avg leak</p>
            <div className="mt-2 flex items-end gap-2">
              <span className={secondaryStatValueClass}>
                {leakToLpm(session.avg_leak, session.leak_unit)?.toFixed(1) ?? '—'}
              </span>
              <InfoPopover title="Average leak">{statHelp.leak}</InfoPopover>
            </div>
            <p className={secondaryStatNoteClass}>avg L/min · P95 {leakToLpm(session.p95_leak, session.leak_unit)?.toFixed(1) ?? '—'} L/min</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className={secondaryStatContentClass}>
            <p className={secondaryStatLabelClass}>Avg resp rate</p>
            <div className="mt-2 flex items-end gap-2">
              <span className={secondaryStatValueClass}>{session.avg_resp_rate?.toFixed(1) ?? '—'}</span>
              <InfoPopover title="Respiratory rate">{statHelp.respRate}</InfoPopover>
            </div>
            <p className={secondaryStatNoteClass}>avg breaths per minute</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className={secondaryStatContentClass}>
            <p className={secondaryStatLabelClass}>Avg tidal vol</p>
            <div className="mt-2 flex items-end gap-2">
              <span className={secondaryStatValueClass}>
                {session.avg_tidal_vol != null ? (session.avg_tidal_vol * 1000).toFixed(0) : '—'}
              </span>
              <InfoPopover title="Tidal volume">{statHelp.tidalVolume}</InfoPopover>
            </div>
            <p className={secondaryStatNoteClass}>avg mL</p>
          </CardContent>
        </Card>
      </div>

      <section aria-labelledby="daily-review-heading" className="space-y-4">
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
          {selectedEvent ? (
            <Button variant="outline" size="sm" onClick={clearSelectedEvent}>
              Clear event and show whole night
            </Button>
          ) : null}
        </div>
        <div className="grid items-start gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
          <Card className="xl:sticky xl:top-4">
            <CardHeader className="pb-3">
              <CardTitle>Events</CardTitle>
              <CardDescription>Select an event to center the graph stack and open detailed waveforms.</CardDescription>
            </CardHeader>
            <CardContent>
              <EventTable events={events} selectedEventId={selectedEventId} onSelectEvent={inspectEvent} />
            </CardContent>
          </Card>
          <div className="min-w-0 space-y-4">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle>Event flags</CardTitle>
                <CardDescription>All tracks below use this same visible time window.</CardDescription>
              </CardHeader>
              <CardContent>
                <EventTimeline
                  events={events}
                  durationSeconds={session.duration_seconds}
                  startDatetime={session.start_datetime}
                  timeDomain={reviewTimeDomain}
                  selectedEventId={selectedEventId}
                  onSelectEvent={inspectEvent}
                />
              </CardContent>
            </Card>
            {fullNightFlow ? (
              <FullNightFlowChart
                waveform={fullNightFlow}
                events={events}
                timeDomain={reviewTimeDomain}
                wholeNight={waveformWindow == null}
                loading={waveformLoading}
                onSelectEvent={inspectEvent}
                onSelectWindow={selectWaveformWindow}
                onPan={panWaveformWindow}
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
            {isEventInspectorOpen && selectedEvent ? (
              <div ref={eventInspectorRef} className="scroll-mt-4">
                <EventInspector
                  data={eventWindow}
                  loading={eventWindowLoading}
                  windowMinutes={eventWindowMinutes}
                  hasPreviousEvent={selectedEventIndex > 0}
                  hasNextEvent={selectedEventIndex >= 0 && selectedEventIndex < events.length - 1}
                  onClose={() => setIsEventInspectorOpen(false)}
                  onWindowMinutesChange={setEventWindowMinutes}
                  onPreviousEvent={() => inspectRelativeEvent(-1)}
                  onNextEvent={() => inspectRelativeEvent(1)}
                />
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <div className="grid items-stretch gap-6">
        <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
          <TherapyScoreCard session={session} />
          {therapyContext && (
            <Card>
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
          {!therapyContext && hasDeviceSettings && (
            <Card>
              <CardContent className={secondaryStatContentClass}>
                <p className="mb-3 text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">Device settings</p>
                <div className="flex flex-wrap gap-x-6 gap-y-1.5 text-sm">
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
              </CardContent>
            </Card>
          )}

          {hasEquipment && equipment && (
            <Card>
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

          <Card>
            <CardHeader className="p-4 pb-2 sm:p-5 sm:pb-2">
              <CardTitle>Notes</CardTitle>
              {session.note ? <CardDescription>Saved note for this night.</CardDescription> : null}
            </CardHeader>
            <CardContent className="px-4 pb-4 pt-0 sm:px-5 sm:pb-5 sm:pt-0">
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
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="p-4 pb-2 sm:p-5 sm:pb-2">
              <CardTitle>Tags</CardTitle>
              {session.tags.length ? <CardDescription>Saved tags for this night.</CardDescription> : null}
            </CardHeader>
            <CardContent className="px-4 pb-4 pt-0 sm:px-5 sm:pb-5 sm:pt-0">
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
            </CardContent>
          </Card>
        </div>
      </div>

      {spo2 ? (
        <SpO2Chart spo2={spo2} wearable={wearableData} />
      ) : (
        <UnavailableDataCard
          title="Oximetry unavailable"
          description={isParserBacked
            ? 'This ResMed cpap-parser workflow does not yet claim SpO2 or pulse support. SleepLab leaves those values unavailable instead of estimating them.'
            : 'No SpO2 or pulse samples were imported for this night.'}
        />
      )}

      {wearableData && wearableData.stages.length > 0 && (
        <WearableSleepStageChart stages={wearableData.stages} />
      )}

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
    <Card>
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

function UnavailableDataCard({ title, description }: { title: string; description: string }) {
  return (
    <Card>
      <CardContent className="p-5 sm:p-6">
        <p className="text-base font-bold text-[var(--foreground)]">{title}</p>
        <p className="mt-1.5 max-w-3xl text-sm leading-6 text-[var(--muted-foreground)]">{description}</p>
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
      <div className="mt-4 hidden min-h-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-[var(--border)] md:flex">
        <div className="max-h-80 overflow-auto">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-[var(--surface-strong)] text-[var(--muted-foreground)]">
            <tr>
              <th className="px-3 py-2 font-bold uppercase tracking-[0.12em]">Code</th>
              <th className="px-3 py-2 font-bold uppercase tracking-[0.12em]">Time</th>
              <th className="px-3 py-2 font-bold uppercase tracking-[0.12em]">Duration</th>
              <th className="px-3 py-2 font-bold uppercase tracking-[0.12em]">Type</th>
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
                  <td className="px-3 py-2 text-[var(--muted-foreground)]">
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

function TherapyScoreCard({ session }: { session: SessionDetailType }) {
  const score = session.therapy_score
  const delta = session.score_vs_30d_avg
  const deltaLabel = delta == null
    ? null
    : `${delta > 0 ? '+' : ''}${delta.toFixed(1)} vs 30d avg`

  return (
    <Card>
      <CardContent className="px-4 pb-4 pt-4 sm:px-5 sm:pb-5 sm:pt-5">
        <div className="mb-3 flex items-center gap-1.5">
          <p className="whitespace-nowrap text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">Therapy score</p>
          <TherapyScoreHelp />
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[12px] bg-[rgba(82,81,167,0.10)] text-2xl font-extrabold leading-none text-[var(--accent)]">
            {score.total}
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-1.5 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-4 sm:gap-y-1.5">
            <span className="text-lg font-bold leading-6 text-[var(--foreground)] sm:text-xl">Grade {score.grade}</span>
            {deltaLabel ? <span className="text-sm font-medium text-[var(--muted-foreground)]">{deltaLabel}</span> : null}
            {score.low_confidence ? (
              <span className="w-fit rounded-full bg-[var(--surface-soft)] px-2 py-0.5 text-[11px] font-bold text-[var(--muted-foreground)]">
                Low confidence
              </span>
            ) : null}
          </div>
        </div>

        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {THERAPY_COMPONENTS.map(({ key, label }) => (
            <TherapyComponentRow key={key} label={label} component={score.components[key]} />
          ))}
        </div>

        <p className="mt-3 text-sm leading-5 text-[var(--muted-foreground)]">{score.callout}</p>
      </CardContent>
    </Card>
  )
}

function TherapyComponentRow({ label, component }: { label: string; component: TherapyScoreComponent | null }) {
  const pct = component ? Math.round((component.score / component.max_score) * 100) : 0
  const value = component?.value == null
    ? null
    : `${component.value.toFixed(component.unit === 'hours' ? 1 : 0)} ${component.unit ?? ''}`.trim()

  return (
    <div className="rounded-[12px] bg-[var(--surface-soft)] px-3 py-2.5">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="font-bold text-[var(--foreground)]">{label}</span>
        <span className="truncate text-right text-[var(--muted-foreground)]">
          {component ? (value ?? `${pct}%`) : 'Unavailable'}
        </span>
      </div>
      <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-[var(--border)]">
        <div
          className="h-full rounded-full bg-[var(--accent)]"
          style={{ width: `${component ? pct : 0}%` }}
        />
      </div>
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
        className="pointer-events-none absolute left-1/2 top-7 z-50 hidden w-[min(20rem,calc(100vw-2rem))] -translate-x-1/2 rounded-[18px] border border-[var(--border)] bg-[var(--popover-surface)] p-3 text-left text-xs font-medium normal-case leading-5 tracking-normal text-[var(--muted-foreground)] shadow-[0_16px_48px_rgba(0,0,0,0.08)] group-hover:block group-focus-within:block sm:left-auto sm:right-0 sm:translate-x-0"
      >
        Therapy Score is a 0-100 summary based on AHI, leak, usage duration, and SpO2 when available. Default weights are AHI 40, leak 25, duration 20, and SpO2 15. If a component is unavailable, its weight is redistributed. Low confidence means the session was not parser-validated.
      </span>
    </span>
  )
}
