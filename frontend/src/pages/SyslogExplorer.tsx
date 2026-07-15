/**
 * Syslog Explorer — filtered log search with pagination and row expansion.
 */
import React, { useState, useCallback, useEffect, useRef } from 'react'
import { api, SyslogRecord, SyslogSearchParams } from '../api/client'
import { useTimezone } from '../hooks/useTimezone'

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

const PAGE_SIZE = 100

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

// ── Row expansion ─────────────────────────────────────────────────────────────

// Every syslog_events column gets its own field here — no conditional
// hiding, no combining two columns into one line — so the dropdown is a
// complete, literal listing of the full record, not just a subset.
function ExpandedRow({ r, timezone }: { r: SyslogRecord; timezone: string }) {
  const fields: [string, string][] = [
    ['timestamp',      fmtTs(r.timestamp, timezone)],
    ['received_at',    fmtTs(r.received_at, timezone)],
    ['source_ip',      r.source_ip],
    ['source_name',    r.source_name || '—'],
    ['facility',       String(r.facility)],
    ['facility_name',  r.facility_name],
    ['severity',       String(r.severity)],
    ['severity_name',  r.severity_name],
    ['program',        r.program || '—'],
    ['pid',            r.pid || '—'],
    ['collector_ip',   r.collector_ip],
    ['collector_name', r.collector_name || '—'],
    ['org',            r.org || '—'],
    ['log_group',      r.log_group || '—'],
    ['site',           r.site || '—'],
  ]

  return (
    <tr className="bg-gray-950">
      <td colSpan={6} className="px-5 py-3">
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
  // Filter state
  const [presetIdx, setPresetIdx] = useState(2)       // default: last 6h
  const [severityMax, setSeverityMax] = useState('')
  const [program, setProgram]   = useState('')
  const [sourceIp, setSourceIp] = useState('')
  const [collectorName, setCollectorName] = useState('')
  const [q, setQ] = useState('')
  const [qDraft, setQDraft] = useState('')

  // Results state
  const [records, setRecords]   = useState<SyslogRecord[]>([])
  const [total, setTotal]       = useState(0)
  const [offset, setOffset]     = useState(0)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const timezone = useTimezone()

  const abortRef = useRef<AbortController | null>(null)

  const search = useCallback(async (off = 0) => {
    abortRef.current?.abort()
    abortRef.current = new AbortController()

    setLoading(true)
    setError('')
    try {
      const params: SyslogSearchParams = {
        start: toISOStart(TIME_PRESETS[presetIdx].minutes),
        limit: PAGE_SIZE,
        offset: off,
      }
      if (severityMax !== '') params.severity_max = Number(severityMax)
      if (program)        params.program = program
      if (sourceIp)       params.source_ip = sourceIp
      if (collectorName)  params.collector_name = collectorName
      if (q)              params.q = q

      const res = await api.searchSyslog(params)
      setRecords(res.records)
      setTotal(res.total)
      setOffset(off)
      setExpanded(null)
    } catch (e: any) {
      if (e.name !== 'AbortError') setError(e.message ?? 'Search failed')
    } finally {
      setLoading(false)
    }
  }, [presetIdx, severityMax, program, sourceIp, collectorName, q])

  // Run on mount and when filters change
  useEffect(() => { search(0) }, [search])

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  const rowKey = (r: SyslogRecord, i: number) => `${r.timestamp}-${r.source_ip}-${i}`

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold text-white">Syslog Explorer</h1>

      {/* Filter bar */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
        {/* Row 1: time + severity + search */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Time preset */}
          <div className="flex items-center gap-1 bg-gray-800 border border-gray-700 rounded-lg p-0.5">
            {TIME_PRESETS.map((p, i) => (
              <button
                key={p.label}
                onClick={() => setPresetIdx(i)}
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
          <input
            type="text"
            value={sourceIp}
            onChange={e => setSourceIp(e.target.value)}
            placeholder="Source IP…"
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 w-40 font-mono"
          />
          <input
            type="text"
            value={collectorName}
            onChange={e => setCollectorName(e.target.value)}
            placeholder="Collector…"
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 w-36"
          />
          <input
            type="text"
            value={program}
            onChange={e => setProgram(e.target.value)}
            placeholder="Program…"
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500 w-36"
          />
          <button
            onClick={() => {
              setSourceIp(''); setCollectorName(''); setProgram('')
              setSeverityMax(''); setQDraft(''); setQ('')
            }}
            className="text-xs text-gray-500 hover:text-white transition-colors ml-auto"
          >
            Clear filters
          </button>
        </div>
      </div>

      {/* Results header */}
      <div className="flex items-center justify-between text-xs text-gray-500">
        <span>
          {loading ? 'Searching…' : `${total.toLocaleString()} result${total !== 1 ? 's' : ''}`}
          {total > 0 && ` · showing ${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`}
        </span>
        {totalPages > 1 && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => search(offset - PAGE_SIZE)}
              disabled={offset === 0}
              className="px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
            >
              ← Prev
            </button>
            <span className="text-gray-400">{currentPage} / {totalPages}</span>
            <button
              onClick={() => search(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= total}
              className="px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
            >
              Next →
            </button>
          </div>
        )}
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
          <div className="px-5 py-10 text-center text-gray-600 text-sm">
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
                        {r.collector_name || r.collector_ip}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-300 truncate max-w-0 w-32">
                        {r.source_name || r.source_ip}
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

      {/* Bottom pagination */}
      {totalPages > 1 && (
        <div className="flex justify-end gap-2">
          <button
            onClick={() => search(offset - PAGE_SIZE)}
            disabled={offset === 0}
            className="px-3 py-1.5 text-xs bg-gray-900 border border-gray-700 rounded-lg disabled:opacity-40 hover:bg-gray-800 text-white transition-colors"
          >
            ← Previous
          </button>
          <button
            onClick={() => search(offset + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total}
            className="px-3 py-1.5 text-xs bg-gray-900 border border-gray-700 rounded-lg disabled:opacity-40 hover:bg-gray-800 text-white transition-colors"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}
