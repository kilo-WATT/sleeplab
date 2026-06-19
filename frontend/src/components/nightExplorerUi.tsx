import type { ReactNode } from 'react'

import { cn } from '../lib/utils'
import { Card } from './ui/card'

/**
 * Shared presentational primitives for the Night Explorer (Calendar) page.
 *
 * These exist so every card on the page — utility stats, calendar, selected-night
 * inspector, Recent Nights, Import Gaps — shares one header/eyebrow rhythm:
 * zero-margin uppercase eyebrows with normalized line-height and letter spacing,
 * a consistent header min-height and bottom spacing, and uniform body padding.
 */

/** Section eyebrow: small uppercase accent label with reset margins/line-height. */
export const EYEBROW = 'text-[11px] font-bold uppercase leading-none tracking-[0.14em] text-[var(--accent)]'

/** Micro label used inside stat blocks and inline groupings. */
export const MICRO_LABEL =
  'text-[10px] font-bold uppercase leading-none tracking-[0.12em] text-[var(--muted-foreground)]'

interface CardSectionProps {
  /** Uppercase eyebrow label. */
  eyebrow?: ReactNode
  /** Optional one-line description under the eyebrow. */
  description?: ReactNode
  /** Optional right-aligned header content (badge / action). */
  action?: ReactNode
  children: ReactNode
  className?: string
  /** Extra classes for the body wrapper (e.g. spacing of stacked content). */
  bodyClassName?: string
  testId?: string
}

/**
 * A card with a standardized header zone (eyebrow + optional description +
 * optional action) and a body. The header keeps a consistent min-height and
 * bottom margin so cards line up whether or not they carry a description.
 *
 * @returns The rendered React element.
 */
export function CardSection({
  eyebrow,
  description,
  action,
  children,
  className,
  bodyClassName,
  testId,
}: CardSectionProps) {
  const hasHeader = eyebrow != null || description != null || action != null

  return (
    <Card className={className} data-testid={testId}>
      <div className="p-5">
        {hasHeader ? (
          <div className="mb-4 flex min-h-7 items-start justify-between gap-3">
            <div className="min-w-0 space-y-1.5">
              {eyebrow != null ? <p className={EYEBROW}>{eyebrow}</p> : null}
              {description != null ? (
                <p className="text-xs leading-snug text-[var(--muted-foreground)]">{description}</p>
              ) : null}
            </div>
            {action != null ? <div className="shrink-0">{action}</div> : null}
          </div>
        ) : null}
        <div className={cn(bodyClassName)}>{children}</div>
      </div>
    </Card>
  )
}
