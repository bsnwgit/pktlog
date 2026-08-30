/**
 * Syslog Explorer — filtered log search with pagination and row expansion.
 */
import React, { useState, useCallback, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, SyslogRecord, SyslogSearchParams } from '../api/client'
import { useTimezone } from '../hooks/useTimezone'
import HelpButton from '../components/HelpButton'
import IpLink from '../components/IpLink'

// ── Severity config ───────────────────────────────────────────────────────────

const SEV_BADGE: Record<string, string> = {
  emergency: 'bg-red-900/60 text-red-300 border-red-700/50',
  alert:     'bg-red-900/40 text-red-300 border-red-700/40',
  critical:  'bg-orange-900/50 text-orange-300 border-orange-700/40',
  error:     'bg-orange-900/30 text-orange-300 border-orange-700/30',
  warning:   'bg-yellow-900/30 text-yellow-300 border-yellow-700/30',
  notice:    'bg-blue-900/30 text-blue-300 border-blue-700/30',
  info:      'bg-green-900/20 text-green-300 border-green-700/20',
  debug:     'bg-gray-800/60 text-gray-400 border-gray-700/30',
}

const SEV_OPTIONS = [
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

const TIME_PRESETS = [
  { label: 'Last 15m', minutes: 15 },
  { label: 'Last 1h',  minutes: 60 },
  { label: 'Last 6h',  minutes: 360 },
  { label: 'Last 24h', minutes: 1440 },
  { label: 'Last 7d',  minutes: 10080 },
]

const PAGE_SIZE_OPTIONS = [25, 50, 75, 100]

// ── Field filters ─────────────────────────────────────────────────────────────
// The five free-text filters, driven from one list so they share the debounce
// and the Exact toggle rather than each carrying its own copy of the wiring.

type FieldFilters = {
  sourceIp: string
  destIp: string
  collectorIp: string
  collectorName: string
  program: string
}

const FIELD_FILTERS: { key: keyof FieldFilters; placeholder: string; width: string; mono: boolean }[] = [
  { key: 'sourceIp',      placeholder: 'Source IP…',      width: 'w-40', mono: true  },
  { key: 'destIp',        placeholder: 'Destination IP…', width: 'w-40', mono: true  },
  { key: 'collectorIp',   placeholder: 'Collector IP…',   width: 'w-36', mono: true  },
  { key: 'collectorName', placeholder: 'Collector…',      width: 'w-36', mono: false },
  { key: 'program',       placeholder: 'Program…',        width: 'w-36', mono: false },
]

const EMPTY_FIELDS: FieldFilters = {
  sourceIp: '', destIp: '', collectorIp: '', collectorName: '', program: '',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function toISOStart(minutesAgo: number): string {
  return new Date(Date.now() - minutesAgo * 60_000).toISOString()
}

function fmtTs(iso: string, timeZone: string): string {
  const d = new Date(iso)
  return d.toLocaleString([], {
    timeZone,
    month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

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
  const btn = (p: number) =>
    `text-xs min-w-[1.75rem] px-2 py-1 rounded-lg border transition-colors ${
      p === page
        ? 'bg-blue-600/30 border-blue-500 text-blue-200'
        : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white'
    }`
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

// ── Row expansion ─────────────────────────────────────────────────────────────

// Every syslog_events column gets its own field here — no conditional
// hiding, no combining two columns into one line — so the dropdown is a
// complete, literal listing of the full record, not just a subset.
function ExpandedRow({ r, timezone }: { r: SyslogRecord; timezone: string }) {
  const fields: [string, React.ReactNode][] = [
    ['timestamp',      fmtTs(r.timestamp, timezone)],
    ['received_at',    fmtTs(r.received_at, timezone)],
    ['source_ip',      <IpLink ip={r.source_ip} />],
    ['source_name',    r.source_name || '—'],
    ['dest_ip',        r.dest_ip ? <IpLink ip={r.dest_ip} /> : '—'],
    ['facility',       String(r.facility)],
    ['facility_name',  r.facility_name],
    ['severity',       String(r.severity)],
    ['severity_name',  r.severity_name],
    ['program',        r.program || '—'],
    ['pid',            r.pid || '—'],
    ['collector_ip',   <IpLink ip={r.collector_ip} />],
    ['collector_name', r.collector_name || '—'],
    ['org',            r.org || '—'],
    ['log_group',      r.log_group || '—'],
    ['site',           r.site || '—'],
  ]

  return (
    <tr className="bg-gray-950">
      <td colSpan={7} className="px-5 py-3">
        <div className="space-y-2 text-xs font-mono">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-8 gap-y-1 text-gray-400">
            {fields.map(([label, value]) => (
              <div key={label}><span className="text-gray-600">{label} </span>{value}</div>
            ))}
          </div>
          <div>
            <p className="text-gray-600 mb-0.5">message</p>
            <p className="text-gray-200 whitespace-pre-wrap break-all">{r.message}</p>
          </div>
          <div>
            <p className="text-gray-600 mb-0.5">raw</p>
            <p className="text-gray-500 whitespace-pre-wrap break-all">{r.raw || '—'}</p>
          </div>
        </div>
      </td>
    </tr>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function SyslogExplorer() {
  const [searchParams, setSearchParams] = useSearchParams()

  // Filter state — seeded from URL params when deep-linked from an alert's
  // "Investigate" button (see Alerts.tsx buildInvestigateUrl).
  const [presetIdx, setPresetIdx] = useState(2)       // default: last 6h
  const [severityMax, setSeverityMax] = useState(() => searchParams.get('severity_max') ?? '')
  const [q, setQ] = useState(() => searchParams.get('q') ?? '')
  const [qDraft, setQDraft] = useState(() => searchParams.get('q') ?? '')

  // The five field filters match case-insensitively, anywhere in the value.
  // Exact anchors them at the first character instead, so each character typed
  // narrows the set further rather than the value having to match outright.
  // It doesn't apply to the message search — a syslog line almost never starts
  // with the term you're looking for.
  const [exact, setExact] = useState(false)

  // Those five run as you type, so they're held twice: `fieldDraft` backs the
  // inputs and updates on every keystroke, `fields` is the debounced copy the
  // query reads. Sharing one initial object keeps the two identical on mount,
  // so the debounce below is a no-op until something is actually typed.
  const initialFields = (): FieldFilters => ({
    sourceIp:      searchParams.get('source_ip') ?? '',
    destIp:        searchParams.get('dest_ip') ?? '',
    collectorIp:   searchParams.get('collector_ip') ?? '',
    collectorName: searchParams.get('collector_name') ?? '',
    program:       searchParams.get('program') ?? '',
  })
  const [fieldDraft, setFieldDraft] = useState(initialFields)
  const [fields, setFields] = useState(fieldDraft)

  useEffect(() => {
    const t = setTimeout(() => setFields(fieldDraft), 300)
    return () => clearTimeout(t)
  }, [fieldDraft])

  // Absolute time window from a deep link — overrides the preset buttons
  // until the user picks a preset themselves, since an alert's investigate
  // link needs the exact window around when it fired, not a rounded preset.
  const [absWindow, setAbsWindow] = useState<{ from?: string; to?: string } | null>(() => {
    const from = searchParams.get('time_from')
    const to   = searchParams.get('time_to')
    return from ? { from, to: to ?? undefined } : null
  })

  // Clear the deep-link params from the URL once consumed so revisiting
  // this page later doesn't keep reapplying a stale investigate window.
  useEffect(() => {
    if (searchParams.toString()) setSearchParams({}, { replace: true })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Results state
  const [records, setRecords]   = useState<SyslogRecord[]>([])
  const [total, setTotal]       = useState(0)
  const [offset, setOffset]     = useState(0)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [pageSize, setPageSize] = useState(25)
  const timezone = useTimezone()

  const abortRef = useRef<AbortController | null>(null)

  const search = useCallback(async (off = 0) => {
    abortRef.current?.abort()
    const ctl = new AbortController()
    abortRef.current = ctl

    setLoading(true)
    setError('')
    try {
      const params: SyslogSearchParams = {
        start: absWindow?.from ?? toISOStart(TIME_PRESETS[presetIdx].minutes),
        limit: pageSize,
        offset: off,
      }
      if (absWindow?.to)  params.end = absWindow.to
      if (severityMax !== '')   params.severity_max = Number(severityMax)
      if (fields.program)       params.program = fields.program
      if (fields.sourceIp)      params.source_ip = fields.sourceIp
      if (fields.destIp)        params.dest_ip = fields.destIp
      if (fields.collectorIp)   params.collector_ip = fields.collectorIp
      if (fields.collectorName) params.collector_name = fields.collectorName
      if (q)                    params.q = q
      if (exact)                params.match_mode = 'prefix'

      const res = await api.searchSyslog(params, ctl.signal)
      // A response that arrives after its request was superseded must not
      // overwrite the newer one's results — the abort usually prevents this,
      // but a request already on the wire can still land first.
      if (abortRef.current !== ctl) return
      setRecords(res.records)
      setTotal(res.total)
      setOffset(off)
      setExpanded(null)
    } catch (e: any) {
      if (e.name !== 'AbortError') setError(e.message ?? 'Search failed')
    } finally {
      if (abortRef.current === ctl) setLoading(false)
    }
  }, [presetIdx, severityMax, fields, exact, q, absWindow, pageSize])

  // Run on mount and when filters change
  useEffect(() => { search(0) }, [search])

  const changePageSize = (size: number) => { setPageSize(size) }

  const totalPages = Math.ceil(total / pageSize)
  const currentPage = Math.floor(offset / pageSize) + 1

  const rowKey = (r: SyslogRecord, i: number) => `${r.timestamp}-${r.source_ip}-${i}`

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="text-xl font-bold text-white">Syslog Explorer</h1>
        <HelpButton title="Syslog Explorer — How It Works">
          <p>Queries hit raw syslog records directly, server-side paginated — timestamps are displayed in the app's configured timezone (Settings → General), not your browser's local zone.</p>
          <p>Only messages from a <span className="text-gray-300 font-medium">registered, enabled collector</span> ever reach storage — a device sending syslog on the wire that isn't registered won't appear here at all, not even unlabeled. If something is missing, check the <span className="text-gray-300 font-medium">Approval</span> page: senders pktLog has seen but dropped are queued there waiting to be admitted.</p>
          <p>Rows expand in place for the full raw message — useful for headerless or non-standard formats (some devices send syslog-shaped lines with no <code className="text-gray-400">&lt;PRI&gt;</code> header, or no header at all) that still get parsed into a real message rather than showing up as unparseable.</p>
          <p>The <span className="text-gray-300 font-medium">Source / Destination / Collector / Program</span> boxes filter as you type and match case-insensitively, anywhere in the value — <code className="text-gray-400">203</code> finds <code className="text-gray-400">10.0.203.7</code>. Ticking <span className="text-gray-300 font-medium">Exact</span> anchors all five at the first character instead, so they narrow from the left as you type. The message box is always a full-text search and ignores Exact.</p>
          <p><span className="text-gray-300 font-medium">Destination</span> is only populated for messages that embed a <code className="text-gray-400">DST=</code> field in their content (netfilter/firewall-style logs) — most syslog lines have no concept of a destination IP, so this column is blank for them, not broken.</p>
        </HelpButton>
      </div>

      {/* Filter bar */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
        {/* Row 1: time + severity + search */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Time preset */}
          <div className="flex items-center gap-1 bg-gray-800 border border-gray-700 rounded-lg p-0.5">
            {TIME_PRESETS.map((p, i) => (
              <button
                key={p.label}
                onClick={() => { setPresetIdx(i); setAbsWindow(null) }}
                className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
                  i === presetIdx ? 'bg-blue-600 text-white font-medium' : 'text-gray-400 hover:text-white'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Severity */}
          <select
            value={severityMax}
            onChange={e => setSeverityMax(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500"
          >
            {SEV_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>

          {/* Text search */}
          <form
            className="flex-1 flex items-center gap-2 min-w-48"
            onSubmit={e => { e.preventDefault(); setQ(qDraft) }}
          >
            <input
              type="text"
              value={qDraft}
              onChange={e => setQDraft(e.target.value)}
              placeholder="Search message text…"
              className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
            />
            <button
              type="submit"
              className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors"
            >
              Search
            </button>
            {q && (
              <button
                type="button"
                onClick={() => { setQDraft(''); setQ('') }}
                className="text-gray-500 hover:text-white text-xs px-2 py-1.5"
              >
                ✕
              </button>
            )}
          </form>
        </div>

        {/* Row 2: source, collector, program filters */}
        <div className="flex items-center gap-3 flex-wrap">
          {FIELD_FILTERS.map(f => (
            <input
              key={f.key}
              type="text"
              value={fieldDraft[f.key]}
              onChange={e => setFieldDraft(d => ({ ...d, [f.key]: e.target.value }))}
              placeholder={f.placeholder}
              className={`bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 ${f.width} ${f.mono ? 'font-mono' : ''}`}
            />
          ))}
          <label className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-white transition-colors cursor-pointer select-none">
            <input
              type="checkbox"
              checked={exact}
              onChange={e => setExact(e.target.checked)}
              className="w-3.5 h-3.5 rounded border-gray-700 bg-gray-800 text-blue-600 focus:ring-0 focus:ring-offset-0 cursor-pointer"
            />
            Exact
          </label>
          <button
            onClick={() => {
              setFieldDraft(EMPTY_FIELDS)
              setSeverityMax(''); setQDraft(''); setQ(''); setAbsWindow(null)
            }}
            className="text-xs text-gray-500 hover:text-white transition-colors ml-auto"
          >
            Clear filters
          </button>
        </div>
      </div>

      {/* Results header */}
      <div className="flex items-center justify-between text-xs text-gray-500 flex-wrap gap-2">
        <span>
          {loading ? 'Searching…' : `${total.toLocaleString()} result${total !== 1 ? 's' : ''}`}
          {total > 0 && ` · showing ${offset + 1}–${Math.min(offset + pageSize, total)}`}
        </span>
        <div className="flex items-center justify-center gap-6">
          <Pagination page={currentPage} totalPages={totalPages} onChange={p => search((p - 1) * pageSize)} />
          <div className="flex items-center gap-2">
            <label htmlFor="explorer-results-per-page" className="text-xs text-gray-400">Results per page:</label>
            <select
              id="explorer-results-per-page"
              value={pageSize}
              onChange={e => changePageSize(Number(e.target.value))}
              className="text-sm bg-gray-800 border border-gray-700 text-white rounded-lg px-2 py-1 focus:outline-none focus:border-sky-500"
            >
              {PAGE_SIZE_OPTIONS.map(size => (
                <option key={size} value={size}>{size}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-900/20 border border-red-700/30 rounded-xl px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Results table */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        {loading && records.length === 0 ? (
          <div className="space-y-0">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-10 border-b border-gray-800 animate-pulse bg-gray-800/20" />
            ))}
          </div>
        ) : records.length === 0 ? (
          <div className="f-tbl-scroll px-5 py-10 text-center text-gray-600 text-sm">
            No log records found for this filter set.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-xs text-gray-500">
                <th className="px-4 py-2.5 text-left font-medium w-40">Timestamp</th>
                <th className="px-3 py-2.5 text-left font-medium w-24">Severity</th>
                <th className="px-3 py-2.5 text-left font-medium w-32">Collector</th>
                <th className="px-3 py-2.5 text-left font-medium w-32">Source</th>
                <th className="px-3 py-2.5 text-left font-medium w-32">Destination</th>
                <th className="px-3 py-2.5 text-left font-medium w-28">Program</th>
                <th className="px-3 py-2.5 text-left font-medium">Message</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r, i) => {
                const key = rowKey(r, i)
                const isExpanded = expanded === key
                const badge = SEV_BADGE[r.severity_name] ?? SEV_BADGE.debug
                return (
                  <React.Fragment key={key}>
                    <tr
                      onClick={() => setExpanded(isExpanded ? null : key)}
                      className={`border-b border-gray-800/60 cursor-pointer transition-colors ${
                        isExpanded ? 'bg-gray-800/50' : 'hover:bg-gray-800/30'
                      }`}
                    >
                      <td className="px-4 py-2 font-mono text-xs text-gray-400 whitespace-nowrap">
                        {fmtTs(r.timestamp, timezone)}
                      </td>
                      <td className="px-3 py-2">
                        <span className={`inline-block px-2 py-0.5 text-xs rounded border font-medium capitalize ${badge}`}>
                          {r.severity_name}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-300 truncate max-w-0 w-32">
                        {r.collector_name || <IpLink ip={r.collector_ip} />}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-300 truncate max-w-0 w-32">
                        {r.source_name || <IpLink ip={r.source_ip} />}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-300 truncate max-w-0 w-32">
                        {r.dest_ip ? <IpLink ip={r.dest_ip} /> : '—'}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-400 truncate max-w-0 w-28">
                        {r.program || '—'}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-200 max-w-0">
                        <p className="truncate">{r.message}</p>
                      </td>
                    </tr>
                    {isExpanded && <ExpandedRow r={r} timezone={timezone} />}
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
