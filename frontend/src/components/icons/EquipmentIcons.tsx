import type { SVGProps } from 'react'

/**
 * Generic line-art icon for a CPAP mask / cushion.
 *
 * Deliberately generic — these are simple inline SVGs, not manufacturer product
 * imagery, so the page stays visual without any licensing concerns.
 *
 * @returns The rendered React element.
 */
export function MaskIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden={true} {...props}>
      <path
        d="M10 3.6c3.1 0 5.3 2.9 5.3 6.4 0 3.4-2.3 6.2-5.3 6.2s-5.3-2.8-5.3-6.2c0-3.5 2.2-6.4 5.3-6.4Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M4.9 7.6 2.4 6.5M15.1 7.6 17.6 6.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="8.4" cy="9.4" r="0.9" fill="currentColor" />
      <circle cx="11.6" cy="9.4" r="0.9" fill="currentColor" />
    </svg>
  )
}

/**
 * Generic line-art icon for mask headgear / straps.
 *
 * @returns The rendered React element.
 */
export function HeadgearIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden={true} {...props}>
      <circle cx="10" cy="10" r="4.4" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M5.6 8.4 2.6 8.4M5.6 11.6 2.6 11.6M14.4 8.4 17.4 8.4M14.4 11.6 17.4 11.6"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path d="M2.6 7.2v5.6M17.4 7.2v5.6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  )
}

/**
 * Generic line-art icon for CPAP tubing / hose.
 *
 * @returns The rendered React element.
 */
export function TubingIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden={true} {...props}>
      <path
        d="M3.2 13.5c2.6 0 2.6-7 5.2-7s2.6 7 5.2 7"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <rect x="13" y="11.2" width="4.2" height="4.6" rx="1.2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M5.2 9.5h6M4.4 12h5.6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" opacity="0.6" />
    </svg>
  )
}

/**
 * Generic line-art icon for a humidifier water chamber.
 *
 * @returns The rendered React element.
 */
export function WaterChamberIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden={true} {...props}>
      <path
        d="M4.8 6h10.4v7.2a2 2 0 0 1-2 2H6.8a2 2 0 0 1-2-2V6Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M4 6h12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <path
        d="M5.4 11.4c1 .9 2 .9 3 0s2-.9 3 0 2 .9 3 0"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/**
 * Generic line-art icon for an air filter pad.
 *
 * @returns The rendered React element.
 */
export function FilterIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden={true} {...props}>
      <rect x="4.2" y="5.2" width="11.6" height="9.6" rx="1.8" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M4.6 8.2 8 4.8M4.6 11.6 11.4 4.8M7 13.8 15.4 5.4M10.4 13.8 15.4 8.8M13.8 13.8 15.4 12.2"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        opacity="0.7"
      />
    </svg>
  )
}

/**
 * Generic line-art icon for a CPAP machine.
 *
 * @returns The rendered React element.
 */
export function MachineIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden={true} {...props}>
      <rect x="3" y="7" width="14" height="8" rx="2" stroke="currentColor" strokeWidth="1.6" />
      <rect x="5" y="9.2" width="4.4" height="3.6" rx="0.8" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="13.4" cy="11" r="1.7" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}
