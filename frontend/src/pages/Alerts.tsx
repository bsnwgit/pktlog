import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, AlertRule, AlertEvent, getToken } from '../api/client'
import { useWebSocket, type WsMessage, type AlertFiredPayload } from '../hooks/useWebSocket'
import { useTimezone } from '../hooks/useTimezone'
import HelpButton from '../components/HelpButton'
import IpLink, { linkifyIps } from '../components/IpLink'

// ── Time range ────────────────────────────────────────────────────────────────

const TIME_RANGES = [
  { value: '1h',     label: '1h' },
  { value: '6h',     label: '6h' },
  { value: '24h',    label: '24h' },
  { value: '7d',     label: '7d' },
  { value: '30d',    label: '30d' },
  { value: 'all',    label: 'All time' },
  { value: 'custom', label: 'Custom range…' },
] as const
type TimeRange = typeof TIME_RANGES[number]['value']

const TIME_RANGE_MS: Record<Exclude<TimeRange, 'all' | 'custom'>, number> = {
  '1h':  60 * 60 * 1000,
  '6h':  6 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '7d':  7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
}

interface TimeWindow {
  since?: string
  until?: string
}

// datetime-local values are local wall-clock time with no timezone info, so
// format from local (not UTC) date components — a plain toISOString() would
// shift the displayed clock time by the browser's UTC offset.
function toLocalInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function todayStart(): string {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return toLocalInputValue(d)
}
function todayEnd(): string {
  const d = new Date()
  d.setHours(23, 59, 0, 0)
  return toLocalInputValue(d)
}
/** Never allow a future moment — clamp back to right now instead. */
function clampFuture(value: string): string {
  if (!value) return value
  const now = new Date()
  return new Date(value).getTime() > now.getTime() ? toLocalInputValue(now) : value
}

/**
 * Preset + custom date/time range picker. Owns its own preset/from/to UI state
 * and reports the resolved {since, until} ISO bounds up to the parent.
 *
 * Validation: neither side can be in the future (clamped back to now), and
 * "to" must be after "from" — an invalid range shows an inline error and does
 * not get applied, leaving the last valid window.
 */
function TimeRangeControl({ onChange }: { onChange: (window: TimeWindow) => void }) {
  const [preset, setPreset]         = useState<TimeRange>('all')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo, setCustomTo]     = useState('')
  const [rangeError, setRangeError] = useState('')

  const emit = (p: TimeRange, from: string, to: string) => {
    if (p === 'custom') {
      onChange({
        since: from ? new Date(from).toISOString() : undefined,
        until: to ? new Date(to).toISOString() : undefined,
      })
    } else if (p === 'all') {
      onChange({})
    } else {
      onChange({ since: new Date(Date.now() - TIME_RANGE_MS[p]).toISOString() })
    }
  }

  const applyCustom = (from: string, to: string) => {
    if (from && to && new Date(to).getTime() < new Date(from).getTime()) {
      setRangeError('End date/time must be after the start date/time.')
      return
    }
    setRangeError('')
    emit('custom', from, to)
  }

  const nowLocal = toLocalInputValue(new Date())

  return (
    <div className="flex items-center gap-1.5">
      <select
        value={preset}
        onChange={e => {
          const p = e.target.value as TimeRange
          setPreset(p)
          setRangeError('')
          if (p === 'custom') {
            // Default to today's full day (12:00 AM – 11:59 PM), clamped to
            // "now" since the end can't be in the future.
            const from = customFrom || todayStart()
            const to   = clampFuture(customTo || todayEnd())
            setCustomFrom(from)
            setCustomTo(to)
            applyCustom(from, to)
          } else {
            emit(p, customFrom, customTo)
          }
        }}
        className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
      >
        {TIME_RANGES.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
      </select>
      {preset === 'custom' && (
        <>
          <input
            type="datetime-local"
            value={customFrom}
            max={nowLocal}
            onChange={e => {
              const v = clampFuture(e.target.value)
              setCustomFrom(v)
              applyCustom(v, customTo)
            }}
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <span className="text-xs text-gray-500">to</span>
          <input
            type="datetime-local"
            value={customTo}
            max={nowLocal}
            onChange={e => {
              const v = clampFuture(e.target.value)
              setCustomTo(v)
              applyCustom(customFrom, v)
            }}
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          {rangeError && <span className="text-xs text-red-400">{rangeError}</span>}
        </>
      )}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(ts: string, timeZone: string): string {
  // fired_at/acked_at/resolved_at are stored as naive UTC (SQLite's
  // datetime('now'), no 'Z'/offset) — without forcing UTC interpretation here,
  // the browser parses it against its own system timezone (not the app's
  // configured display timezone), then the `timeZone` option below re-renders
  // that wrong instant, compounding the error rather than fixing it.
  const utc = ts.includes('T') || ts.endsWith('Z') ? ts : ts.replace(' ', 'T') + 'Z'
  return new Date(utc).toLocaleString([], {
    timeZone,
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

const SEV_STYLES: Record<string, string> = {
  critical: 'bg-red-500/20 text-red-400 border border-red-500/40',
  warning:  'bg-yellow-500/20 text-yellow-400 border border-yellow-500/40',
  info:     'bg-blue-500/20 text-blue-400 border border-blue-500/40',
}

const TOPIC_STYLES: Record<string, string> = {
  Volume:         'bg-sky-500/15 text-sky-400 border border-sky-500/30',
  Infrastructure: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30',
}

const CHANNELS_AVAILABLE = ['inapp', 'slack', 'email', 'pagerduty', 'webhook', 'tracecat']

const RULE_TYPES: Array<{ value: string; label: string; group: string; hint: string }> = [
  // Event volume
  { value: 'threshold',       label: 'Threshold',            group: 'Volume',         hint: 'Fire when syslog event volume exceeds a fixed value' },
  { value: 'rate_spike',      label: 'Rate spike',           group: 'Volume',         hint: 'Fire when event volume spikes relative to a rolling baseline' },
  { value: 'top_talker',      label: 'Top talker',           group: 'Volume',         hint: 'Fire when a single source IP dominates event volume in the window' },
  // Infrastructure
  { value: 'data_gap',        label: 'Data gap',             group: 'Infrastructure', hint: 'Fire when a known collector stops sending syslog data' },
  { value: 'new_host',        label: 'New host',             group: 'Infrastructure', hint: 'Fire when an unrecognized collector sends syslog data' },
  { value: 'ingest_rate_low', label: 'Ingest rate low',      group: 'Infrastructure', hint: 'Fire when the overall ingest rate drops below a minimum' },
  { value: 'clickhouse_size', label: 'ClickHouse table size', group: 'Infrastructure', hint: 'Fire when a ClickHouse table exceeds a storage threshold' },
]

function ruleGroup(rule_type: string): string {
  return RULE_TYPES.find(r => r.value === rule_type)?.group ?? 'Other'
}

const RULE_DEFAULTS: Record<string, { name: string; description: string }> = {
  threshold:          { name: 'Event volume threshold',      description: 'Alert when syslog event volume exceeds a set threshold' },
  rate_spike:         { name: 'Event rate spike',            description: 'Alert when event volume spikes above the rolling baseline by a multiplier' },
  top_talker:         { name: 'Top talker threshold',        description: 'Alert when a single source IP dominates event volume in the eval window' },
  data_gap:           { name: 'Collector data gap',          description: 'Alert when a known collector stops sending syslog data for a set period' },
  new_host:           { name: 'Unknown collector detected',  description: 'Alert when an unrecognized collector sends syslog data' },
  ingest_rate_low:    { name: 'Ingest rate too low',         description: 'Alert when the overall syslog ingest rate drops below a minimum' },
  clickhouse_size:    { name: 'ClickHouse table too large',  description: 'Alert when a ClickHouse table exceeds a storage size threshold' },
}

// ── Conditions builder ────────────────────────────────────────────────────────

type Cond = Record<string, unknown>

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-gray-400 mb-1">{label}</label>
      {children}
      {hint && <p className="text-xs text-gray-600 mt-0.5">{hint}</p>}
    </div>
  )
}

function TextInput({ value, onChange, placeholder, type = 'text' }: {
  value: string; onChange: (v: string) => void; placeholder?: string; type?: string
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
    />
  )
}

function SelectInput({ value, onChange, options }: {
  value: string; onChange: (v: string) => void
  options: Array<{ value: string; label: string }>
}) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
    >
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

const OPERATOR_OPTS = [
  { value: 'gt',  label: '> greater than' },
  { value: 'gte', label: '≥ at least' },
  { value: 'lt',  label: '< less than' },
  { value: 'lte', label: '≤ at most' },
]

// Matches SyslogExplorer.tsx's SEV_OPTIONS for consistency
const SEVERITY_MAX_OPTS = [
  { value: '',  label: 'All severities' },
  { value: '0', label: '≤ Emergency (0)' },
  { value: '1', label: '≤ Alert (1)' },
  { value: '2', label: '≤ Critical (2)' },
  { value: '3', label: '≤ Error (3)' },
  { value: '4', label: '≤ Warning (4)' },
  { value: '5', label: '≤ Notice (5)' },
  { value: '6', label: '≤ Info (6)' },
  { value: '7', label: 'All (≤ Debug)' },
]

function ConditionsBuilder({ ruleType, conds, onChange }: {
  ruleType: string
  conds: Cond
  onChange: (c: Cond) => void
}) {
  const set = (key: string, val: unknown) => onChange({ ...conds, [key]: val })
  const g = (key: string, def: unknown) => (conds[key] !== undefined ? conds[key] : def)

  if (ruleType === 'data_gap') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Silence threshold (minutes)" hint="Alert if a collector sends no syslog data for this long">
          <TextInput type="number" value={String(g('silence_minutes', 10))} onChange={v => set('silence_minutes', parseInt(v) || 10)} />
        </Field>
      </div>
    )
  }

  if (ruleType === 'new_host') {
    return (
      <p className="text-xs text-gray-500 italic">No conditions — fires whenever an unrecognized collector sends syslog data.</p>
    )
  }

  if (ruleType === 'threshold') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Operator">
          <SelectInput value={String(g('operator', 'gt'))} onChange={v => set('operator', v)} options={OPERATOR_OPTS} />
        </Field>
        <Field label="Threshold value" hint="Number of syslog events in the eval window">
          <TextInput type="number" value={String(g('value', 0))} onChange={v => set('value', parseFloat(v) || 0)} />
        </Field>
        <Field label="Minimum severity (optional)" hint="Only count events at least this severe">
          <SelectInput value={String(g('severity_max', ''))} onChange={v => set('severity_max', v === '' ? '' : parseInt(v))} options={SEVERITY_MAX_OPTS} />
        </Field>
        <Field label="Program (optional)" hint="Only count events from this program">
          <TextInput value={String(g('program', ''))} onChange={v => set('program', v)} placeholder="e.g. sshd" />
        </Field>
        <Field label="Collector IP (optional)" hint="Leave blank to check all collectors">
          <TextInput value={String(g('collector_ip', ''))} onChange={v => set('collector_ip', v)} placeholder="e.g. 192.0.2.10" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'rate_spike') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Spike multiplier" hint="Alert when current rate is this many times the baseline">
          <TextInput type="number" value={String(g('multiplier', 3))} onChange={v => set('multiplier', parseFloat(v) || 3)} />
        </Field>
        <Field label="Baseline period (days)" hint="Rolling average over this many days">
          <TextInput type="number" value={String(g('baseline_days', 7))} onChange={v => set('baseline_days', parseInt(v) || 7)} />
        </Field>
        <Field label="Minimum severity (optional)" hint="Only count events at least this severe">
          <SelectInput value={String(g('severity_max', ''))} onChange={v => set('severity_max', v === '' ? '' : parseInt(v))} options={SEVERITY_MAX_OPTS} />
        </Field>
        <Field label="Program (optional)" hint="Only count events from this program">
          <TextInput value={String(g('program', ''))} onChange={v => set('program', v)} placeholder="e.g. sshd" />
        </Field>
        <Field label="Collector IP (optional)">
          <TextInput value={String(g('collector_ip', ''))} onChange={v => set('collector_ip', v)} placeholder="e.g. 192.0.2.10" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'top_talker') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Threshold" hint="Alert when any single source IP sends more than this many events in the eval window">
          <TextInput type="number" value={String(g('threshold', 0))} onChange={v => set('threshold', parseFloat(v) || 0)} />
        </Field>
        <Field label="Minimum severity (optional)" hint="Only count events at least this severe">
          <SelectInput value={String(g('severity_max', ''))} onChange={v => set('severity_max', v === '' ? '' : parseInt(v))} options={SEVERITY_MAX_OPTS} />
        </Field>
        <Field label="Collector IP (optional)">
          <TextInput value={String(g('collector_ip', ''))} onChange={v => set('collector_ip', v)} placeholder="e.g. 192.0.2.10" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'ingest_rate_low') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Minimum events/sec" hint="Alert when ingest rate falls below this threshold">
          <TextInput type="number" value={String(g('min_events_per_sec', 1))} onChange={v => set('min_events_per_sec', parseFloat(v) || 1)} placeholder="e.g. 1" />
        </Field>
        <Field label="Collector IP (optional)" hint="Leave blank to check total ingest across all collectors">
          <TextInput value={String(g('collector_ip', ''))} onChange={v => set('collector_ip', v)} placeholder="e.g. 192.0.2.10" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'clickhouse_size') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Table size threshold (GB)" hint="Alert when the table's compressed size exceeds this">
          <TextInput type="number" value={String(g('threshold_gb', 10))} onChange={v => set('threshold_gb', parseFloat(v) || 10)} placeholder="e.g. 50" />
        </Field>
        <Field label="Table name" hint="ClickHouse table to monitor">
          <TextInput value={String(g('table', 'syslog_events'))} onChange={v => set('table', v)} placeholder="syslog_events" />
        </Field>
      </div>
    )
  }

  return null
}

// ── Alert details panel ───────────────────────────────────────────────────────
type DetailMap = Record<string, unknown>

function fmtVal(v: unknown): string {
  if (typeof v === 'number') return v > 999 ? v.toLocaleString() : String(v)
  return String(v)
}

type TopSource = { source_ip: string; count: number }

function MiniTable({ title, headers, rows }: {
  title: string
  headers: string[]
  rows: (string | number | React.ReactNode)[][]
}) {
  if (!rows.length) return null
  return (
    <div>
      <p className="text-xs text-gray-500 mb-1.5 uppercase tracking-wide">{title}</p>
      <div className="f-tbl-scroll">
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="text-gray-500">
              {headers.map((h, i) => (
                <th key={i} className={`pb-1 font-normal ${i === headers.length - 1 ? 'text-right' : 'text-left'}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri} className="border-t border-gray-800/70">
                {row.map((cell, ci) => (
                  <td key={ci} className={`py-1 ${ci === row.length - 1 ? 'text-right text-gray-200' : 'text-gray-200 font-mono'}`}>
                    {typeof cell === 'string' || typeof cell === 'number' ? (typeof cell === 'string' ? cell : cell.toLocaleString()) : cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function DetailsPanel({ details }: { details: DetailMap }) {
  const topSources = details.top_sources as TopSource[] | undefined

  const TABLE_KEYS = new Set(['top_sources'])
  const CHIP_IP_KEYS = ['src_ip', 'sampler_ip']
  const META_SKIP = new Set([...CHIP_IP_KEYS, ...TABLE_KEYS])

  const chips: [string, string, string][] = []
  CHIP_IP_KEYS.forEach(k => {
    const v = details[k] as string | undefined
    if (v && v !== 'all') chips.push([k, k === 'src_ip' ? 'Source IP' : 'Collector', v])
  })

  const kvs = Object.entries(details).filter(([k]) => !META_SKIP.has(k))

  const topSourceRows = (topSources || []).map(s => [<IpLink ip={s.source_ip} />, s.count])

  return (
    <div className="mt-3 space-y-3">
      {/* Chips */}
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {chips.map(([k, label, v]) => (
            <span key={k} className="text-xs bg-blue-500/15 text-blue-300 border border-blue-500/25 px-2.5 py-0.5 rounded-full">
              <span className="text-blue-500/70 mr-1">{label}</span>
              <IpLink ip={v} className="text-blue-300" />
            </span>
          ))}
        </div>
      )}

      {/* Key metrics */}
      {kvs.length > 0 && (
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          {kvs.map(([k, v]) => (
            <span key={k} className="text-xs">
              <span className="text-gray-500">{k.replace(/_/g,' ')}: </span>
              <span className="text-gray-200 font-medium">{fmtVal(v)}</span>
            </span>
          ))}
        </div>
      )}

      {/* Top sources (threshold / rate_spike) */}
      <MiniTable title="Top sources" headers={['Source IP', 'Events']} rows={topSourceRows} />
    </div>
  )
}

// ── Investigate URL builder ───────────────────────────────────────────────────

function buildInvestigateUrl(event: AlertEvent): string {
  const d = event.details as Record<string, any>
  const windowMin: number = typeof d._time_window_min === 'number' ? d._time_window_min : 30

  // fired_at is naive UTC (SQLite datetime('now'), no 'Z'/offset) — normalize
  // before parsing so the window isn't shifted by the browser's local zone.
  const firedAtUtc = event.fired_at.replace(' ', 'T') +
    (event.fired_at.endsWith('Z') || /[+\-]\d{2}:?\d{2}$/.test(event.fired_at) ? '' : 'Z')
  const firedAt  = new Date(firedAtUtc)
  const timeFrom = new Date(firedAt.getTime() - windowMin * 60 * 1000)
  const timeTo   = new Date(firedAt.getTime() + 15 * 60 * 1000)

  const p = new URLSearchParams()
  p.set('time_from', timeFrom.toISOString())
  p.set('time_to',   timeTo.toISOString())

  // sampler_ip is this app's alert-engine field name for the collector IP —
  // skip when "all" (rule wasn't scoped to a single collector)
  if (d.sampler_ip && d.sampler_ip !== 'all') p.set('collector_ip', String(d.sampler_ip))

  return `/explorer?${p.toString()}`
}

// ── Alert event card ──────────────────────────────────────────────────────────
function EventCard({ event, onAck, timezone }: { event: AlertEvent; onAck: (id: number) => void; timezone: string }) {
  const navigate = useNavigate()
  const [expanded, setExpanded] = useState(false)
  const isAcked     = Boolean(event.acked_at)
  const isResolved  = Boolean(event.resolved_at) && !isAcked
  const hasDetails  = Object.keys(event.details).length > 0

  // "Unknown collector <ip> sent syslog data — not in collector registry"
  // (see app/alerts/engine.py _check_unknown_samplers) — offer a direct
  // link to register it, pre-filled with just the IP. The user still fills
  // in and saves the rest themselves.
  const unknownIp = event.message.startsWith('Unknown collector ')
    && typeof event.details.sampler_ip === 'string'
    ? event.details.sampler_ip as string
    : null

  return (
    <div className={`bg-gray-900 border rounded-xl p-4 transition-opacity ${
      isAcked ? 'opacity-40 border-gray-800' : isResolved ? 'opacity-70 border-gray-700' : 'border-gray-700'
    }`}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3 min-w-0">
          <span className={`shrink-0 text-xs px-2 py-0.5 rounded-full font-medium capitalize ${SEV_STYLES[event.severity] ?? SEV_STYLES.info}`}>
            {event.severity}
          </span>
          {isResolved && (
            <span className="shrink-0 text-xs px-2 py-0.5 rounded-full font-medium bg-green-500/20 text-green-400 border border-green-500/40">
              auto-resolved
            </span>
          )}
          <div className="min-w-0">
            <p className="text-sm font-medium text-white truncate">{event.rule_name}</p>
            <p className="text-sm text-white mt-0.5">{linkifyIps(event.message)}</p>
            {isResolved && (
              <p className="text-xs text-green-500/70 mt-0.5">Resolved {fmtTime(event.resolved_at!, timezone)}</p>
            )}
            {unknownIp && (
              <Link
                to="/approval"
                className="inline-block mt-1 text-xs text-blue-400 hover:text-blue-300 underline"
              >
                Approve collector {unknownIp} →
              </Link>
            )}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          <span className="text-xs text-white">{fmtTime(event.fired_at, timezone)}</span>
          <button
            onClick={() => navigate(buildInvestigateUrl(event))}
            className="text-xs bg-gray-800 hover:bg-gray-700 text-blue-400 hover:text-blue-300 border border-blue-500/40 rounded px-2.5 py-1 transition-colors"
            title="Open Syslog Explorer for this alert's time window"
          >
            Investigate ↗
          </button>
          {!isAcked && (
            <button
              onClick={() => onAck(event.id)}
              className="text-xs bg-gray-800 hover:bg-gray-700 text-white hover:text-white border border-gray-700 rounded px-2.5 py-1 transition-colors"
            >
              Ack
            </button>
          )}
          {isAcked && <span className="text-xs text-green-500">✓ Acked</span>}
        </div>
      </div>

      {hasDetails && (
        <div className="mt-2">
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            {expanded ? '▾ Hide details' : '▸ Show details'}
          </button>
          {expanded && (
            <div className="mt-1 bg-gray-800/50 border border-gray-700/50 rounded-lg px-3 py-2">
              <DetailsPanel details={event.details as DetailMap} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Custom rule type selector ─────────────────────────────────────────────────
function RuleTypeSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const selected = RULE_TYPES.find(r => r.value === value)
  const groups = RULE_TYPES.reduce<Record<string, typeof RULE_TYPES>>((acc, rt) => {
    ;(acc[rt.group] = acc[rt.group] || []).push(rt)
    return acc
  }, {})

  const TOPIC_BADGE: Record<string, string> = {
    Traffic:        'text-sky-400',
    Security:       'text-orange-400',
    Infrastructure: 'text-emerald-400',
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white text-left flex items-center justify-between focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <span>{selected?.label ?? 'Select type…'}</span>
        <span className="text-gray-500 ml-2">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full bg-gray-800 border border-gray-700 rounded-lg shadow-lg overflow-hidden max-h-80 overflow-y-auto">
          {Object.entries(groups).map(([grp, types]) => (
            <div key={grp}>
              <p className={`px-3 pt-2.5 pb-1 text-xs font-semibold uppercase tracking-wider ${TOPIC_BADGE[grp] ?? 'text-gray-400'}`}>
                {grp}
              </p>
              {types.map(rt => (
                <button
                  key={rt.value}
                  type="button"
                  onClick={() => { onChange(rt.value); setOpen(false) }}
                  className={`w-full text-left px-3 py-2 hover:bg-gray-700 transition-colors ${rt.value === value ? 'bg-gray-700/60' : ''}`}
                >
                  <p className="text-sm text-white">{rt.label}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{rt.hint}</p>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Rule form ─────────────────────────────────────────────────────────────────
interface RuleFormData {
  name: string; description: string; enabled: boolean; rule_type: string
  severity: string; channels: string[]; cooldown_min: string; time_window_min: string
  conditions: Cond
}

const EMPTY_RULE: RuleFormData = {
  name:        RULE_DEFAULTS['data_gap']?.name ?? '',
  description: RULE_DEFAULTS['data_gap']?.description ?? '',
  enabled: true, rule_type: 'data_gap',
  severity: 'warning', channels: ['inapp'], cooldown_min: '30', time_window_min: '5',
  conditions: {},
}

function fromRule(r: AlertRule): RuleFormData {
  return {
    name: r.name, description: r.description, enabled: r.enabled,
    rule_type: r.rule_type, severity: r.severity,
    channels: r.channels, cooldown_min: String(r.cooldown_min),
    time_window_min: '5',
    conditions: (r.conditions as Cond) ?? {},
  }
}

function RuleForm({
  initial, onSave, onCancel, saving,
}: {
  initial: RuleFormData
  onSave: (data: RuleFormData) => Promise<void>
  onCancel: () => void
  saving: boolean
}) {
  const [form, setForm] = useState<RuleFormData>(initial)
  const set = <K extends keyof RuleFormData>(k: K, v: RuleFormData[K]) =>
    setForm(f => ({ ...f, [k]: v }))

  // Reset conditions and auto-fill name/description when rule type changes
  const setRuleType = (t: string) => setForm(f => {
    const prevDefault = RULE_DEFAULTS[f.rule_type]
    const newDefault  = RULE_DEFAULTS[t] ?? { name: '', description: '' }
    const nameIsDefault     = !f.name.trim()        || f.name        === prevDefault?.name
    const descIsDefault     = !f.description.trim() || f.description === prevDefault?.description
    return {
      ...f,
      rule_type:   t,
      conditions:  {},
      name:        nameIsDefault ? newDefault.name : f.name,
      description: descIsDefault ? newDefault.description : f.description,
    }
  })

  const toggleChannel = (ch: string) => {
    set('channels', form.channels.includes(ch)
      ? form.channels.filter(c => c !== ch)
      : [...form.channels, ch])
  }

  const hasConditions = !['new_host'].includes(form.rule_type)

  return (
    <div className="bg-gray-900 border border-blue-500/30 rounded-xl p-5 space-y-5">
      <h3 className="text-sm font-semibold text-white">{initial.name ? 'Edit rule' : 'New alert rule'}</h3>

      {/* Base fields */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="sm:col-span-2">
          <label className="block text-xs text-gray-400 mb-1">Rule type</label>
          <RuleTypeSelect value={form.rule_type} onChange={setRuleType} />
        </div>
        <div className="sm:col-span-2">
          <label className="block text-xs text-gray-400 mb-1">Rule name</label>
          <input
            value={form.name}
            onChange={e => set('name', e.target.value)}
            placeholder="My alert rule"
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div className="sm:col-span-2">
          <label className="block text-xs text-gray-400 mb-1">Description (optional)</label>
          <input
            value={form.description}
            onChange={e => set('description', e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Severity</label>
          <select
            value={form.severity}
            onChange={e => set('severity', e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="critical">Critical</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Cooldown (minutes)</label>
          <input
            type="number" min="1" max="1440"
            value={form.cooldown_min}
            onChange={e => set('cooldown_min', e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Eval window (minutes)</label>
          <input
            type="number" min="1" max="1440"
            value={form.time_window_min}
            onChange={e => set('time_window_min', e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>

      {/* Conditions */}
      {hasConditions && (
        <div>
          <p className="text-xs font-medium text-gray-400 mb-3 uppercase tracking-wide">Conditions</p>
          <div className="bg-gray-800/50 border border-gray-700/60 rounded-lg p-4">
            <ConditionsBuilder
              ruleType={form.rule_type}
              conds={form.conditions}
              onChange={c => set('conditions', c)}
            />
          </div>
        </div>
      )}

      {/* Channels */}
      <div>
        <label className="block text-xs text-gray-400 mb-2">Notification channels</label>
        <div className="flex flex-wrap gap-2">
          {CHANNELS_AVAILABLE.map(ch => {
            const active = form.channels.includes(ch)
            return (
              <button
                key={ch}
                type="button"
                onClick={() => toggleChannel(ch)}
                className={`text-xs px-3 py-1.5 rounded-lg border transition-colors capitalize ${
                  active
                    ? 'bg-blue-600/30 border-blue-500 text-blue-300'
                    : 'bg-gray-800 border-gray-700 text-white hover:border-gray-500'
                }`}
              >
                {ch}
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex items-center gap-3 pt-1">
        <button
          onClick={() => onSave(form)}
          disabled={saving || !form.name.trim()}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg px-5 py-2 transition-colors"
        >
          {saving ? 'Saving…' : 'Save rule'}
        </button>
        <button
          onClick={onCancel}
          className="text-white hover:text-white text-sm border border-gray-700 rounded-lg px-4 py-2 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── Pagination ────────────────────────────────────────────────────────────────

const PAGE_SIZE_OPTIONS = [25, 50, 75, 100]

/**
 * Page-number bar: shows every page when there are 5 or fewer, otherwise
 * pages 1-5, an ellipsis, then the last page — plus prev/next buttons.
 */
function Pagination({ page, totalPages, onChange }: { page: number; totalPages: number; onChange: (p: number) => void }) {
  if (totalPages <= 1) return null
  // The visible block of 5 slides with the current page — e.g. Next from
  // page 5 moves to page 6 and the block updates to show 6-10, not a fixed
  // 1-5. Same in reverse for Prev.
  const blockStart = Math.floor((page - 1) / 5) * 5 + 1
  const blockEnd   = Math.min(blockStart + 4, totalPages)
  const pages = Array.from({ length: blockEnd - blockStart + 1 }, (_, i) => blockStart + i)
  const btn = (p: number) => [
    'text-xs min-w-[1.75rem] px-2 py-1 rounded-lg border transition-colors',
    p === page
      ? 'bg-blue-600/30 border-blue-500 text-blue-200'
      : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white',
  ].join(' ')
  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={() => onChange(Math.max(1, page - 1))}
        disabled={page === 1}
        className="text-xs px-2.5 py-1 rounded-lg border border-gray-700 bg-gray-800 text-gray-300 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        ← Prev
      </button>
      {blockStart > 1 && (
        <>
          <button onClick={() => onChange(1)} className={btn(1)}>1</button>
          <span className="px-1 text-gray-500 text-xs">..</span>
        </>
      )}
      {pages.map(p => <button key={p} onClick={() => onChange(p)} className={btn(p)}>{p}</button>)}
      {blockEnd < totalPages && (
        <>
          <span className="px-1 text-gray-500 text-xs">..</span>
          <button onClick={() => onChange(totalPages)} className={btn(totalPages)}>{totalPages}</button>
        </>
      )}
      <button
        onClick={() => onChange(Math.min(totalPages, page + 1))}
        disabled={page === totalPages}
        className="text-xs px-2.5 py-1 rounded-lg border border-gray-700 bg-gray-800 text-gray-300 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        Next →
      </button>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
type Tab = 'active' | 'history' | 'rules'

export default function Alerts() {
  const timezone = useTimezone()
  const [tab, setTab]               = useState<Tab>('active')
  const [events, setEvents]         = useState<AlertEvent[]>([])
  const [history, setHistory]       = useState<AlertEvent[]>([])
  const [rules, setRules]           = useState<AlertRule[]>([])
  const [loading, setLoading]       = useState(false)
  const [editRule, setEditRule]     = useState<AlertRule | null>(null)
  const [addingRule, setAddingRule] = useState(false)
  const [saving, setSaving]         = useState(false)
  const [error, setError]           = useState('')
  const [activeFilter, setActiveFilter]     = useState('')
  const [activeSevFilter, setActiveSevFilter] = useState('')
  const [activeWindow, setActiveWindow]     = useState<TimeWindow>({})
  const [historyFilter, setHistoryFilter]   = useState('')
  const [historySevFilter, setHistorySevFilter] = useState('')
  const [historyWindow, setHistoryWindow]   = useState<TimeWindow>({})
  const [activePage, setActivePage]         = useState(1)
  const [historyPage, setHistoryPage]       = useState(1)
  const [activePageSize, setActivePageSize]   = useState(25)
  const [historyPageSize, setHistoryPageSize] = useState(25)
  const [rulesFilter, setRulesFilter]       = useState('')
  const [rulesTopicFilter, setRulesTopicFilter] = useState('')
  const [rulesSortKey, setRulesSortKey]     = useState<keyof AlertRule | 'topic' | null>(null)
  const [rulesSortDir, setRulesSortDir]     = useState<'asc' | 'desc'>('asc')
  const [toasts, setToasts]             = useState<AlertFiredPayload[]>([])
  const [rulesExporting, setRulesExporting]     = useState(false)
  const [rulesImportResult, setRulesImportResult] = useState<{ created: number; skipped: number; errors: string[] } | null>(null)
  const rulesImportFileRef = useRef<HTMLInputElement>(null)

  const loadEvents = useCallback(async () => {
    setLoading(true)
    try {
      const [active, all] = await Promise.all([
        api.getAlertEvents(true, activeWindow.since, activeWindow.until),
        api.getAlertEvents(false, historyWindow.since, historyWindow.until),
      ])
      setEvents(active)
      setHistory(all.filter(e => e.acked_at !== null))
    } finally {
      setLoading(false)
    }
  }, [activeWindow, historyWindow])

  const loadRules = async () => {
    const data = await api.getAlertRules()
    setRules(data)
  }

  // Live alert toast — fires whenever the engine triggers an alert
  const handleWsMessage = useCallback((msg: WsMessage) => {
    if (msg.type === 'alert_fired') {
      setToasts(prev => [msg.data, ...prev].slice(0, 5))
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.event_id !== msg.data.event_id))
      }, 8000)
      loadEvents()
    }
  }, [loadEvents])

  useWebSocket(handleWsMessage)

  const toggleRulesSort = (key: keyof AlertRule | 'topic') => {
    if (rulesSortKey === key) setRulesSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setRulesSortKey(key); setRulesSortDir('asc') }
  }

  useEffect(() => {
    loadEvents()
    loadRules()
  }, [loadEvents])

  const handleAck = async (id: number) => {
    await api.ackEvent(id)
    await loadEvents()
  }

  const handleAckAll = async () => {
    await api.ackAllEvents()
    await loadEvents()
  }

  const changeActivePageSize = (size: number) => { setActivePageSize(size); setActivePage(1) }
  const changeHistoryPageSize = (size: number) => { setHistoryPageSize(size); setHistoryPage(1) }

  const filteredEvents = useMemo(() => events.filter(e => {
    if (activeSevFilter && e.severity !== activeSevFilter) return false
    if (!activeFilter) return true
    const q = activeFilter.toLowerCase()
    return e.rule_name.toLowerCase().includes(q) || e.message.toLowerCase().includes(q)
  }), [events, activeSevFilter, activeFilter])
  const activeTotalPages = Math.max(1, Math.ceil(filteredEvents.length / activePageSize))
  const activePageClamped = Math.min(activePage, activeTotalPages)
  const pagedEvents = filteredEvents.slice((activePageClamped - 1) * activePageSize, activePageClamped * activePageSize)

  const filteredHistory = useMemo(() => history.filter(e => {
    if (historySevFilter && e.severity !== historySevFilter) return false
    if (!historyFilter) return true
    const q = historyFilter.toLowerCase()
    return e.rule_name.toLowerCase().includes(q) || e.message.toLowerCase().includes(q)
  }), [history, historySevFilter, historyFilter])
  const historyTotalPages = Math.max(1, Math.ceil(filteredHistory.length / historyPageSize))
  const historyPageClamped = Math.min(historyPage, historyTotalPages)
  const pagedHistory = filteredHistory.slice((historyPageClamped - 1) * historyPageSize, historyPageClamped * historyPageSize)

  const authHeaders = () => ({
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${getToken() ?? ''}`,
  })

  const handleToggle = async (rule: AlertRule) => {
    setRules(rs => rs.map(r => r.id === rule.id ? { ...r, enabled: !r.enabled } : r))
    try {
      const res = await fetch(`/api/alerts/rules/${rule.id}/toggle`, {
        method: 'PATCH',
        headers: authHeaders(),
      })
      if (!res.ok) throw new Error()
    } catch {
      setRules(rs => rs.map(r => r.id === rule.id ? { ...r, enabled: rule.enabled } : r))
    }
  }

  const handleDeleteRule = async (id: number) => {
    if (!confirm('Delete this alert rule? All associated alert events will also be deleted.')) return
    const res = await fetch(`/api/alerts/rules/${id}`, { method: 'DELETE', headers: authHeaders() })
    if (!res.ok) {
      const msg = await res.text().catch(() => res.statusText)
      setError(`Failed to delete rule: ${msg}`)
      return
    }
    await loadRules()
  }

  const handleDownloadRulesTemplate = () => {
    const rows = [
      ['name', 'description', 'rule_type', 'conditions', 'time_window_min', 'severity', 'channels', 'cooldown_min', 'enabled'],
      ['Collector gap — Branch-A', 'Fires when no syslog data arrives', 'collector_gap', '{"collector_ip": "10.0.1.5", "silence_minutes": 15}', '5', 'warning', 'inapp,email', '30', 'true'],
    ]
    const csv = rows.map(r => r.map(v => `"${v.replace(/"/g, '""')}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'pktlog-alert-rules-template.csv'; a.click()
    URL.revokeObjectURL(url)
  }

  const handleExportRules = async () => {
    setRulesExporting(true)
    try {
      const res = await fetch('/api/alerts/rules/export', { headers: authHeaders() })
      if (!res.ok) throw new Error(`Export failed: ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = 'pktlog-alert-rules.csv'; a.click()
      URL.revokeObjectURL(url)
    } catch (e: any) { setError(e.message) }
    finally { setRulesExporting(false) }
  }

  const handleImportRulesFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    try {
      const formData = new FormData()
      formData.append('file', file)
      const headers: Record<string, string> = { 'Authorization': `Bearer ${getToken() ?? ''}` }
      const res = await fetch('/api/alerts/rules/import-csv', { method: 'POST', headers, body: formData })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail || res.statusText)
      }
      const result = await res.json()
      setRulesImportResult(result)
      if (result.created > 0) await loadRules()
    } catch (e: any) { setError(e.message) }
  }

  const handleSaveRule = async (form: RuleFormData) => {
    setSaving(true)
    setError('')
    try {
      const body = {
        name: form.name, description: form.description, enabled: form.enabled,
        rule_type: form.rule_type,
        conditions: form.conditions,
        time_window_min: parseInt(form.time_window_min) || 5,
        severity: form.severity, channels: form.channels,
        cooldown_min: parseInt(form.cooldown_min) || 30,
      }
      let res: Response
      if (editRule) {
        res = await fetch(`/api/alerts/rules/${editRule.id}`, {
          method: 'PUT',
          headers: authHeaders(),
          body: JSON.stringify(body),
        })
        if (!res.ok) throw new Error(`${res.status}`)
        setEditRule(null)
      } else {
        res = await fetch('/api/alerts/rules', {
          method: 'POST',
          headers: authHeaders(),
          body: JSON.stringify(body),
        })
        if (!res.ok) throw new Error(`${res.status}`)
        setAddingRule(false)
      }
      await loadRules()
    } catch (e) {
      setError(`Failed to save rule (${e})`)
    } finally {
      setSaving(false)
    }
  }

  const SEV_TOAST: Record<string, string> = {
    critical: 'bg-red-900/80 border-red-500/60 text-red-200',
    warning:  'bg-yellow-900/80 border-yellow-500/60 text-yellow-200',
    info:     'bg-blue-900/80 border-blue-500/60 text-blue-200',
  }

  return (
    <div className="space-y-4">
      {/* Live alert toasts */}
      {toasts.length > 0 && (
        <div className="flex flex-col gap-2">
          {toasts.map(t => (
            <div
              key={t.event_id}
              className={`flex items-center justify-between gap-4 border rounded-lg px-4 py-3 text-sm ${SEV_TOAST[t.severity] ?? SEV_TOAST.info}`}
            >
              <div className="flex items-center gap-3 min-w-0">
                <span className="w-2 h-2 rounded-full bg-current flex-shrink-0 animate-pulse" />
                <span className="font-medium flex-shrink-0 capitalize">{t.severity}</span>
                <span className="font-semibold flex-shrink-0">{t.rule_name}</span>
                <span className="truncate opacity-80">{linkifyIps(t.message)}</span>
              </div>
              <button
                onClick={() => setToasts(prev => prev.filter(x => x.event_id !== t.event_id))}
                className="flex-shrink-0 opacity-60 hover:opacity-100 text-lg leading-none"
              >×</button>
            </div>
          ))}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-white">Alerts</h1>
            <HelpButton title="Alerts — How It Works">
              <p>Rule types span two groups: <span className="text-gray-300 font-medium">Volume</span> (threshold, rate spike, top talker) and <span className="text-gray-300 font-medium">Infrastructure</span> (data gap, new host, ingest rate low, ClickHouse table size).</p>
              <p><span className="text-gray-300 font-medium">Auto-resolve</span> means an open alert closes itself the next time its rule evaluates and the condition no longer holds — no need to manually clear it.</p>
              <p><span className="text-gray-300 font-medium">Investigate</span> deep-links into Syslog Explorer, pre-filtered to the specific collector/host/time range that triggered the alert.</p>
              <p>Rules can be bulk-provisioned via CSV — Export CSV to snapshot current rules, download the template for the expected columns, then Import CSV to create many at once.</p>
              <p className="text-gray-500">What actually gets notified (Slack/Email/PagerDuty/Webhook/TraceCat) when a rule fires is configured separately in Settings → Notifications.</p>
            </HelpButton>
          </div>
          <p className="text-sm text-white mt-0.5">
            {(() => {
              const active   = events.filter(e => !e.resolved_at).length
              const resolved = events.filter(e => e.resolved_at).length
              if (active > 0)
                return `${active} active alert${active !== 1 ? 's' : ''}${resolved > 0 ? `, ${resolved} auto-resolved` : ''}`
              if (resolved > 0)
                return `${resolved} auto-resolved alert${resolved !== 1 ? 's' : ''} — all conditions cleared`
              return 'No active alerts'
            })()}
          </p>
        </div>
        {tab === 'active' && events.length > 0 && (
          <button
            onClick={handleAckAll}
            className="text-sm border border-gray-700 hover:border-gray-500 text-white hover:text-white rounded-lg px-4 py-2 transition-colors"
          >
            Ack all
          </button>
        )}
        {tab === 'rules' && !addingRule && !editRule && (
          <div className="flex items-center gap-2">
            <button onClick={handleExportRules} disabled={rulesExporting}
              className="px-3 py-2 text-sm bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors disabled:opacity-50">
              {rulesExporting ? 'Exporting…' : '↓ Export CSV'}
            </button>
            <div className="flex items-center gap-1">
              <button onClick={() => rulesImportFileRef.current?.click()}
                className="px-3 py-2 text-sm bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors rounded-r-none border-r border-gray-600">
                ↑ Import CSV
              </button>
              <button onClick={handleDownloadRulesTemplate} title="Download CSV template"
                className="px-2 py-2 text-sm bg-gray-700 hover:bg-gray-600 text-gray-400 hover:text-white rounded-lg transition-colors rounded-l-none"
                aria-label="Download template">
                template
              </button>
            </div>
            <input ref={rulesImportFileRef} type="file" accept=".csv" className="hidden" onChange={handleImportRulesFile} />
            <button
              onClick={() => setAddingRule(true)}
              className="bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg px-4 py-2 transition-colors"
            >
              + New rule
            </button>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1 w-fit">
        {(['active', 'history', 'rules'] as Tab[]).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`text-sm px-4 py-1.5 rounded-lg transition-colors capitalize ${
              tab === t ? 'bg-gray-700 text-white' : 'text-white hover:text-white'
            }`}
          >
            {t}
            {t === 'active' && events.filter(e => !e.resolved_at).length > 0 && (
              <span className="ml-1.5 bg-red-500 text-white text-xs rounded-full px-1.5 py-0.5">
                {events.filter(e => !e.resolved_at).length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Active events */}
      {tab === 'active' && (
        <div className="space-y-3">
          {/* Filter bar */}
          <div className="flex items-center gap-3 flex-wrap">
            <input
              value={activeFilter}
              onChange={e => { setActiveFilter(e.target.value); setActivePage(1) }}
              placeholder="Filter by rule name or message…"
              className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1 text-xs text-white placeholder-gray-600 w-56 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            {activeFilter && <button onClick={() => { setActiveFilter(''); setActivePage(1) }} className="text-xs text-white hover:text-white">✕</button>}
            <select
              value={activeSevFilter}
              onChange={e => { setActiveSevFilter(e.target.value); setActivePage(1) }}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">All severities</option>
              <option value="critical">Critical</option>
              <option value="warning">Warning</option>
              <option value="info">Info</option>
            </select>
            {activeSevFilter && <button onClick={() => { setActiveSevFilter(''); setActivePage(1) }} className="text-xs text-white hover:text-white">✕</button>}
            <TimeRangeControl onChange={w => { setActiveWindow(w); setActivePage(1) }} />
          </div>
          {loading && <p className="text-sm text-white">Loading…</p>}
          {!loading && events.length === 0 && (
            <div className="flex flex-col items-center justify-center h-32 text-white">
              <p className="text-2xl mb-2">✓</p>
              <p className="text-sm">No unacknowledged alerts</p>
            </div>
          )}
          {!loading && events.length > 0 && filteredEvents.length === 0 && (
            <p className="text-sm text-white text-center py-8">No alerts match this filter</p>
          )}
          {filteredEvents.length > 0 && (
            <div className="flex items-center justify-center gap-6">
              <Pagination page={activePageClamped} totalPages={activeTotalPages} onChange={setActivePage} />
              <div className="flex items-center gap-2">
                <label htmlFor="active-alerts-per-page" className="text-xs text-gray-400">Alerts per page:</label>
                <select
                  id="active-alerts-per-page"
                  value={activePageSize}
                  onChange={e => changeActivePageSize(Number(e.target.value))}
                  className="text-sm bg-gray-800 border border-gray-700 text-white rounded-lg px-2 py-1 focus:outline-none focus:border-sky-500"
                >
                  {PAGE_SIZE_OPTIONS.map(size => (
                    <option key={size} value={size}>{size}</option>
                  ))}
                </select>
              </div>
            </div>
          )}
          {pagedEvents.map(e => <EventCard key={e.id} event={e} onAck={handleAck} timezone={timezone} />)}
          {filteredEvents.length > 0 && (
            <p className="text-xs text-gray-500 pt-1">
              Showing {((activePageClamped - 1) * activePageSize + 1).toLocaleString()}–{((activePageClamped - 1) * activePageSize + pagedEvents.length).toLocaleString()} of {filteredEvents.length.toLocaleString()} alerts
            </p>
          )}
        </div>
      )}

      {/* History */}
      {tab === 'history' && (
        <div className="space-y-3">
          {/* Filter bar */}
          <div className="flex items-center gap-3 flex-wrap">
            <input
              value={historyFilter}
              onChange={e => { setHistoryFilter(e.target.value); setHistoryPage(1) }}
              placeholder="Filter by rule name or message…"
              className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1 text-xs text-white placeholder-gray-600 w-56 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            {historyFilter && <button onClick={() => { setHistoryFilter(''); setHistoryPage(1) }} className="text-xs text-white hover:text-white">✕</button>}
            <select
              value={historySevFilter}
              onChange={e => { setHistorySevFilter(e.target.value); setHistoryPage(1) }}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">All severities</option>
              <option value="critical">Critical</option>
              <option value="warning">Warning</option>
              <option value="info">Info</option>
            </select>
            {historySevFilter && <button onClick={() => { setHistorySevFilter(''); setHistoryPage(1) }} className="text-xs text-white hover:text-white">✕</button>}
            <TimeRangeControl onChange={w => { setHistoryWindow(w); setHistoryPage(1) }} />
          </div>
          {history.length === 0 && !loading && (
            <div className="flex flex-col items-center justify-center h-32 text-white">
              <p className="text-sm">No alert history</p>
            </div>
          )}
          {history.length > 0 && filteredHistory.length === 0 && (
            <p className="text-sm text-white text-center py-8">No alerts match this filter</p>
          )}
          {filteredHistory.length > 0 && (
            <div className="flex items-center justify-center gap-6">
              <Pagination page={historyPageClamped} totalPages={historyTotalPages} onChange={setHistoryPage} />
              <div className="flex items-center gap-2">
                <label htmlFor="history-alerts-per-page" className="text-xs text-gray-400">Alerts per page:</label>
                <select
                  id="history-alerts-per-page"
                  value={historyPageSize}
                  onChange={e => changeHistoryPageSize(Number(e.target.value))}
                  className="text-sm bg-gray-800 border border-gray-700 text-white rounded-lg px-2 py-1 focus:outline-none focus:border-sky-500"
                >
                  {PAGE_SIZE_OPTIONS.map(size => (
                    <option key={size} value={size}>{size}</option>
                  ))}
                </select>
              </div>
            </div>
          )}
          {pagedHistory.map(e => <EventCard key={e.id} event={e} onAck={handleAck} timezone={timezone} />)}
          {filteredHistory.length > 0 && (
            <p className="text-xs text-gray-500 pt-1">
              Showing {((historyPageClamped - 1) * historyPageSize + 1).toLocaleString()}–{((historyPageClamped - 1) * historyPageSize + pagedHistory.length).toLocaleString()} of {filteredHistory.length.toLocaleString()} alerts
            </p>
          )}
        </div>
      )}

      {/* Rules */}
      {tab === 'rules' && (
        <div className="space-y-4">
          {addingRule && (
            <RuleForm
              initial={EMPTY_RULE}
              onSave={handleSaveRule}
              onCancel={() => setAddingRule(false)}
              saving={saving}
            />
          )}
          {editRule && (
            <RuleForm
              initial={fromRule(editRule)}
              onSave={handleSaveRule}
              onCancel={() => setEditRule(null)}
              saving={saving}
            />
          )}
          {error && <p className="text-sm text-red-400">{error}</p>}

          {(() => {
            const displayedRules = rules
              .filter(r => {
                if (rulesTopicFilter && ruleGroup(r.rule_type) !== rulesTopicFilter) return false
                if (!rulesFilter) return true
                const q = rulesFilter.toLowerCase()
                return r.name.toLowerCase().includes(q) ||
                  r.rule_type.toLowerCase().includes(q) ||
                  r.severity.toLowerCase().includes(q)
              })
              .sort((a, b) => {
                if (!rulesSortKey) return 0
                const av = rulesSortKey === 'topic' ? ruleGroup(a.rule_type) : a[rulesSortKey] as any
                const bv = rulesSortKey === 'topic' ? ruleGroup(b.rule_type) : b[rulesSortKey] as any
                if (typeof av === 'number') return rulesSortDir === 'asc' ? av - bv : bv - av
                return rulesSortDir === 'asc'
                  ? String(av ?? '').localeCompare(String(bv ?? ''))
                  : String(bv ?? '').localeCompare(String(av ?? ''))
              })
            const RULE_COLS: Array<{ label: string; key: keyof AlertRule | 'topic' | null }> = [
              { label: 'Enabled',  key: null },
              { label: 'Rule',     key: 'name' },
              { label: 'Topic',    key: 'topic' },
              { label: 'Type',     key: 'rule_type' },
              { label: 'Severity', key: 'severity' },
              { label: 'Channels', key: null },
              { label: 'Cooldown', key: 'cooldown_min' },
              { label: '',         key: null },
            ]
            return (
          <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2.5 border-b border-gray-800 flex items-center gap-3 flex-wrap">
              <input
                value={rulesFilter}
                onChange={e => setRulesFilter(e.target.value)}
                placeholder="Filter by name, type, severity…"
                className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1 text-xs text-white placeholder-gray-600 w-52 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
              {rulesFilter && <button onClick={() => setRulesFilter('')} className="text-xs text-white hover:text-white">✕</button>}
              <select
                value={rulesTopicFilter}
                onChange={e => setRulesTopicFilter(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">All topics</option>
                <option value="Traffic">Traffic</option>
                <option value="Security">Security</option>
                <option value="Infrastructure">Infrastructure</option>
              </select>
              {rulesTopicFilter && (
                <button onClick={() => setRulesTopicFilter('')} className="text-xs text-white hover:text-white">✕</button>
              )}
              <span className="text-xs text-white ml-auto">{displayedRules.length} rule{displayedRules.length !== 1 ? 's' : ''}</span>
            </div>
            <div className="f-tbl-scroll">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-800">
                    {RULE_COLS.map(col => (
                      <th
                        key={col.label}
                        onClick={() => col.key && toggleRulesSort(col.key)}
                        className={`px-4 py-3 text-left text-xs font-medium select-none
                          ${col.key ? `cursor-pointer ${rulesSortKey === col.key ? 'text-blue-400' : 'text-white hover:text-gray-200'}` : 'text-white'}`}
                      >
                        {col.label}
                        {rulesSortKey === col.key && col.key && <span className="ml-1">{rulesSortDir === 'asc' ? '↑' : '↓'}</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800/50">
                  {displayedRules.map(rule => (
                    <tr key={rule.id} className="hover:bg-gray-800/30 transition-colors">
                      <td className="px-4 py-3">
                        <button
                          onClick={() => handleToggle(rule)}
                          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                            rule.enabled ? 'bg-blue-600' : 'bg-gray-700'
                          }`}
                        >
                          <span className={`inline-block h-3 w-3 rounded-full bg-white transition-transform ${
                            rule.enabled ? 'translate-x-5' : 'translate-x-1'
                          }`} />
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <p className="font-medium text-white">{rule.name}</p>
                        {rule.description && (
                          <p className="text-xs text-white mt-0.5">{rule.description}</p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {(() => {
                          const grp = ruleGroup(rule.rule_type)
                          return (
                            <span className={`text-xs px-2 py-0.5 rounded-full ${TOPIC_STYLES[grp] ?? 'bg-gray-800 text-gray-400'}`}>
                              {grp}
                            </span>
                          )
                        })()}
                      </td>
                      <td className="px-4 py-3 text-white text-xs">
                        <span className="bg-gray-800 px-2 py-0.5 rounded">{rule.rule_type}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded-full capitalize ${SEV_STYLES[rule.severity] ?? SEV_STYLES.info}`}>
                          {rule.severity}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-white">{rule.channels.join(', ')}</td>
                      <td className="px-4 py-3 text-xs text-white">{rule.cooldown_min}m</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <button
                            onClick={() => { setEditRule(rule); setAddingRule(false) }}
                            className="text-xs text-white hover:text-blue-400 transition-colors"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => handleDeleteRule(rule.id)}
                            className="text-xs text-white hover:text-red-400 transition-colors"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {displayedRules.length === 0 && (
                    <tr>
                      <td colSpan={8} className="px-4 py-8 text-center text-sm text-white">
                        {rulesFilter ? 'No rules match this filter' : 'No alert rules yet — click "+ New rule" to add one'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
            )
          })()}
        </div>
      )}

      {rulesImportResult && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 py-8 px-4" onClick={() => setRulesImportResult(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 max-w-lg w-full" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-white mb-3">Import complete</h3>
            <div className="space-y-1 mb-4">
              <p className="text-sm text-green-400">✓ {rulesImportResult.created} rule{rulesImportResult.created !== 1 ? 's' : ''} created</p>
              {rulesImportResult.skipped > 0 && (
                <p className="text-sm text-yellow-400">⚠ {rulesImportResult.skipped} row{rulesImportResult.skipped !== 1 ? 's' : ''} skipped</p>
              )}
            </div>
            {rulesImportResult.errors.length > 0 && (
              <div className="bg-gray-800 rounded-lg px-3 py-2 max-h-36 overflow-y-auto mb-4">
                {rulesImportResult.errors.map((e, i) => (
                  <p key={i} className="text-xs text-red-400 font-mono">{e}</p>
                ))}
              </div>
            )}
            <div className="bg-gray-800/60 rounded-lg px-3 py-2 mb-4">
              <p className="text-xs font-medium text-gray-400 mb-1">CSV columns (header row required)</p>
              <p className="text-xs font-mono text-gray-500 break-all">
                name, description, rule_type, conditions, time_window_min, severity, channels, cooldown_min, enabled
              </p>
              <p className="text-xs text-gray-600 mt-1">
                conditions: a JSON object string — shape depends on rule_type. channels: comma-separated (e.g. inapp,slack).
              </p>
            </div>
            <div className="flex items-center justify-between">
              <button onClick={handleDownloadRulesTemplate} className="text-xs text-blue-400 hover:text-blue-300 transition-colors">
                ↓ Download template
              </button>
              <button onClick={() => setRulesImportResult(null)} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg">
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
