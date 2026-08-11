/**
 * Instrument chart kit — the Foundation console language applied to data.
 *
 * The dashboard's "Fabric Integrity" radiant is the reference: what makes it
 * read as an instrument rather than a chart is a small, repeatable set of
 * devices, and this module is those devices factored out so every chart in
 * the app can wear them.
 *
 *   bezel        corner brackets around the plot — it reads as a housing
 *   tick collar  a regular fine tick rail along the frame edges
 *   glow         the data mark emits light rather than just being drawn
 *   survey grid  sparse dotted hairlines, recessive to the point of nearly
 *                vanishing
 *   live edge    a pulsing marker on the newest sample, so a live chart
 *                visibly reads as live
 *
 * Everything decorative here sits OUTSIDE the plot area and carries no data
 * meaning — a tick collar that implied value positions would be lying, so the
 * collar is fixed to the frame and never to the scale.
 */
import { ReactNode } from 'react'

// ── shared chrome values ─────────────────────────────────────────────────────
export const INSTRUMENT = {
  gold:    '#d8b46e',
  goldHi:  '#f5e2b6',
  ice:     '#8ad8ea',
  ink:     '#dcd6c9',
  inkDim:  '#a9a294',
  grid:    '#2a2418',
  surface: '#0d1219',
  hair:    'rgba(216,180,110,.34)',
  hairSoft:'rgba(216,180,110,.20)',
}

/** Axis props shared by every Cartesian chart, so they cannot drift apart. */
export const axisTick = { fontSize: 9, fill: INSTRUMENT.inkDim, letterSpacing: 0.5 }
export const axisProps = { axisLine: false, tickLine: false, tick: axisTick }

/** Tooltip styling matching the console surfaces. */
export const tooltipProps = {
  contentStyle: {
    background: 'rgba(13,18,25,.96)',
    border: `1px solid ${INSTRUMENT.hair}`,
    borderRadius: 0,
    fontSize: 11,
    fontFamily: 'ui-monospace, SF Mono, Menlo, monospace',
    boxShadow: '0 0 24px rgba(216,180,110,.10)',
  },
  labelStyle: { color: INSTRUMENT.inkDim, fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase' as const },
  itemStyle: { color: INSTRUMENT.ink },
  cursor: { stroke: INSTRUMENT.hair, strokeWidth: 1, strokeDasharray: '3 3' },
}

/** Grid: sparse dotted hairlines, horizontal only — vertical rules fight the data. */
export const gridProps = {
  strokeDasharray: '1 5',
  stroke: 'rgba(216,180,110,.22)',
  vertical: false,
}

/** A soft emissive halo for line/area strokes. */
export const glow = (color: string, strength = 5) =>
  ({ filter: `drop-shadow(0 0 ${strength}px ${color}66)` })

// ── Bezel + tick collar ──────────────────────────────────────────────────────

/**
 * Wraps a chart in an instrument housing: corner brackets plus a fine tick
 * rail along the bottom and left edges. Purely chrome — it is anchored to the
 * frame, never to the scale.
 */
export function InstrumentFrame({
  children,
  height,
  ticks = 48,
  className = '',
  live = false,
}: {
  children: ReactNode
  height: number | string
  ticks?: number
  className?: string
  live?: boolean
}) {
  const collar = Array.from({ length: ticks }, (_, i) => {
    const long = i % 6 === 0
    return { pct: (i / (ticks - 1)) * 100, len: long ? 5 : 2.5, op: long ? 0.55 : 0.25 }
  })

  return (
    <div className={`relative ${className}`} style={{ height }}>
      {/* corner brackets — the housing. Percent coordinates are not valid in
          SVG path data, so these are positioned elements, not a path. */}
      {([
        { pos: 'top-0 left-0',     border: 'border-t border-l' },
        { pos: 'top-0 right-0',    border: 'border-t border-r' },
        { pos: 'bottom-0 left-0',  border: 'border-b border-l' },
        { pos: 'bottom-0 right-0', border: 'border-b border-r' },
      ] as const).map((c, i) => (
        <span
          key={i}
          aria-hidden="true"
          className={`absolute ${c.pos} ${c.border} w-2 h-2 pointer-events-none z-10`}
          style={{ borderColor: 'rgba(216,180,110,.55)' }}
        />
      ))}

      {/* bottom tick collar */}
      <div className="absolute left-0 right-0 bottom-0 h-[6px] pointer-events-none z-10">
        <svg width="100%" height="6" aria-hidden="true">
          {collar.map((t, i) => (
            <line
              key={i}
              x1={`${t.pct}%`} x2={`${t.pct}%`}
              y1={6 - t.len} y2="6"
              stroke={INSTRUMENT.gold}
              strokeWidth="1"
              opacity={t.op}
            />
          ))}
        </svg>
      </div>

      {/* live indicator */}
      {live && (
        <div className="absolute top-1.5 right-2 flex items-center gap-1.5 pointer-events-none z-10">
          <span
            className="w-1 h-1 rounded-full bg-green-400 f-breathe"
            style={{ boxShadow: '0 0 6px #7ee0a8' }}
          />
          <span className="text-[7px] uppercase tracking-[0.28em] text-gray-500">live</span>
        </div>
      )}

      {children}
    </div>
  )
}

// ── Radial gauge (the radiant) ───────────────────────────────────────────────

/**
 * Concentric survey rings with a value arc and a 72-point tick collar. This is
 * the dashboard's Fabric Integrity dial, generalised: any single bounded
 * percentage can wear it.
 */
export function RadialGauge({
  pct,
  label,
  value,
  size = 192,
  loading = false,
  tone = 'gold',
}: {
  pct: number
  label: string
  value?: string
  size?: number
  loading?: boolean
  tone?: 'gold' | 'ice'
}) {
  const stroke = tone === 'ice' ? INSTRUMENT.ice : INSTRUMENT.gold
  const R = 58
  const C = 2 * Math.PI * R
  const clamped = Math.max(0, Math.min(100, isFinite(pct) ? pct : 0))

  const ticks = Array.from({ length: 72 }, (_, i) => {
    const a = (i / 72) * Math.PI * 2 - Math.PI / 2
    const long = i % 6 === 0
    const r1 = long ? 30 : 34
    return {
      x1: 96 + Math.cos(a) * r1, y1: 96 + Math.sin(a) * r1,
      x2: 96 + Math.cos(a) * 38, y2: 96 + Math.sin(a) * 38,
      o: long ? 0.7 : 0.28,
    }
  })

  return (
    <div className="grid place-items-center py-1">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox="0 0 192 192" fill="none">
          <g className="f-spin-slow">
            <circle cx="96" cy="96" r="88" stroke="rgba(216,180,110,.14)" />
            <circle cx="96" cy="96" r="88" stroke="rgba(216,180,110,.5)" strokeDasharray="2 20" />
            <circle cx="96" cy="8" r="2.4" fill={INSTRUMENT.gold} />
          </g>
          <g className="f-spin-rev">
            <circle cx="96" cy="96" r="72" stroke="rgba(138,216,234,.2)" />
            <circle cx="96" cy="96" r="72" stroke="rgba(138,216,234,.5)" strokeDasharray="34 260" strokeLinecap="round" />
            <circle cx="24" cy="96" r="1.8" fill={INSTRUMENT.ice} />
          </g>
          <circle cx="96" cy="96" r={R} stroke="rgba(216,180,110,.18)" />
          <circle
            cx="96" cy="96" r={R}
            stroke={stroke} strokeWidth="1.6" strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={C * (1 - clamped / 100)}
            transform="rotate(-90 96 96)"
            style={{ transition: 'stroke-dashoffset .8s ease', ...glow(stroke, 4) }}
          />
          <circle cx="96" cy="96" r="44" stroke="rgba(216,180,110,.26)" />
          <g stroke="rgba(216,180,110,.3)">
            {ticks.map((t, i) => (
              <line key={i} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} opacity={t.o} />
            ))}
          </g>
        </svg>
        <div className="absolute inset-0 grid place-content-center text-center">
          <div className={`f-num text-[26px] ${tone === 'ice' ? 'f-num-ice' : 'f-num-gold'}`}>
            {loading ? '—' : (value ?? clamped.toFixed(1))}
          </div>
          <div className="f-lbl mt-1.5">{label}</div>
        </div>
      </div>
    </div>
  )
}

// ── Section heading with a fading rule ───────────────────────────────────────

export function InstrumentHead({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 mb-3.5">
      <span className="f-lbl">{children}</span>
      <div className="flex-1 h-px bg-blue-500/25" />
      {right}
    </div>
  )
}
