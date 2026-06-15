import { useEffect, useState, type ComponentType, type SVGProps } from 'react'
import { api } from '../api/client'
import type { Equipment, EquipmentCreate, EquipmentType } from '../api/client'
import { Button } from './ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import {
  FilterIcon,
  HeadgearIcon,
  MaskIcon,
  TubingIcon,
  WaterChamberIcon,
} from './icons/EquipmentIcons'

/** Icon for each tracked equipment type. */
const EQUIPMENT_TYPE_ICONS: Record<EquipmentType, ComponentType<SVGProps<SVGSVGElement>>> = {
  cushion: MaskIcon,
  headgear: HeadgearIcon,
  tubing: TubingIcon,
  humidifier_chamber: WaterChamberIcon,
  filter: FilterIcon,
}

/** Display labels for each tracked equipment type. */
const TYPE_LABELS: Record<EquipmentType, string> = {
  cushion: 'Cushion / Pillow',
  headgear: 'Headgear',
  tubing: 'Tubing',
  humidifier_chamber: 'Water Chamber',
  filter: 'Filter',
}

/** Stable display + iteration order for the equipment categories. */
const TYPE_ORDER: EquipmentType[] = ['cushion', 'headgear', 'tubing', 'humidifier_chamber', 'filter']

const MASK_CATEGORIES = ['Nasal', 'Nasal Pillows', 'Full Face', 'Hybrid']

/**
 * Type definition for the replacement unit.
 */
type ReplacementUnit = 'days' | 'weeks' | 'months' | 'years'

/** Number of days represented by one of each replacement-interval unit. */
const REPLACEMENT_UNIT_DAYS: Record<ReplacementUnit, number> = {
  days: 1,
  weeks: 7,
  months: 30,
  years: 365,
}

// US insurance replacement intervals by type.
// Cushion default is 15d (nasal); updates to 30d when Full Face / Hybrid is selected.
const DEFAULT_REPLACEMENT_DAYS: Record<EquipmentType, number> = {
  cushion: 15,
  headgear: 180,
  tubing: 90,
  humidifier_chamber: 180,
  filter: 30,
}

/**
 * Helper function for cushion days for category.
 */
function cushionDaysForCategory(category: string | null): number {
  if (category === 'Full Face' || category === 'Hybrid') return 30
  return 15
}

/** Coarse health classification for a tracked item's replacement timeline. */
type StatusKind = 'overdue' | 'due-soon' | 'on-track' | 'none'

interface StatusInfo {
  kind: StatusKind
  /** Full, color-independent description, e.g. "Overdue by 3 days". */
  label: string
  /** Short status word for compact pills, e.g. "Overdue". */
  short: string
  /** Tailwind classes for the status pill (text + background). */
  pillClass: string
  /** Tailwind classes for the progress-bar fill. */
  barClass: string
  /** Progress through the replacement interval, 0–1, or null when unknown. */
  fraction: number | null
}

const DAY = (n: number) => `${n} ${n === 1 ? 'day' : 'days'}`

/**
 * Classify an item's replacement timeline into a status with display treatment.
 *
 * The text labels are written to stand on their own without color, so the status
 * remains clear to anyone who can't distinguish the accent hues.
 */
function statusInfo(item: Equipment): StatusInfo {
  if (!item.replacement_days || item.days_in_use == null) {
    return {
      kind: 'none',
      label: 'No replacement reminder set',
      short: 'No reminder',
      pillClass: 'bg-[var(--surface-muted)] text-[var(--muted-foreground)]',
      barClass: 'bg-[var(--neutral-200)]',
      fraction: null,
    }
  }
  const remaining = item.replacement_days - item.days_in_use
  const fraction = Math.min(1, Math.max(0, item.days_in_use / item.replacement_days))
  if (remaining < 0) {
    return {
      kind: 'overdue',
      label: `Overdue by ${DAY(-remaining)}`,
      short: 'Overdue',
      pillClass: 'bg-[var(--danger-soft)] text-[var(--danger-text)]',
      barClass: 'bg-[var(--danger-text)]',
      fraction,
    }
  }
  if (remaining <= 14) {
    return {
      kind: 'due-soon',
      label: remaining === 0 ? 'Due today' : remaining === 1 ? 'Due tomorrow' : `Due in ${DAY(remaining)}`,
      short: 'Due soon',
      pillClass: 'bg-[var(--warning-soft)] text-[var(--warning-text)]',
      barClass: 'bg-[var(--warning-text)]',
      fraction,
    }
  }
  return {
    kind: 'on-track',
    label: `${DAY(remaining)} until replacement`,
    short: 'On track',
    pillClass: 'bg-[var(--success-soft)] text-[var(--success-text)]',
    barClass: 'bg-[var(--success-text)]',
    fraction,
  }
}

/**
 * Helper function for equipment label.
 */
function equipmentLabel(item: Equipment): string {
  return [item.brand, item.model].filter(Boolean).join(' ') || TYPE_LABELS[item.equipment_type]
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Format an ISO `YYYY-MM-DD` date as e.g. "Jun 1, 2025" without timezone drift. */
function formatDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  return `${MONTHS[m - 1]} ${d}, ${y}`
}

/**
 * Helper function for infer replacement unit.
 */
function inferReplacementUnit(days: number | null | undefined): ReplacementUnit {
  if (!days) return 'days'
  if (days % REPLACEMENT_UNIT_DAYS.years === 0) return 'years'
  if (days % REPLACEMENT_UNIT_DAYS.months === 0) return 'months'
  if (days % REPLACEMENT_UNIT_DAYS.weeks === 0) return 'weeks'
  return 'days'
}

/**
 * Helper function for replacement interval value.
 */
function replacementIntervalValue(days: number | null | undefined, unit: ReplacementUnit): string {
  if (!days) return ''
  const value = days / REPLACEMENT_UNIT_DAYS[unit]
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
}

/** Render the icon for an equipment type inside a soft rounded tile. */
function TypeIcon({ type, className }: { type: EquipmentType; className?: string }) {
  const Icon = EQUIPMENT_TYPE_ICONS[type]
  return <Icon className={className ?? 'h-5 w-5'} />
}

const EMPTY_FORM: EquipmentCreate = {
  equipment_type: 'cushion',
  start_date: new Date().toISOString().slice(0, 10),
  replacement_days: DEFAULT_REPLACEMENT_DAYS['cushion'],
  mask_category: null,
  brand: null,
  model: null,
  notes: null,
}

/**
 * Equipment dashboard: surfaces the active mask, replacement health, and the full
 * tracked inventory grouped by category, with add / edit / replace controls.
 *
 * @returns The rendered React element.
 */
export default function EquipmentCatalog() {
  const [items, setItems] = useState<Equipment[]>([])
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [replacingFrom, setReplacingFrom] = useState<Equipment | null>(null)
  const [form, setForm] = useState<EquipmentCreate>({ ...EMPTY_FORM })
  const [replacementIntervalUnit, setReplacementIntervalUnit] = useState<ReplacementUnit>(
    inferReplacementUnit(EMPTY_FORM.replacement_days),
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  useEffect(() => {
    api.listEquipment().then(setItems).catch(() => {})
  }, [])

  function openAdd(type: EquipmentType = 'cushion') {
    setForm({ ...EMPTY_FORM, equipment_type: type, replacement_days: DEFAULT_REPLACEMENT_DAYS[type] })
    setReplacementIntervalUnit(inferReplacementUnit(DEFAULT_REPLACEMENT_DAYS[type]))
    setEditingId(null)
    setReplacingFrom(null)
    setError(null)
    setShowForm(true)
  }

  function openReplacement(item: Equipment) {
    setReplacementIntervalUnit(inferReplacementUnit(item.replacement_days))
    setForm({
      equipment_type: item.equipment_type,
      start_date: new Date().toISOString().slice(0, 10),
      replacement_days: item.replacement_days,
      mask_category: item.mask_category,
      brand: item.brand,
      model: item.model,
      notes: item.notes,
    })
    setEditingId(null)
    setReplacingFrom(item)
    setError(null)
    setShowForm(true)
  }

  function openEdit(item: Equipment) {
    setReplacementIntervalUnit(inferReplacementUnit(item.replacement_days))
    setForm({
      equipment_type: item.equipment_type,
      start_date: item.start_date,
      replacement_days: item.replacement_days,
      mask_category: item.mask_category,
      brand: item.brand,
      model: item.model,
      notes: item.notes,
    })
    setEditingId(item.id)
    setReplacingFrom(null)
    setError(null)
    setShowForm(true)
  }

  function cancelForm() {
    setShowForm(false)
    setEditingId(null)
    setReplacingFrom(null)
  }

  function setField<K extends keyof EquipmentCreate>(key: K, value: EquipmentCreate[K]) {
    if (key === 'equipment_type') {
      const t = value as EquipmentType
      const replacementDays = DEFAULT_REPLACEMENT_DAYS[t]
      setReplacementIntervalUnit(inferReplacementUnit(replacementDays))
      setForm(f => ({
        ...f,
        equipment_type: t,
        replacement_days: replacementDays,
        mask_category: t !== 'cushion' ? null : f.mask_category,
      }))
    } else if (key === 'mask_category') {
      const cat = value as string | null
      const replacementDays = cushionDaysForCategory(cat)
      setReplacementIntervalUnit(inferReplacementUnit(replacementDays))
      setForm(f => ({
        ...f,
        mask_category: cat,
        replacement_days: f.equipment_type === 'cushion' ? replacementDays : f.replacement_days,
      }))
    } else {
      setForm(f => ({ ...f, [key]: value }))
    }
  }

  function setReplacementInterval(value: string) {
    const numericValue = Number(value)
    setForm(f => ({
      ...f,
      replacement_days: value && Number.isFinite(numericValue) && numericValue > 0
        ? Math.round(numericValue * REPLACEMENT_UNIT_DAYS[replacementIntervalUnit])
        : null,
    }))
  }

  function setReplacementUnit(unit: ReplacementUnit) {
    const currentValue = replacementIntervalValue(form.replacement_days, replacementIntervalUnit)
    setReplacementIntervalUnit(unit)
    if (currentValue) {
      const numericValue = Number(currentValue)
      setForm(f => ({
        ...f,
        replacement_days: Number.isFinite(numericValue) && numericValue > 0
          ? Math.round(numericValue * REPLACEMENT_UNIT_DAYS[unit])
          : null,
      }))
    }
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      if (editingId) {
        const updated = await api.updateEquipment(editingId, {
          start_date: form.start_date,
          replacement_days: form.replacement_days,
          mask_category: form.mask_category,
          brand: form.brand,
          model: form.model,
          notes: form.notes,
        })
        setItems(prev => prev.map(i => i.id === editingId ? updated : i))
      } else {
        const created = await api.createEquipment(form)
        // A logged replacement inherits "in use" from the item it replaces.
        if (replacingFrom?.is_default) {
          await handleSetDefault(created.id, created.equipment_type)
        }
        setItems(prev => [created, ...prev])
      }
      cancelForm()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save equipment')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    if (deletingId !== id) { setDeletingId(id); return }
    try {
      await api.deleteEquipment(id)
      setItems(prev => prev.filter(i => i.id !== id))
    } catch {
      // silently ignore — item still visible
    } finally {
      setDeletingId(null)
    }
  }

  async function handleSetDefault(id: string, type: EquipmentType) {
    // Optimistically flip the default within the type; reconcile on error.
    setItems(prev => prev.map(i =>
      i.equipment_type === type ? { ...i, is_default: i.id === id } : i,
    ))
    try {
      await api.setDefaultEquipment(id)
    } catch {
      api.listEquipment().then(setItems).catch(() => {})
    }
  }

  function onMaskSelect(value: string) {
    if (value === '__add__') {
      openAdd('cushion')
      return
    }
    if (value) void handleSetDefault(value, 'cushion')
  }

  const grouped = Object.fromEntries(
    TYPE_ORDER.map(t => [t, items.filter(i => i.equipment_type === t)]),
  ) as Record<EquipmentType, Equipment[]>

  // The mask shown by default on each night: the chosen default, else most recent.
  const activeMask = grouped.cushion.find(c => c.is_default) ?? grouped.cushion[0] ?? null

  const withStatus = items.map(item => ({ item, status: statusInfo(item) }))
  const attention = withStatus
    .filter(({ status }) => status.kind === 'overdue' || status.kind === 'due-soon')
    .sort((a, b) => {
      // Overdue first, then soonest-due.
      const rank = (s: StatusInfo) => (s.kind === 'overdue' ? 0 : 1)
      if (rank(a.status) !== rank(b.status)) return rank(a.status) - rank(b.status)
      const ra = (a.item.replacement_days ?? 0) - (a.item.days_in_use ?? 0)
      const rb = (b.item.replacement_days ?? 0) - (b.item.days_in_use ?? 0)
      return ra - rb
    })

  const dueSoonCount = withStatus.filter(({ status }) => status.kind === 'due-soon').length
  const overdueCount = withStatus.filter(({ status }) => status.kind === 'overdue').length
  const inUseCount = items.filter(i => i.is_default).length
  const lastLogged = items.reduce<string | null>(
    (latest, i) => (latest && latest >= i.start_date ? latest : i.start_date),
    null,
  )

  const summary: { label: string; value: string; sub?: string; tone?: string }[] = [
    { label: 'Tracked items', value: String(items.length), sub: inUseCount ? `${inUseCount} in use` : undefined },
    {
      label: 'Due soon',
      value: String(dueSoonCount),
      tone: dueSoonCount ? 'text-[var(--warning-text)]' : undefined,
    },
    {
      label: 'Overdue',
      value: String(overdueCount),
      tone: overdueCount ? 'text-[var(--danger-text)]' : undefined,
    },
    { label: 'Last logged', value: lastLogged ? formatDate(lastLogged) : '—' },
  ]

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h2 className="text-2xl font-extrabold tracking-tight text-[var(--foreground)]">Equipment</h2>
        <p className="text-sm text-[var(--muted-foreground)]">
          Track your CPAP gear, see what's in use, and stay ahead of replacements. SleepLab shows age and reminders on each session.
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {summary.map(card => (
          <Card key={card.label} className="bg-[var(--surface-strong)]">
            <CardContent className="p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.1em] text-[var(--muted-foreground)]">{card.label}</p>
              <p className={`mt-1 text-2xl font-extrabold ${card.tone ?? 'text-[var(--foreground)]'}`}>{card.value}</p>
              {card.sub && <p className="text-xs text-[var(--muted-foreground)]">{card.sub}</p>}
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Current setup hero */}
      <Card className="overflow-hidden bg-[radial-gradient(circle_at_top_left,_var(--accent-soft),_transparent_55%),var(--surface-strong)]">
        <CardHeader className="pb-3">
          <CardTitle className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--accent)]">Current setup</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {activeMask ? (
            <ActiveMaskHero
              item={activeMask}
              status={statusInfo(activeMask)}
              onLogReplacement={() => openReplacement(activeMask)}
            />
          ) : (
            <div className="flex flex-col items-start gap-3 rounded-[16px] bg-[var(--surface-soft)] p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-3">
                <span className="flex h-11 w-11 items-center justify-center rounded-[14px] bg-[var(--accent-soft)] text-[var(--accent)]">
                  <TypeIcon type="cushion" className="h-6 w-6" />
                </span>
                <div>
                  <p className="text-sm font-bold text-[var(--foreground)]">No active mask yet</p>
                  <p className="text-xs text-[var(--muted-foreground)]">Add your mask to start tracking its age and replacements.</p>
                </div>
              </div>
              <Button onClick={() => openAdd('cushion')}>Add a mask</Button>
            </div>
          )}

          {/* Change active mask — preserves the original "mask in use" behavior. */}
          {grouped.cushion.length > 0 && (
            <div className="flex flex-col gap-2 border-t border-[var(--border)] pt-3 sm:flex-row sm:items-center sm:justify-between">
              <Label htmlFor="mask-in-use" className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--muted-foreground)]">
                Active mask
              </Label>
              <select
                id="mask-in-use"
                value={activeMask?.id ?? ''}
                onChange={e => onMaskSelect(e.target.value)}
                className="flex h-10 w-full rounded-full border border-[var(--border)] bg-[var(--surface-strong)] px-4 py-2 text-sm text-[var(--foreground)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-border)] sm:max-w-xs"
              >
                {grouped.cushion.map(c => (
                  <option key={c.id} value={c.id}>
                    {equipmentLabel(c)}{c.mask_category ? ` · ${c.mask_category}` : ''}
                  </option>
                ))}
                <option value="__add__">＋ Add a new mask…</option>
              </select>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Needs attention */}
      {attention.length > 0 && (
        <Card className="bg-[var(--surface-strong)]">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Needs attention</CardTitle>
            <CardDescription>
              {overdueCount > 0
                ? `${overdueCount} overdue${dueSoonCount > 0 ? ` · ${dueSoonCount} due soon` : ''}`
                : `${dueSoonCount} due soon`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {attention.map(({ item, status }) => (
              <div
                key={item.id}
                className="flex flex-col gap-3 rounded-[14px] bg-[var(--surface-soft)] px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[12px] bg-[var(--surface-strong)] text-[var(--accent)]">
                    <TypeIcon type={item.equipment_type} />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-bold text-[var(--foreground)]">
                      {equipmentLabel(item)}
                      {item.mask_category ? <span className="font-medium text-[var(--muted-foreground)]"> · {item.mask_category}</span> : null}
                    </p>
                    <p className="text-xs text-[var(--muted-foreground)]">
                      <span className={`font-bold ${status.kind === 'overdue' ? 'text-[var(--danger-text)]' : 'text-[var(--warning-text)]'}`}>
                        {status.label}
                      </span>
                      {item.days_in_use != null && ` · ${item.days_in_use}d in use`}
                      {item.replacement_days != null && ` · replace every ${item.replacement_days}d`}
                    </p>
                  </div>
                </div>
                <Button size="sm" onClick={() => openReplacement(item)} className="shrink-0">
                  Log replacement
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Equipment by category */}
      <Card className="bg-[var(--surface-strong)]">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Your equipment</CardTitle>
          <CardDescription>Everything you track, grouped by category.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {TYPE_ORDER.map(type => (
            <div key={type}>
              <div className="mb-2 flex items-center gap-2">
                <span className="flex h-7 w-7 items-center justify-center rounded-[9px] bg-[var(--accent-soft)] text-[var(--accent)]">
                  <TypeIcon type={type} className="h-4 w-4" />
                </span>
                <p className="text-xs font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
                  {TYPE_LABELS[type]}
                </p>
                <span className="ml-auto text-xs text-[var(--muted-foreground)]">{grouped[type].length || ''}</span>
              </div>
              {grouped[type].length === 0 ? (
                <button
                  type="button"
                  onClick={() => openAdd(type)}
                  className="w-full rounded-[14px] border border-dashed border-[var(--border)] px-4 py-3 text-left text-xs text-[var(--muted-foreground)] transition hover:border-[var(--accent-border)] hover:text-[var(--accent)]"
                >
                  None tracked yet — add {TYPE_LABELS[type].toLowerCase()}
                </button>
              ) : (
                <div className="space-y-2">
                  {grouped[type].map(item => {
                    const status = statusInfo(item)
                    const label = equipmentLabel(item)
                    const categoryTag = item.mask_category ? ` · ${item.mask_category}` : ''
                    return (
                      <div key={item.id} className="rounded-[14px] bg-[var(--surface-soft)] px-4 py-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-[var(--foreground)]">
                              <span className="truncate">{label}{categoryTag}</span>
                              {item.is_default && (
                                <span className="shrink-0 rounded-full bg-[var(--accent-soft)] px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--accent)]">
                                  In use
                                </span>
                              )}
                              <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.08em] ${status.pillClass}`}>
                                {status.short}
                              </span>
                            </p>
                            <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                              Started {formatDate(item.start_date)}
                              {item.days_in_use != null && ` · ${item.days_in_use}d in use`}
                              {status.kind !== 'none' && <> · {status.label}</>}
                            </p>
                          </div>
                          <div className="flex shrink-0 gap-2">
                            <Button variant="outline" size="sm" onClick={() => openReplacement(item)}>Replace</Button>
                            <Button variant="ghost" size="sm" onClick={() => openEdit(item)}>Edit</Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className={deletingId === item.id ? 'text-[var(--danger-text)]' : ''}
                              onClick={() => handleDelete(item.id)}
                            >
                              {deletingId === item.id ? 'Confirm' : 'Remove'}
                            </Button>
                          </div>
                        </div>
                        {status.fraction != null && (
                          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-muted)]">
                            <div
                              className={`h-full rounded-full ${status.barClass}`}
                              style={{ width: `${Math.round(status.fraction * 100)}%` }}
                            />
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Add / Edit / Replace form */}
      {showForm && (
        <Card className="bg-[var(--surface-strong)]">
          <CardContent className="space-y-4 p-5 sm:p-6">
            <p className="text-sm font-bold text-[var(--foreground)]">
              {editingId ? 'Edit equipment' : replacingFrom ? 'Log replacement' : 'Add equipment'}
            </p>

            {replacingFrom && (
              <div className="rounded-[14px] border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-3 text-xs text-[var(--muted-foreground)]">
                Replacing <span className="font-bold text-[var(--foreground)]">{equipmentLabel(replacingFrom)}</span>
                {' · '}started {formatDate(replacingFrom.start_date)}
                {replacingFrom.days_in_use != null && ` · ${replacingFrom.days_in_use}d used`}
                . The new item keeps the same details and starts today.
              </div>
            )}

            {!editingId && !replacingFrom && (
              <div className="space-y-2">
                <Label>Type</Label>
                <div className="flex flex-wrap gap-2">
                  {TYPE_ORDER.map(t => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setField('equipment_type', t)}
                      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium border transition ${
                        form.equipment_type === t
                          ? 'bg-[var(--accent)] text-[var(--accent-foreground)] border-[var(--accent)]'
                          : 'border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--accent)]'
                      }`}
                    >
                      <TypeIcon type={t} className="h-3.5 w-3.5" />
                      {TYPE_LABELS[t]}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {form.equipment_type === 'cushion' && (
              <>
                <div className="space-y-2">
                  <Label>Mask type</Label>
                  <div className="flex flex-wrap gap-2">
                    {MASK_CATEGORIES.map(c => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => setField('mask_category', form.mask_category === c ? null : c)}
                        className={`rounded-full px-3 py-1 text-xs font-medium border transition ${
                          form.mask_category === c
                            ? 'bg-[var(--accent)] text-[var(--accent-foreground)] border-[var(--accent)]'
                            : 'border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--accent)]'
                        }`}
                      >
                        {c}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="eq-brand">Brand</Label>
                    <Input id="eq-brand" value={form.brand ?? ''} placeholder="e.g. ResMed"
                      onChange={e => setField('brand', e.target.value || null)} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="eq-model">Model</Label>
                    <Input id="eq-model" value={form.model ?? ''} placeholder="e.g. AirFit P10"
                      onChange={e => setField('model', e.target.value || null)} />
                  </div>
                </div>
              </>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="eq-start">Start date</Label>
                <Input id="eq-start" type="date" value={form.start_date}
                  onChange={e => setField('start_date', e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="eq-replace">Replace every</Label>
                <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_9rem]">
                  <Input
                    id="eq-replace"
                    inputMode="decimal"
                    value={replacementIntervalValue(form.replacement_days, replacementIntervalUnit)}
                    placeholder="Optional"
                    onChange={e => setReplacementInterval(e.target.value)}
                  />
                  <select
                    value={replacementIntervalUnit}
                    onChange={e => setReplacementUnit(e.target.value as ReplacementUnit)}
                    className="flex h-11 w-full rounded-full border border-[var(--border)] bg-[var(--surface-strong)] px-4 py-2 text-sm text-[var(--foreground)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-border)]"
                  >
                    <option value="days" className="bg-[var(--surface-strong)] text-[var(--foreground)]">Days</option>
                    <option value="weeks" className="bg-[var(--surface-strong)] text-[var(--foreground)]">Weeks</option>
                    <option value="months" className="bg-[var(--surface-strong)] text-[var(--foreground)]">Months</option>
                    <option value="years" className="bg-[var(--surface-strong)] text-[var(--foreground)]">Years</option>
                  </select>
                </div>
                {form.replacement_days ? (
                  <p className="text-xs text-[var(--muted-foreground)]">
                    Saved as {form.replacement_days} days for reminders.
                  </p>
                ) : null}
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="eq-notes">Notes</Label>
              <Input id="eq-notes" value={form.notes ?? ''} placeholder="Optional"
                onChange={e => setField('notes', e.target.value || null)} />
            </div>

            {error && <p className="text-sm text-[var(--danger-text)]">{error}</p>}

            <div className="flex gap-2">
              <Button onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : editingId ? 'Update' : replacingFrom ? 'Log replacement' : 'Add'}
              </Button>
              <Button variant="outline" onClick={cancelForm}>Cancel</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {!showForm && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-[var(--muted-foreground)]">
            Track masks, cushions, headgear, tubing, water chambers, and filters.
          </p>
          <Button onClick={() => openAdd('cushion')} className="gap-1.5">
            <span aria-hidden="true" className="text-base leading-none">＋</span> Add equipment
          </Button>
        </div>
      )}
    </div>
  )
}

/** Hero block describing the currently active mask and its replacement health. */
function ActiveMaskHero({
  item,
  status,
  onLogReplacement,
}: {
  item: Equipment
  status: StatusInfo
  onLogReplacement: () => void
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 gap-4">
        <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[16px] bg-[var(--accent-soft)] text-[var(--accent)]">
          <TypeIcon type={item.equipment_type} className="h-7 w-7" />
        </span>
        <div className="min-w-0 space-y-2">
          <div>
            <p className="flex flex-wrap items-center gap-2 text-lg font-extrabold text-[var(--foreground)]">
              <span className="truncate">{equipmentLabel(item)}</span>
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.08em] ${status.pillClass}`}>
                {status.short}
              </span>
            </p>
            <p className="text-xs text-[var(--muted-foreground)]">
              {TYPE_LABELS[item.equipment_type]}
              {item.mask_category ? ` · ${item.mask_category}` : ''}
            </p>
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--muted-foreground)]">
            <span>Started <span className="font-semibold text-[var(--foreground)]">{formatDate(item.start_date)}</span></span>
            {item.days_in_use != null && (
              <span><span className="font-semibold text-[var(--foreground)]">{item.days_in_use}</span> days in use</span>
            )}
            {item.replacement_days != null && (
              <span>Replace every <span className="font-semibold text-[var(--foreground)]">{item.replacement_days}d</span></span>
            )}
          </div>
          <div className="space-y-1">
            {status.fraction != null && (
              <div className="h-2 w-full max-w-xs overflow-hidden rounded-full bg-[var(--surface-muted)]">
                <div
                  className={`h-full rounded-full ${status.barClass}`}
                  style={{ width: `${Math.round(status.fraction * 100)}%` }}
                />
              </div>
            )}
            <p className="text-xs font-semibold text-[var(--foreground)]">{status.label}</p>
          </div>
        </div>
      </div>
      <Button onClick={onLogReplacement} className="shrink-0">Log replacement</Button>
    </div>
  )
}
