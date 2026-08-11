/**
 * Instrument chart kit — the Foundation console language applied to data.
 *
 * pktSNMP's "Fabric Integrity" radiant is the reference: what makes it
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
  sweep = false,
}: {
  children: ReactNode
  height: number | string
  ticks?: number
  className?: string
  live?: boolean
  sweep?: boolean
}) {
  const collar = Array.from({ length: ticks }, (_, i) => {
    const long = i % 6 === 0
    return { pct: (i / (ticks - 1)) * 100, len: long ? 5 : 2.5, op: long ? 0.55 : 0.25 }
  })

  return (
    <div className={`relative ${className}`} style={{ height }}>
      <LiveChartDefs />
      {sweep && <SweepOverlay />}
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

// ── Flow ribbons (Sankey) ────────────────────────────────────────────────────

/**
 * Animation + gradient defs for instrument-style Sankey ribbons.
 *
 * A Sankey already encodes volume in ribbon width; what it does not show is
 * that the thing being drawn is *moving*. A slow dash travelling along each
 * ribbon's centreline restores direction and liveness without adding a second
 * visual variable — the dash carries no magnitude, it only says "this way".
 *
 * Rendered as a <style> inside the chart rather than in the shared stylesheet
 * so the effect travels with the component.
 */
export function FlowDefs({ ribbons }: { ribbons: Array<{ from: string; to: string }> }) {
  return (
    <defs>
      <style>{`
        @keyframes f-flow { to { stroke-dashoffset: -220; } }
        .f-ribbon-pulse {
          animation: f-flow 5.5s linear infinite;
          stroke-dasharray: 3 26;
        }
        @media (prefers-reduced-motion: reduce) {
          .f-ribbon-pulse { animation: none; }
        }
      `}</style>
      {ribbons.map((r, i) => (
        <linearGradient key={i} id={`f-ribbon-${i}`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor={r.from} stopOpacity={0.5} />
          <stop offset="100%" stopColor={r.to} stopOpacity={0.16} />
        </linearGradient>
      ))}
      <filter id="f-ribbon-glow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="2.2" result="b" />
        <feMerge>
          <feMergeNode in="b" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
    </defs>
  )
}

/**
 * A node rail: the hairline equivalent of a Sankey's filled node block, with a
 * lit cap so the endpoint reads as an instrument terminal rather than a bar.
 */
export function NodeRail({
  x, y, h, w = 3, color, side,
}: {
  x: number; y: number; h: number; w?: number; color: string; side: 'src' | 'dst'
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} fill={color} opacity={0.85}
            style={{ filter: `drop-shadow(0 0 4px ${color}66)` }} />
      {/* terminal caps */}
      <rect x={side === 'src' ? x - 1.5 : x + w - 1.5} y={y - 1} width={4.5} height={2} fill={INSTRUMENT.goldHi} opacity={0.9} />
      <rect x={side === 'src' ? x - 1.5 : x + w - 1.5} y={y + h - 1} width={4.5} height={2} fill={INSTRUMENT.goldHi} opacity={0.9} />
    </g>
  )
}

// ── Radial ring (part-to-whole in the radiant idiom) ─────────────────────────

/**
 * Concentric arc segments instead of a pie. Same data, same part-to-whole
 * reading, but it wears the console's geometry — and unlike a pie, comparing
 * arc lengths on a shared radius is an easier judgement than comparing angles.
 */
export function RadialRing({
  segments,
  size = 190,
  label,
  total,
}: {
  segments: Array<{ name: string; value: number; color: string }>
  size?: number
  label?: string
  total?: string
}) {
  const sum = segments.reduce((s, x) => s + x.value, 0) || 1
  // Data rings pulled inward to leave room for the survey collar and the two
  // counter-rotating orbit rings outside them — the orbiting dots are what
  // make the dial read as an instrument rather than a static donut.
  const R0 = 72          // outermost data ring
  const STEP = 12        // spacing between rings
  const C = (r: number) => 2 * Math.PI * r

  const ticks = Array.from({ length: 60 }, (_, i) => {
    const a = (i / 60) * Math.PI * 2 - Math.PI / 2
    const long = i % 5 === 0
    const r1 = long ? 77 : 79.5
    return {
      x1: 96 + Math.cos(a) * r1, y1: 96 + Math.sin(a) * r1,
      x2: 96 + Math.cos(a) * 82, y2: 96 + Math.sin(a) * 82,
      o: long ? 0.6 : 0.24,
    }
  })

  return (
    <div className="grid place-items-center">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox="0 0 192 192" fill="none">
          {/* orbit rings — slow, counter-rotating, each carrying a floating dot */}
          <g className="f-spin-slow">
            <circle cx="96" cy="96" r="92" stroke="rgba(216,180,110,.14)" />
            <circle cx="96" cy="96" r="92" stroke="rgba(216,180,110,.5)" strokeDasharray="2 20" />
            <circle cx="96" cy="4" r="2.4" fill={INSTRUMENT.gold}
                    style={{ filter: `drop-shadow(0 0 4px ${INSTRUMENT.gold})` }} />
          </g>
          <g className="f-spin-rev">
            <circle cx="96" cy="96" r="86" stroke="rgba(138,216,234,.2)" />
            <circle cx="96" cy="96" r="86" strokeDasharray="30 240" strokeLinecap="round"
                    stroke="rgba(138,216,234,.5)" />
            <circle cx="10" cy="96" r="1.9" fill={INSTRUMENT.ice}
                    style={{ filter: `drop-shadow(0 0 4px ${INSTRUMENT.ice})` }} />
          </g>

          <g stroke="rgba(216,180,110,.3)">
            {ticks.map((t, i) => (
              <line key={i} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} opacity={t.o} />
            ))}
          </g>
          {segments.slice(0, 5).map((seg, i) => {
            const r = R0 - i * STEP
            const frac = seg.value / sum
            return (
              <g key={seg.name}>
                <circle cx="96" cy="96" r={r} stroke="rgba(216,180,110,.14)" strokeWidth="6" />
                <circle
                  cx="96" cy="96" r={r}
                  stroke={seg.color} strokeWidth="6" strokeLinecap="round"
                  strokeDasharray={C(r)}
                  strokeDashoffset={C(r) * (1 - frac)}
                  transform="rotate(-90 96 96)"
                  style={{ transition: 'stroke-dashoffset .8s ease', ...glow(seg.color, 4) }}
                />
              </g>
            )
          })}
        </svg>
        {(label || total) && (
          <div className="absolute inset-0 grid place-content-center text-center pointer-events-none">
            {total && <div className="f-num f-num-gold text-[19px]">{total}</div>}
            {label && <div className="f-lbl mt-1.5">{label}</div>}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Making a line chart feel live ────────────────────────────────────────────
//
// Three devices, none of which touch the plotted values:
//
//   sweep       a slow light bar crossing the plot, behind the data — the
//               console is scanning, the way a radar face does
//   live edge   a pulsing marker on the newest sample: "this is now"
//   breathing   the stroke's glow rises and falls gently
//
// The constraint throughout: nothing may move a datum, change its apparent
// value, or imply a reading that is not in the series. Everything here is
// either behind the data or anchored to a real point.

/** Keyframes for the live-chart devices. Mount once per chart. */
export function LiveChartDefs() {
  return (
    <style>{`
      @keyframes f-sweep {
        0%   { transform: translateX(-12%); opacity: 0; }
        8%   { opacity: 1; }
        92%  { opacity: 1; }
        100% { transform: translateX(112%); opacity: 0; }
      }
      @keyframes f-pulse-ring {
        0%   { r: 3; opacity: 0.75; }
        70%  { r: 11; opacity: 0; }
        100% { r: 11; opacity: 0; }
      }
      @keyframes f-glow-breathe {
        0%, 100% { opacity: 0.85; }
        50%      { opacity: 1; }
      }
      .f-sweep-bar { animation: f-sweep 9s cubic-bezier(.4,0,.6,1) infinite; }
      .f-pulse-ring { animation: f-pulse-ring 2.4s ease-out infinite; }
      .f-breathe-stroke { animation: f-glow-breathe 3.6s ease-in-out infinite; }
      @media (prefers-reduced-motion: reduce) {
        .f-sweep-bar, .f-pulse-ring, .f-breathe-stroke { animation: none; }
        .f-sweep-bar { opacity: 0; }
      }
    `}</style>
  )
}

/**
 * A light bar that crosses the plot area on a long loop. Sits behind the data
 * at low opacity — it is chrome, and must never be mistaken for a value.
 */
export function SweepOverlay() {
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none" aria-hidden="true">
      <div
        className="f-sweep-bar absolute top-0 bottom-0 w-[16%]"
        style={{
          background:
            'linear-gradient(90deg, transparent, rgba(216,180,110,.10) 45%, rgba(245,226,182,.16) 50%, rgba(216,180,110,.10) 55%, transparent)',
        }}
      />
    </div>
  )
}

/**
 * Recharts dot renderer that marks only the newest sample, with a pulsing
 * halo. Anchored to a real datum, so it cannot mislead about position.
 *
 *   <Area dot={liveEdgeDot(data.length, colour)} … />
 */
export function liveEdgeDot(dataLength: number, color: string) {
  return function LiveEdge(props: any) {
    const { cx, cy, index } = props
    if (index !== dataLength - 1 || cx == null || cy == null) return <g />
    return (
      <g>
        <circle cx={cx} cy={cy} r="3" className="f-pulse-ring" fill="none" stroke={color} strokeWidth="1.2" />
        <circle cx={cx} cy={cy} r="2.4" fill={color} style={{ filter: `drop-shadow(0 0 5px ${color})` }} />
      </g>
    )
  }
}

/**
 * A travelling highlight that lives *in the line itself*.
 *
 * The stroke is painted with a gradient whose bright band slides from left to
 * right and repeats, so the line reads as a live trace being drawn rather than
 * a static shape. Two properties make this safe:
 *
 *   - the line stays continuous. A dashed stroke would animate just as easily
 *     but would break the series into segments, and a gap in a line chart
 *     means "no data" — it would be saying something untrue.
 *   - only colour moves. No vertex shifts, so every point stays exactly where
 *     its value puts it.
 *
 * Left-to-right matches the time axis, so the motion runs the same direction
 * the data is read.
 *
 * Usage: drop <LinePulseGradient id="x" color="#8ad8ea" /> inside the chart's
 * <defs> and set stroke="url(#x)" on the Area/Line.
 */
export function LinePulseGradient({
  id,
  color,
  duration = 4.5,
  width = 0.16,
  intensity = '#ffffff',
  baseOpacity = 0.55,
}: {
  id: string
  color: string
  duration?: number
  width?: number
  intensity?: string
  baseOpacity?: number
}) {
  const dur = `${duration}s`
  // The band runs from off-screen left to off-screen right so it enters and
  // leaves cleanly rather than appearing mid-line.
  const from = -width * 2
  const to = 1 + width * 2
  const seq = (shift: number) => `${from + shift};${to + shift}`

  // The resting stroke is held below full strength so the travelling band is
  // the brightest thing on the line. Painting the band over an already-bright
  // stroke gives almost no contrast — the trace has to be dim for the pulse to
  // read as a pulse. It stays well above the legibility floor at rest.
  return (
    <linearGradient id={id} x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stopColor={color} stopOpacity={baseOpacity} />
      <stop offset="0" stopColor={color} stopOpacity={baseOpacity}>
        <animate attributeName="offset" values={seq(0)} dur={dur} repeatCount="indefinite" />
      </stop>
      <stop offset="0" stopColor={intensity} stopOpacity="1">
        <animate attributeName="offset" values={seq(width / 2)} dur={dur} repeatCount="indefinite" />
      </stop>
      <stop offset="0" stopColor={color} stopOpacity={baseOpacity}>
        <animate attributeName="offset" values={seq(width)} dur={dur} repeatCount="indefinite" />
      </stop>
      <stop offset="1" stopColor={color} stopOpacity={baseOpacity} />
    </linearGradient>
  )
}

/**
 * The line pulse, adapted to bars.
 *
 * A horizontal bar is a line with thickness, so the same travelling band works
 * — a charge running the length of each bar. Two differences from the line
 * version:
 *
 *   - each bar gets its own gradient with a staggered start, so the row reads
 *     as a bank of instruments rather than everything strobing in unison.
 *   - the band runs the direction the bar grows, so it reinforces the
 *     magnitude reading instead of cutting across it.
 *
 * The bar's length is untouched: only fill colour moves, so the value a bar
 * states is exactly the value it states when static.
 */
export function BarPulseGradients({
  colors,
  idPrefix = 'f-bar-pulse',
  duration = 4.5,
  width = 0.22,
  baseOpacity = 0.62,
  stagger = 0.22,
  intensity = '#ffffff',
}: {
  colors: string[]
  idPrefix?: string
  duration?: number
  width?: number
  baseOpacity?: number
  stagger?: number
  intensity?: string
}) {
  const from = -width * 2
  const to = 1 + width * 2
  return (
    <>
      {colors.map((color, i) => {
        const seq = (shift: number) => `${from + shift};${to + shift}`
        const begin = `${(i * stagger).toFixed(2)}s`
        return (
          <linearGradient key={i} id={`${idPrefix}-${i}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor={color} stopOpacity={baseOpacity} />
            <stop offset="0" stopColor={color} stopOpacity={baseOpacity}>
              <animate attributeName="offset" values={seq(0)} dur={`${duration}s`}
                       begin={begin} repeatCount="indefinite" />
            </stop>
            <stop offset="0" stopColor={intensity} stopOpacity="0.9">
              <animate attributeName="offset" values={seq(width / 2)} dur={`${duration}s`}
                       begin={begin} repeatCount="indefinite" />
            </stop>
            <stop offset="0" stopColor={color} stopOpacity={baseOpacity}>
              <animate attributeName="offset" values={seq(width)} dur={`${duration}s`}
                       begin={begin} repeatCount="indefinite" />
            </stop>
            <stop offset="1" stopColor={color} stopOpacity={baseOpacity} />
          </linearGradient>
        )
      })}
    </>
  )
}
