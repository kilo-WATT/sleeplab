import { type FormEvent, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import type { SessionDetail } from '../api/client'
import { api } from '../api/client'
import { ahiColor, ahiLabel, type NightCell } from '../lib/nightExplorer'
import { leakToLpm } from '../lib/units'
import { CalendarIcon } from './icons/ChevronIcons'
import { EYEBROW, MICRO_LABEL } from './nightExplorerUi'
import { Button } from './ui/button'
import { Card } from './ui/card'

/** Properties and structure for the night detail panel. */
interface Props {
  selectedDate: string | null
  cell: NightCell | null
}

function formatLongDate(iso: string): string {
  const [year, month, day] = iso.split('-').map(Number)
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-[14px] border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-2.5">
      <p className={MICRO_LABEL}>{label}</p>
      <p className="text-lg font-extrabold leading-none text-[var(--foreground)]">{value}</p>
      <p className="text-[11px] leading-none text-[var(--muted-foreground)]">{hint ?? ' '}</p>
    </div>
  )
}

/**
 * Selected-night inspector for the Night Explorer. Shows quick metrics derived
 * from the bulk list immediately, then lazily enriches with the per-night detail
 * (waveform availability, mask/equipment context, note) without turning the page
 * into a dashboard. Actions route into the Session page and Event Inspector.
 *
 * @returns The rendered React element.
 */
export default function NightDetailPanel({ selectedDate, cell }: Props) {
  const navigate = useNavigate()
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [noteOpen, setNoteOpen] = useState(false)
  const [noteDraft, setNoteDraft] = useState('')
  const [noteSaving, setNoteSaving] = useState(false)

  useEffect(() => {
    if (!selectedDate) return

    let cancelled = false

    async function loadDetail(date: string) {
      setLoading(true)
      setError(null)
      setNoteOpen(false)
      setDetail(null)
      try {
        const data = await api.getSessionByDate(date)
        if (cancelled) return
        setDetail(data)
        setNoteDraft(data.note ?? '')
      } catch (err) {
        if (!cancelled) setError(String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadDetail(selectedDate)

    return () => {
      cancelled = true
    }
  }, [selectedDate])

  if (!selectedDate || !cell) {
    return (
      <Card className="h-full" data-testid="night-detail-empty">
        <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
          <span
            className="flex h-11 w-11 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface-soft)] text-[var(--accent)]"
            aria-hidden="true"
          >
            <CalendarIcon className="h-5 w-5" />
          </span>
          <div className="space-y-1.5">
            <p className="text-sm font-extrabold leading-none text-[var(--foreground)]">No night selected</p>
            <p className="mx-auto max-w-[15rem] text-xs leading-snug text-[var(--muted-foreground)]">
              Pick a night on the calendar to preview its therapy summary before opening the full session.
            </p>
          </div>
        </div>
      </Card>
    )
  }

  async function handleNoteSubmit(event: FormEvent) {
    event.preventDefault()
    if (!detail) return
    setNoteSaving(true)
    try {
      const updated = await api.updateSessionNote(detail.id, noteDraft)
      setDetail(updated)
      setNoteDraft(updated.note ?? '')
      setNoteOpen(false)
    } catch (err) {
      setError(String(err))
    } finally {
      setNoteSaving(false)
    }
  }

  const availability = detail?.data_availability
  const leakLpm = detail
    ? leakToLpm(detail.p95_leak ?? detail.avg_leak, detail.leak_unit)
    : cell.leak
  const equipmentOverrides = detail?.equipment_overrides ?? {}
  const equipmentSummary = [detail?.mask_type, detail?.therapy_mode].filter(Boolean).join(' · ')
  const machineSummary = [detail?.machine_family, detail?.machine_model].filter(Boolean).join(' ')

  return (
    <Card className="h-full" data-testid="night-detail">
      <div className="space-y-4 p-5">
        <div className="flex min-h-7 items-start justify-between gap-3">
          <div className="min-w-0 space-y-1.5">
            <p className={EYEBROW}>Selected night</p>
            <h3 className="text-lg font-extrabold leading-tight text-[var(--foreground)]">
              {formatLongDate(selectedDate)}
            </h3>
          </div>
          <span
            className="shrink-0 rounded-full px-3 py-1 text-xs font-bold leading-none text-white"
            style={{ background: ahiColor(cell.ahi) }}
            data-testid="night-detail-severity"
          >
            {ahiLabel(cell.ahi)}
          </span>
        </div>

        {cell.missingData ? (
          <p className="rounded-[12px] border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-2 text-xs text-[var(--muted-foreground)]">
            This night logged {cell.hours.toFixed(1)}h of usage but has no detailed recording (e.g. the SD card was out).
          </p>
        ) : null}

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="AHI" value={cell.ahi == null ? '—' : cell.ahi.toFixed(1)} hint={ahiLabel(cell.ahi)} />
          <Stat label="Usage" value={`${cell.hours.toFixed(1)}h`} hint={cell.shortUsage ? 'Below 4h' : 'Compliant'} />
          <Stat
            label="Leak"
            value={leakLpm == null ? '—' : `${leakLpm.toFixed(0)}`}
            hint={leakLpm == null ? 'Not recorded' : detail?.p95_leak != null ? 'L/min (p95)' : 'L/min (avg)'}
          />
          <Stat label="Events" value={cell.recorded && cell.events != null ? String(cell.events) : '—'} hint="total flagged" />
          {detail ? (
            <>
              <Stat
                label="Central / Obstr."
                value={`${detail.central_apnea_count ?? 0} / ${detail.obstructive_apnea_count ?? 0}`}
              />
              <Stat label="Hypopnea" value={String(detail.hypopnea_count ?? 0)} />
            </>
          ) : null}
        </div>

        {loading ? (
          <p className="text-xs text-[var(--muted-foreground)]" data-testid="night-detail-loading">
            Loading night details…
          </p>
        ) : null}

        {error ? (
          <p className="text-xs font-semibold text-[var(--danger-text)]">Couldn’t load full details: {error}</p>
        ) : null}

        {detail ? (
          <div className="space-y-2 text-xs text-[var(--muted-foreground)]" data-testid="night-detail-context">
            {availability ? (
              <p>
                <span className="font-bold text-[var(--foreground)]">Waveforms:</span>{' '}
                {availability.full_night_flow_available
                  ? 'Full-night flow available'
                  : availability.event_waveforms_available
                    ? 'Event waveforms available'
                    : 'Not stored for this night'}
                {availability.spo2_available ? ' · SpO₂ available' : ''}
              </p>
            ) : null}
            {equipmentSummary || machineSummary ? (
              <p>
                <span className="font-bold text-[var(--foreground)]">Setup:</span>{' '}
                {[equipmentSummary, machineSummary].filter(Boolean).join(' · ')}
              </p>
            ) : null}
            {cell.equipmentChanges.length > 0 ? (
              <p>
                <span className="font-bold text-[var(--foreground)]">Equipment change:</span>{' '}
                {cell.equipmentChanges.map((change) => change.label).join(', ')} started this day
              </p>
            ) : null}
            {Object.keys(equipmentOverrides).length > 0 ? (
              <p>
                <span className="font-bold text-[var(--foreground)]">Per-night override:</span>{' '}
                {Object.keys(equipmentOverrides).join(', ')}
              </p>
            ) : null}
          </div>
        ) : null}

        {detail && !noteOpen ? (
          <div className="rounded-[12px] border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-2.5" data-testid="night-detail-note">
            <p className={MICRO_LABEL}>Note</p>
            <p className="mt-1.5 whitespace-pre-wrap text-sm text-[var(--foreground)]">
              {detail.note?.trim() ? detail.note : <span className="text-[var(--muted-foreground)]">No note yet.</span>}
            </p>
          </div>
        ) : null}

        {detail && noteOpen ? (
          <form className="space-y-2" onSubmit={handleNoteSubmit} data-testid="night-detail-note-form">
            <label htmlFor="night-note" className={MICRO_LABEL}>
              Night note
            </label>
            <textarea
              id="night-note"
              value={noteDraft}
              onChange={(event) => setNoteDraft(event.target.value)}
              rows={3}
              className="w-full rounded-[12px] border border-[var(--border)] bg-[var(--surface-strong)] px-3 py-2 text-sm text-[var(--foreground)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-border)]"
              placeholder="e.g. congested, new mask, travelled…"
            />
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={noteSaving}>
                {noteSaving ? 'Saving…' : 'Save note'}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => { setNoteOpen(false); setNoteDraft(detail.note ?? '') }}>
                Cancel
              </Button>
            </div>
          </form>
        ) : null}

        <div className="flex flex-wrap gap-2 pt-1">
          <Button size="sm" onClick={() => navigate(`/sessions/${selectedDate}`)}>
            Open session
          </Button>
          {availability?.events_available ? (
            <Button size="sm" variant="outline" onClick={() => navigate(`/sessions/${selectedDate}#daily-review`)}>
              Open Event Inspector
            </Button>
          ) : null}
          {detail && !noteOpen ? (
            <Button size="sm" variant="ghost" onClick={() => setNoteOpen(true)}>
              {detail.note?.trim() ? 'Edit note' : 'Add note'}
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  )
}
