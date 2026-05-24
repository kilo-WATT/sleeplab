import type { MachineSettingsChange, MachineSettingsSnapshot } from '../api/client'
import { Badge } from './ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'

interface Props {
  history: MachineSettingsSnapshot[]
  changes: MachineSettingsChange[]
}

function formatPressure(min: number | null, max: number | null) {
  if (min == null && max == null) return null
  if (min != null && max != null) return `${min.toFixed(1)}-${max.toFixed(1)} cmH2O`
  if (min != null) return `Min ${min.toFixed(1)} cmH2O`
  return `Max ${max?.toFixed(1)} cmH2O`
}

function formatCurrent(snapshot: MachineSettingsSnapshot | undefined) {
  if (!snapshot) return []
  return [
    ['Mode', snapshot.therapy_mode?.toUpperCase() ?? null],
    ['Pressure', formatPressure(snapshot.pressure_min, snapshot.pressure_max)],
    ['EPR', snapshot.epr_setting],
    ['Ramp', snapshot.ramp_setting],
    ['Humidity', snapshot.humidity_level != null ? String(snapshot.humidity_level) : null],
    ['Mask', snapshot.mask_type],
    ['Temp', snapshot.temperature_c != null ? `${snapshot.temperature_c} C` : null],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]))
}

export default function SettingsHistoryCard({ history, changes }: Props) {
  const latest = history.length > 0 ? history[history.length - 1] : undefined
  const currentSettings = formatCurrent(latest)
  const recentChanges = changes.slice(-5).reverse()
  const apapPressureHidden = latest?.therapy_mode === 'apap' && latest.pressure_min == null && latest.pressure_max == null

  return (
    <Card id="settings-history">
      <CardHeader>
        <CardTitle>Settings history</CardTitle>
      </CardHeader>
      <CardContent>
        {currentSettings.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {currentSettings.map(([label, value]) => (
              <Badge key={label} className="rounded-[8px] px-3 py-1.5">
                <span className="text-[var(--muted-foreground)]">{label}</span>
                <span className="ml-1 font-semibold">{value}</span>
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-sm text-[var(--muted-foreground)]">No locally detected machine settings are available yet.</p>
        )}

        <div className="mt-5">
          <p className="text-sm font-bold text-[var(--foreground)]">Recent changes</p>
          {recentChanges.length > 0 ? (
            <div className="mt-3 divide-y divide-[var(--border)] overflow-hidden rounded-[8px] border border-[var(--border)]">
              {recentChanges.map((change) => (
                <div key={`${change.folder_date}-${change.changed_fields.map((field) => field.field).join('-')}`} className="bg-[var(--surface-soft)] px-4 py-3">
                  <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm font-semibold text-[var(--foreground)]">{change.folder_date}</p>
                    <p className="text-xs text-[var(--muted-foreground)]">
                      AHI {change.before.ahi?.toFixed(1) ?? '-'} to {change.after.ahi?.toFixed(1) ?? '-'}
                    </p>
                  </div>
                  <div className="mt-2 grid gap-2 md:grid-cols-2">
                    {change.changed_fields.map((field) => (
                      <div key={field.field} className="text-sm">
                        <span className="font-medium text-[var(--foreground)]">{field.label}</span>
                        <span className="ml-2 text-[var(--muted-foreground)]">
                          {field.before ?? 'unset'} to {field.after ?? 'unset'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-sm text-[var(--muted-foreground)]">No detected setting changes in the selected history window.</p>
          )}
        </div>
        <p className="mt-4 text-xs text-[var(--muted-foreground)]">
          Mode, fixed CPAP pressure, and EPR are detected from local machine data when available. APAP pressure min/max are not shown because local DATALOG files expose delivered pressure, not the prescribed APAP range.
          {apapPressureHidden ? ' Ramp, humidity, mask, and temperature are not available in the parsed local files.' : ' Ramp, humidity, mask, and temperature appear only when the local files expose them.'}
        </p>
      </CardContent>
    </Card>
  )
}
