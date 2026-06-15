/** Properties and structure for the sparkline props. */
interface SparklineProps {
  /** Ordered values (oldest → newest). Fewer than two points renders nothing. */
  values: number[]
  /** Stroke color (any CSS color or var). */
  color?: string
  width?: number
  height?: number
  className?: string
}

/**
 * Minimal inline sparkline — a single normalized polyline with a dot on the most
 * recent point. Purely decorative trend context for the hero metric cards.
 *
 * @returns The rendered SVG, or null when there is too little data to plot.
 */
export default function Sparkline({
  values,
  color = 'var(--accent)',
  width = 96,
  height = 32,
  className,
}: SparklineProps) {
  if (values.length < 2) return null

  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pad = 2
  const innerW = width - pad * 2
  const innerH = height - pad * 2

  const points = values.map((value, index) => {
    const x = pad + (index / (values.length - 1)) * innerW
    const y = pad + (1 - (value - min) / span) * innerH
    return [x, y] as const
  })

  const path = points.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  const [lastX, lastY] = points[points.length - 1]

  return (
    <svg
      className={className}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <path d={path} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      <circle cx={lastX} cy={lastY} r={2.4} fill={color} />
    </svg>
  )
}
