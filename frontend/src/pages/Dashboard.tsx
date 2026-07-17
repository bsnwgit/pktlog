/**
 * Dashboard — real syslog analytics.
 * Calls /api/syslog/stats and /api/syslog/timeseries.
 */
import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
} from 'recharts'
import { api, SyslogStats, SyslogTimeseriesPoint } from '../api/client'
import HelpButton from '../components/HelpButton'
import { useAutoRefresh } from '../store/autoRefresh'
import { useTimezone } from '../hooks/useTimezone'

// ── Constants ─────────────────────────────────────────────────────────────────

const WINDOWS = [
  { label: '1h',  hours: 1,  bucket: 5  },
  { label: '6h',  hours: 6,  bucket: 15 },
  { label: '24h', hours: 24, bucket: 60 },
  { label: '7d',  hours: 168, bucket: 360 },
]

const SEV_CONFIG: Record<string, { color: string; bg: string; label: string }> = {
  emergency: { color: 'text-red-400',    bg: 'bg-red-900/30 border-red-700/50',    label: 'Emergency' },
  alert:     { color: 'text-red-400',    bg: 'bg-red-900/20 border-red-700/40',    label: 'Alert'     },
  critical:  { color: 'text-orange-400', bg: 'bg-orange-900/30 border-orange-700/50', label: 'Critical'  },
  error:     { color: 'text-orange-300', bg: 'bg-orange-900/20 border-orange-700/30', label: 'Error'     },
  warning:   { color: 'text-yellow-400', bg: 'bg-yellow-900/20 border-yellow-700/30', label: 'Warning'   },
  notice:    { color: 'text-blue-400',   bg: 'bg-blue-900/20 border-blue-700/30',   label: 'Notice'    },
  info:      { color: 'text-green-400',  bg: 'bg-green-900/20 border-green-700/30', label: 'Info'      },
  debug:     { color: 'text-gray-400',   bg: 'bg-gray-800/50 border-gray-700/30',   label: 'Debug'     },
}

function collectorDot(lastSeen: string | null): string {
  if (!lastSeen) return 'bg-gray-500'
  const ageMin = (Date.now() - new Date(lastSeen).getTime()) / 60_000
  if (ageMin < 5)  return 'bg-green-400'
  if (ageMin < 30) return 'bg-yellow-400'
  if (ageMin < 24 * 60) return 'bg-red-400'
  return 'bg-red-600'
}

function fmtLastSeen(lastSeen: string | null, timeZone: string): string {
  if (!lastSeen) return 'No data'
  return new Date(lastSeen).toLocaleString([], {
    timeZone,
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtBucket(iso: string, hours: number, timeZone: string): string {
  const d = new Date(iso)
  if (hours <= 6)  return d.toLocaleTimeString([], { timeZone, hour: '2-digit', minute: '2-digit' })
  if (hours <= 24) return d.toLocaleTimeString([], { timeZone, hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString([], { timeZone, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatCard({ name, count, cfg }: { name: string; count: number; cfg: typeof SEV_CONFIG[string] }) {
  return (
    <div className={`rounded-xl border px-4 py-3 flex items-center justify-between ${cfg.bg}`}>
      <span className={`text-sm font-medium capitalize ${cfg.color}`}>{cfg.label}</span>
      <span className={`text-xl font-bold tabular-nums ${cfg.color}`}>
        {count.toLocaleString()}
      </span>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate()
  const [windowIdx, setWindowIdx] = useState(1) // default: 6h
  const win = WINDOWS[windowIdx]

  const [stats, setStats] = useState<SyslogStats | null>(null)
  const [series, setSeries] = useState<SyslogTimeseriesPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const timezone = useTimezone()

  const { enabled: autoRefresh, intervalSec } = useAutoRefresh()

  const load = useCallback(async () => {
    try {
      const [s, ts] = await Promise.all([
        api.getSyslogStats(win.hours),
        api.getSyslogTimeseries(win.hours, win.bucket),
      ])
      setStats(s)
      setSeries(ts)
      setError('')
    } catch (e: any) {
      setError(e.message ?? 'Failed to load stats')
    } finally {
      setLoading(false)
    }
  }, [win.hours, win.bucket])

  useEffect(() => { setLoading(true); load() }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(load, intervalSec * 1000)
    return () => clearInterval(id)
  }, [autoRefresh, intervalSec, load])

  // Build severity cards in order 0→7
  const sevMap: Record<string, number> = {}
  stats?.count_by_severity.forEach(s => { sevMap[s.severity_name] = s.count })
  const sevOrder = ['emergency', 'alert', 'critical', 'error', 'warning', 'notice', 'info', 'debug']

  const chartData = series.map(p => ({
    t: fmtBucket(p.bucket, win.hours, timezone),
    count: p.count,
  }))

  const totalEvents = stats?.count_by_severity.reduce((s, r) => s + r.count, 0) ?? 0

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-white">Dashboard</h1>
            <HelpButton title="Dashboard — How It Works">
              <p>Severity stat cards, the events-over-time chart, and top hosts are all scoped to the window picker at top right — switching windows re-queries everything on this page at once.</p>
              <p>"Open Explorer →" on the Event Volume chart jumps to Syslog Explorer unfiltered — the severity cards above it are counts only, not clickable filters.</p>
            </HelpButton>
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            {totalEvents.toLocaleString()} events · last {win.label}
          </p>
        </div>
        <div className="flex items-center gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
          {WINDOWS.map((w, i) => (
            <button
              key={w.label}
              onClick={() => setWindowIdx(i)}
              className={`px-3 py-1 text-xs rounded-md transition-colors ${
                i === windowIdx
                  ? 'bg-blue-600 text-white font-medium'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-700/30 rounded-xl px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Severity cards */}
      {loading && !stats ? (
        <div className="grid grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="rounded-xl border border-gray-800 bg-gray-900 h-14 animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-4 gap-3">
          {sevOrder.map(name => {
            const cfg = SEV_CONFIG[name] ?? SEV_CONFIG.debug
            const count = sevMap[name] ?? 0
            return <StatCard key={name} name={name} count={count} cfg={cfg} />
          })}
        </div>
      )}

      {/* Timeseries chart */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl px-5 pt-4 pb-3">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-white">Event Volume</h2>
          <button
            onClick={() => navigate('/explorer')}
            className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
          >
            Open Explorer →
          </button>
        </div>
        {chartData.length === 0 && !loading ? (
          <div className="h-40 flex items-center justify-center text-gray-600 text-sm">
            No events in this window
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={160}>
            <AreaChart data={chartData} margin={{ top: 2, right: 4, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="ev-grad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}   />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="t" tick={{ fill: '#6b7280', fontSize: 11 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip
                contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: '8px', fontSize: '12px' }}
                labelStyle={{ color: '#9ca3af' }}
                itemStyle={{ color: '#60a5fa' }}
              />
              <Area type="monotone" dataKey="count" stroke="#3b82f6" strokeWidth={2} fill="url(#ev-grad)" dot={false} name="Events" />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Bottom row: top hosts + collector status */}
      <div className="grid grid-cols-2 gap-5">
        {/* Top hosts */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800">
            <h2 className="text-sm font-semibold text-white">Top Sources</h2>
          </div>
          <div className="divide-y divide-gray-800">
            {loading && !stats ? (
              Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="px-5 py-2.5 h-10 animate-pulse bg-gray-800/30" />
              ))
            ) : stats?.top_hosts.slice(0, 5).length === 0 ? (
              <div className="px-5 py-4 text-sm text-gray-600">No data</div>
            ) : (
              stats?.top_hosts.slice(0, 5).map((h, i) => (
                <div key={i} className="px-5 py-2.5 flex items-center justify-between">
                  <div className="min-w-0">
                    <p className="text-sm text-white truncate font-mono">
                      {h.source_name || h.source_ip}
                    </p>
                    {h.source_name && (
                      <p className="text-xs text-gray-500 font-mono">{h.source_ip}</p>
                    )}
                  </div>
                  <span className="text-sm text-gray-300 tabular-nums ml-4 flex-shrink-0">
                    {h.count.toLocaleString()}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Collector status */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800">
            <h2 className="text-sm font-semibold text-white">Collector Status</h2>
          </div>
          <div className="divide-y divide-gray-800">
            {loading && !stats ? (
              Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="px-5 py-2.5 h-12 animate-pulse bg-gray-800/30" />
              ))
            ) : stats?.collector_last_seen.length === 0 ? (
              <div className="px-5 py-4 text-sm text-gray-600">No collectors registered</div>
            ) : (
              stats?.collector_last_seen.map((c, i) => (
                <div key={i} className="px-5 py-3 flex items-center gap-3">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${collectorDot(c.last_seen)}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white truncate">
                      {c.collector_name || c.collector_ip}
                    </p>
                    <p className="text-xs text-gray-500 font-mono">{c.collector_ip}</p>
                  </div>
                  <span className="text-xs text-gray-400 flex-shrink-0">{fmtLastSeen(c.last_seen, timezone)}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
