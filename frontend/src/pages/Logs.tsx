import { useEffect, useState, useCallback, useRef } from 'react'
import { api, LogRecord, LogStats } from '../api/client'
import { useAuth } from '../store/auth'
import { useTimezone } from '../hooks/useTimezone'
import HelpButton from '../components/HelpButton'
import clsx from 'clsx'

// ── Constants ─────────────────────────────────────────────────────────────────

const LEVELS = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const
type Level = typeof LEVELS[number]

const LEVEL_STYLES: Record<string, string> = {
  DEBUG:    'bg-gray-500/20 text-gray-300 border border-gray-500/40',
  INFO:     'bg-blue-500/20 text-blue-300 border border-blue-500/40',
  WARNING:  'bg-yellow-500/20 text-yellow-400 border border-yellow-500/40',
  ERROR:    'bg-red-500/20 text-red-400 border border-red-500/40',
  CRITICAL: 'bg-red-700/30 text-red-300 border border-red-600/50',
}

const LEVEL_DOT: Record<string, string> = {
  DEBUG:    'bg-gray-400',
  INFO:     'bg-blue-400',
  WARNING:  'bg-yellow-400',
  ERROR:    'bg-red-400',
  CRITICAL: 'bg-red-300',
}

const CHIP_ACTIVE: Record<Level, string> = {
  ALL:      'bg-gray-700 text-white border-gray-500',
  DEBUG:    'bg-gray-500/30 text-gray-200 border-gray-400',
  INFO:     'bg-blue-600/30 text-blue-200 border-blue-400',
  WARNING:  'bg-yellow-500/30 text-yellow-200 border-yellow-400',
  ERROR:    'bg-red-500/30 text-red-200 border-red-400',
  CRITICAL: 'bg-red-700/40 text-red-200 border-red-500',
}

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
// shift the displayed clock time by the browser's UTC offset. Note: this is
// necessarily the browser's local timezone, not the app's configured display
// timezone (useTimezone) — a plain HTML datetime-local input has no way to
// pin itself to an arbitrary configured zone.
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
 * and reports the resolved {since, until} ISO bounds up to the parent — as logs
 * grow, a preset alone won't always pinpoint the right window.
 *
 * Validation: neither side can be in the future (clamped back to now — both via
 * the inputs' own `max` and as a typed-input backstop), and "to" must be after
 * "from" (same-day-with-earlier-end-time counts as invalid too, since it's a
 * plain datetime comparison, not just a date comparison) — an invalid range
 * shows an inline error and does not get applied, leaving the last valid window.
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
        className="text-xs bg-gray-800 border border-gray-700 text-gray-300 rounded-lg px-2.5 py-1 focus:outline-none focus:border-blue-500"
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
            className="text-xs bg-gray-800 border border-gray-700 text-white rounded-lg px-2 py-1 focus:outline-none focus:border-blue-500"
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
            className="text-xs bg-gray-800 border border-gray-700 text-white rounded-lg px-2 py-1 focus:outline-none focus:border-blue-500"
          />
          {rangeError && <span className="text-xs text-red-400">{rangeError}</span>}
        </>
      )}
    </div>
  )
}

function fmtTs(ts: string, timeZone: string): string {
  const d = new Date(ts)
  // Include milliseconds manually since fractionalSecondDigits has limited TS support
  const base = d.toLocaleString([], {
    timeZone,
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
  const ms = String(d.getMilliseconds()).padStart(3, '0')
  return `${base}.${ms}`
}

function shortLogger(logger: string): string {
  // pktlog.api.logs → api.logs
  return logger.replace(/^pktlog\./, '')
}

const PAGE_SIZE = 50

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
  const btn = (p: number) => clsx(
    'text-xs min-w-[1.75rem] px-2 py-1 rounded-lg border transition-colors',
    p === page
      ? 'bg-blue-600/30 border-blue-500 text-blue-200'
      : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white',
  )
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

// ── Component ─────────────────────────────────────────────────────────────────

export default function Logs() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const timezone = useTimezone()

  const [records, setRecords] = useState<LogRecord[]>([])
  const [stats, setStats]     = useState<LogStats | null>(null)
  const [total, setTotal]     = useState(0)

  const [level, setLevel]         = useState<Level>('ALL')
  const [logger, setLogger]       = useState('')
  const [search, setSearch]       = useState('')
  const [timeWindow, setTimeWindow] = useState<TimeWindow>({})
  const [liveLevel, setLiveLevel] = useState('WARNING')
  const [page, setPage]           = useState(1)

  const [autoRefresh, setAutoRefresh] = useState(false)
  const [loading, setLoading]         = useState(false)
  const [error, setError]             = useState('')
  const [clearing, setClearing]       = useState(false)
  const [levelChanging, setLevelChanging] = useState(false)

  const [expanded, setExpanded] = useState<number | null>(null)

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Data fetch ─────────────────────────────────────────────────────────────

  const fetchLogs = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    setError('')
    try {
      const params: Record<string, string> = {
        limit: String(PAGE_SIZE),
        offset: String((page - 1) * PAGE_SIZE),
      }
      if (level !== 'ALL') params.level = level
      if (logger)          params.logger = logger
      if (search)          params.search = search
      if (timeWindow.since) params.since = timeWindow.since
      if (timeWindow.until) params.until = timeWindow.until

      const [res, s] = await Promise.all([
        api.getLogs(params),
        api.getLogStats(),
      ])
      setRecords(res.records)
      setTotal(res.total)
      setStats(s)
    } catch (e: any) {
      setError(e.message ?? 'Failed to load logs')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [level, logger, search, timeWindow, page])

  useEffect(() => {
    fetchLogs()
  }, [fetchLogs])

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (autoRefresh) {
      timerRef.current = setInterval(() => fetchLogs(true), 5000)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [autoRefresh, fetchLogs])

  // ── Actions ────────────────────────────────────────────────────────────────

  const handleClear = async () => {
    if (!confirm('Clear all app logs?')) return
    setClearing(true)
    try {
      await api.clearLogs()
      setRecords([])
      setTotal(0)
      setStats(null)
      setPage(1)
    } catch (e: any) {
      setError(e.message ?? 'Clear failed')
    } finally {
      setClearing(false)
    }
  }

  const handleSetLevel = async (newLevel: string) => {
    setLevelChanging(true)
    try {
      await api.setLogLevel(newLevel)
      setLiveLevel(newLevel)
    } catch (e: any) {
      setError(e.message ?? 'Failed to set level')
    } finally {
      setLevelChanging(false)
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-white">Application Logs</h1>
            <HelpButton title="Application Logs — How It Works">
              <p>This is pktLog's <span className="text-gray-300 font-medium">own backend log stream</span> — service startup, ingest errors, alert evaluation, auth events — not syslog data received from your devices. For that, use Syslog Explorer instead.</p>
              <p>Level filter and search combine — narrowing to <code className="text-gray-400">ERROR</code> plus a keyword returns only errors matching that text.</p>
              <p><span className="text-gray-300 font-medium">Custom range</span> in the time picker pins an exact from/to window instead of a relative preset — useful when correlating a log entry against a specific incident timestamp.</p>
            </HelpButton>
          </div>
          <p className="text-sm text-gray-400 mt-0.5">
            {total.toLocaleString()} records stored
            {stats?.latest_ts && <> · last entry {fmtTs(stats.latest_ts, timezone)}</>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Auto-refresh toggle */}
          <button
            onClick={() => setAutoRefresh(v => !v)}
            className={clsx(
              'flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border transition-colors',
              autoRefresh
                ? 'bg-blue-600/20 border-blue-500/50 text-blue-300'
                : 'bg-gray-800 border-gray-700 text-gray-300 hover:text-white',
            )}
          >
            <svg className={clsx('w-3 h-3', autoRefresh && 'animate-spin')} style={autoRefresh ? { animationDuration: '3s' } : {}} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
            </svg>
            {autoRefresh ? 'Live' : 'Refresh'}
          </button>
          <button
            onClick={() => fetchLogs()}
            disabled={loading}
            className="text-xs px-3 py-1.5 bg-gray-800 border border-gray-700 text-gray-300 hover:text-white rounded-lg transition-colors disabled:opacity-50"
          >
            Reload
          </button>
          {isAdmin && (
            <button
              onClick={handleClear}
              disabled={clearing}
              className="text-xs px-3 py-1.5 bg-gray-800 border border-red-800/50 text-red-400 hover:text-red-300 rounded-lg transition-colors disabled:opacity-50"
            >
              {clearing ? 'Clearing…' : 'Clear Logs'}
            </button>
          )}
        </div>
      </div>

      {/* Stats row */}
      {stats && (
        <div className="flex items-center gap-3 flex-wrap">
          {(['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'] as const).map(lvl => {
            const cnt = stats.by_level[lvl] ?? 0
            if (cnt === 0 && lvl === 'DEBUG') return null
            return (
              <div key={lvl} className={clsx('flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg', LEVEL_STYLES[lvl])}>
                <span className={clsx('w-1.5 h-1.5 rounded-full', LEVEL_DOT[lvl])} />
                <span className="font-medium">{lvl}</span>
                <span className="opacity-70">{cnt.toLocaleString()}</span>
              </div>
            )
          })}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Level chips */}
        <div className="flex items-center gap-1">
          {LEVELS.map(l => (
            <button
              key={l}
              onClick={() => { setLevel(l); setPage(1) }}
              className={clsx(
                'text-xs px-2.5 py-1 rounded-lg border transition-colors',
                level === l
                  ? CHIP_ACTIVE[l]
                  : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white',
              )}
            >
              {l}
            </button>
          ))}
        </div>

        {/* Logger filter */}
        {stats && stats.loggers.length > 0 && (
          <select
            value={logger}
            onChange={e => { setLogger(e.target.value); setPage(1) }}
            className="text-xs bg-gray-800 border border-gray-700 text-gray-300 rounded-lg px-2.5 py-1 focus:outline-none focus:border-blue-500"
          >
            <option value="">All loggers</option>
            {stats.loggers.map(l => (
              <option key={l} value={l}>{shortLogger(l)}</option>
            ))}
          </select>
        )}

        {/* Search */}
        <input
          type="text"
          placeholder="Search messages…"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          onKeyDown={e => e.key === 'Enter' && fetchLogs()}
          className="text-xs bg-gray-800 border border-gray-700 text-white placeholder-gray-500 rounded-lg px-3 py-1 w-52 focus:outline-none focus:border-blue-500"
        />

        {/* Time range */}
        <TimeRangeControl onChange={w => { setTimeWindow(w); setPage(1) }} />

        {/* Live capture level (admin) */}
        {isAdmin && (
          <div className="ml-auto flex items-center gap-2 text-xs text-gray-400">
            <span>Capture level:</span>
            <select
              value={liveLevel}
              onChange={e => handleSetLevel(e.target.value)}
              disabled={levelChanging}
              className="bg-gray-800 border border-gray-700 text-white rounded-lg px-2 py-1 focus:outline-none focus:border-blue-500 disabled:opacity-50"
            >
              <option value="DEBUG">DEBUG</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
              <option value="CRITICAL">CRITICAL</option>
            </select>
          </div>
        )}
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-700/50 rounded-lg px-4 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Pagination */}
      <Pagination page={page} totalPages={Math.max(1, Math.ceil(total / PAGE_SIZE))} onChange={setPage} />

      {/* Log table */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-gray-500 text-sm">Loading…</div>
        ) : records.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 text-gray-500 text-sm gap-2">
            <svg className="w-8 h-8 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"/>
            </svg>
            <span>No log records match your filters</span>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500 text-left">
                  <th className="px-4 py-2 font-medium w-44">Timestamp</th>
                  <th className="px-3 py-2 font-medium w-24">Level</th>
                  <th className="px-3 py-2 font-medium w-36">Logger</th>
                  <th className="px-3 py-2 font-medium">Message</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/60">
                {records.map(r => (
                  <>
                    <tr
                      key={r.id}
                      onClick={() => r.exc_info ? setExpanded(expanded === r.id ? null : r.id) : undefined}
                      className={clsx(
                        'transition-colors',
                        r.exc_info ? 'cursor-pointer hover:bg-gray-800/60' : 'hover:bg-gray-800/30',
                        r.level === 'ERROR' || r.level === 'CRITICAL' ? 'bg-red-950/10' : '',
                        r.level === 'WARNING' ? 'bg-yellow-950/10' : '',
                      )}
                    >
                      <td className="px-4 py-1.5 text-gray-500 whitespace-nowrap">{fmtTs(r.ts, timezone)}</td>
                      <td className="px-3 py-1.5">
                        <span className={clsx('inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs', LEVEL_STYLES[r.level] ?? '')}>
                          <span className={clsx('w-1 h-1 rounded-full', LEVEL_DOT[r.level] ?? 'bg-gray-400')} />
                          {r.level}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-gray-400 truncate max-w-[9rem]" title={r.logger}>
                        {shortLogger(r.logger)}
                      </td>
                      <td className="px-3 py-1.5 text-gray-200 max-w-0">
                        <div className="flex items-center gap-2">
                          <span className="truncate">{r.message}</span>
                          {r.exc_info && (
                            <span className="flex-shrink-0 text-red-400 opacity-60 text-xs">▼</span>
                          )}
                        </div>
                      </td>
                    </tr>
                    {r.exc_info && expanded === r.id && (
                      <tr key={`${r.id}-exc`} className="bg-red-950/20">
                        <td colSpan={4} className="px-4 py-2">
                          <pre className="text-xs text-red-300 whitespace-pre-wrap break-all leading-relaxed">
                            {r.exc_info}
                          </pre>
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Footer */}
        {records.length > 0 && (
          <div className="px-4 py-2 border-t border-gray-800 flex items-center justify-between text-xs text-gray-500">
            <span>
              Showing {((page - 1) * PAGE_SIZE + 1).toLocaleString()}–{((page - 1) * PAGE_SIZE + records.length).toLocaleString()} of {total.toLocaleString()} records (newest first)
            </span>
            {autoRefresh && (
              <span className="flex items-center gap-1.5 text-blue-400">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                Live — refreshing every 5s
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
