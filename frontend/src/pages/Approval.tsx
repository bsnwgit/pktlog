/**
 * Approval — the queue of syslog senders waiting to be let in.
 *
 * Deliberately a top-level page rather than a Settings tab: Settings goes
 * read-only whenever pktHub manages this app, which would force a managed
 * install over to pktHub just to admit a device. Admitting a device is an
 * operational act, not a configuration one.
 */
import React, { useState, useEffect, useCallback } from 'react'
import { api, PendingCollector, CollectorIn } from '../api/client'
import HelpButton from '../components/HelpButton'
import IpLink from '../components/IpLink'

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!value)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${value ? 'bg-blue-600' : 'bg-gray-700'}`}
    >
      <span className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${value ? 'translate-x-6' : 'translate-x-1'}`} />
    </button>
  )
}

// Relative age reads better than a timestamp here — what matters is whether a
// sender is still active, not the exact moment it first appeared.
function ago(iso: string): string {
  const secs = Math.max(0, (Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).getTime()) / 1000)
  if (secs < 60)    return `${Math.floor(secs)}s ago`
  if (secs < 3600)  return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

type ApprovalForm = Required<CollectorIn>

const EMPTY_FORM: ApprovalForm = {
  collector_ip: '', collector_name: '', org: '', log_group: '', site: '', notes: '', enabled: true,
}

export default function Approval() {
  const [pending, setPending] = useState<PendingCollector[]>([])
  const [showIgnored, setShowIgnored] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState('')
  const [approving, setApproving] = useState<string | null>(null)
  const [form, setForm]       = useState<ApprovalForm>(EMPTY_FORM)
  const [saving, setSaving]   = useState(false)

  const load = useCallback(async () => {
    try {
      setPending(await api.getPendingCollectors(showIgnored))
      setError('')
    } catch (e: any) {
      setError(e.message ?? 'Could not load the approval queue')
    } finally {
      setLoading(false)
    }
  }, [showIgnored])

  // Ingest folds its counters in on a 60s tick, so anything faster than that
  // would just re-render the same numbers.
  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [load])

  const startApprove = (p: PendingCollector) => {
    setApproving(p.collector_ip)
    setForm({ ...EMPTY_FORM, collector_ip: p.collector_ip })
    setError('')
  }

  const submitApprove = async () => {
    if (!form.collector_name.trim()) { setError('A name is required'); return }
    setSaving(true)
    try {
      await api.approveCollector(form)
      setApproving(null)
      setForm(EMPTY_FORM)
      await load()
    } catch (e: any) {
      setError(e.message ?? 'Approval failed')
    } finally {
      setSaving(false)
    }
  }

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); await load() }
    catch (e: any) { setError(e.message ?? 'Action failed') }
  }

  const InputCls = 'w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500'
  const waiting = pending.filter(p => !p.ignored).length

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="text-xl font-bold text-white">Approval</h1>
        {waiting > 0 && (
          <span className="px-2 py-0.5 text-xs rounded-full bg-amber-900/40 text-amber-300 border border-amber-700/40">
            {waiting} waiting
          </span>
        )}
        <HelpButton title="Approval — How It Works">
          <p>pktLog stores syslog only from a <span className="text-gray-300 font-medium">registered, enabled collector</span>. Anything else is dropped at ingest — so a device can be sending perfectly good syslog on the wire and appear nowhere in the app until it is approved here.</p>
          <p>This queue is what those dropped senders look like. <span className="text-gray-300 font-medium">Approve</span> creates the collector registry entry, which is what actually admits the data; from that moment its messages are stored and the sender leaves the queue.</p>
          <p><span className="text-gray-300 font-medium">Ignore</span> hides a sender without admitting it — its messages keep being dropped and its counter keeps rising, so a chatty device you don't want can't bury the ones you do. <span className="text-gray-300 font-medium">Forget</span> removes the row outright; it reappears if the device sends again, so neither action is a block.</p>
          <p>Counts are folded in from ingest roughly once a minute, so a sender that has just started may take a moment to appear, and its total lags slightly behind the wire.</p>
          <p>Approving is admin-only, and lives here rather than under Settings so it keeps working when pktHub manages this app — a managed install would otherwise have to go to pktHub to admit a device. Editing collectors you have already approved is still <span className="text-gray-300 font-medium">Settings → Collectors</span>.</p>
        </HelpButton>
      </div>

      <div className="flex items-center justify-between flex-wrap gap-2">
        <p className="text-xs text-gray-500">
          Senders seen at ingest that aren't in the collector registry. Their messages are being
          dropped until approved.
        </p>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-white transition-colors cursor-pointer select-none">
            <input
              type="checkbox"
              checked={showIgnored}
              onChange={e => setShowIgnored(e.target.checked)}
              className="w-3.5 h-3.5 rounded border-gray-700 bg-gray-800 text-blue-600 focus:ring-0 focus:ring-offset-0 cursor-pointer"
            />
            Show ignored
          </label>
          <button
            onClick={load}
            className="px-3 py-1.5 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <table className="f-tbl-cards w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-500">
              <th className="px-4 py-3 text-left font-medium w-40">Sender IP</th>
              <th className="px-3 py-3 text-left font-medium w-28">First seen</th>
              <th className="px-3 py-3 text-left font-medium w-28">Last seen</th>
              <th className="px-3 py-3 text-right font-medium w-28">Dropped</th>
              <th className="px-3 py-3 text-left font-medium">Sample message</th>
              <th className="px-3 py-3 text-right font-medium w-56"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {loading && pending.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-600">Loading…</td></tr>
            )}

            {!loading && pending.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-sm text-gray-600">
                  Nothing waiting for approval — every sender pktLog has seen is registered.
                </td>
              </tr>
            )}

            {pending.map(p => (
              <React.Fragment key={p.collector_ip}>
                <tr className={p.ignored ? 'opacity-50' : undefined}>
                  <td data-label="Sender IP" className="px-4 py-2.5 font-mono text-xs text-gray-300">
                    <IpLink ip={p.collector_ip} />
                    {!!p.ignored && <span className="ml-2 text-[10px] text-gray-500 uppercase">ignored</span>}
                  </td>
                  <td data-label="First seen" className="px-3 py-2.5 text-xs text-gray-500 whitespace-nowrap">{ago(p.first_seen)}</td>
                  <td data-label="Last seen" className="px-3 py-2.5 text-xs text-gray-400 whitespace-nowrap">{ago(p.last_seen)}</td>
                  <td data-label="Dropped" className="px-3 py-2.5 text-xs text-gray-400 text-right tabular-nums">
                    {p.message_count.toLocaleString()}
                  </td>
                  <td data-label="Sample message" className="px-3 py-2.5 text-xs text-gray-500 max-w-0">
                    <p className="truncate font-mono">{p.sample_message || '—'}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center justify-end gap-2">
                      {approving !== p.collector_ip && (
                        <button
                          onClick={() => startApprove(p)}
                          className="text-xs bg-blue-600 hover:bg-blue-500 text-white rounded px-3 py-1.5 transition-colors"
                        >
                          Approve
                        </button>
                      )}
                      {p.ignored ? (
                        <button
                          onClick={() => act(() => api.unignorePendingCollector(p.collector_ip))}
                          className="text-xs border border-gray-700 text-gray-400 hover:text-white rounded px-3 py-1.5 transition-colors"
                        >
                          Unignore
                        </button>
                      ) : (
                        <button
                          onClick={() => act(() => api.ignorePendingCollector(p.collector_ip))}
                          className="text-xs border border-gray-700 text-gray-400 hover:text-white rounded px-3 py-1.5 transition-colors"
                        >
                          Ignore
                        </button>
                      )}
                      <button
                        onClick={() => act(() => api.forgetPendingCollector(p.collector_ip))}
                        title="Remove from the queue — it returns if the device sends again"
                        className="text-xs text-gray-600 hover:text-red-400 transition-colors px-1"
                      >
                        ✕
                      </button>
                    </div>
                  </td>
                </tr>

                {approving === p.collector_ip && (
                  <tr className="bg-gray-800/40">
                    <td colSpan={6} className="px-4 py-3">
                      <div className="flex items-end gap-3 flex-wrap">
                        <div className="w-40">
                          <label className="block text-[10px] uppercase text-gray-500 mb-1">Name</label>
                          <input autoFocus value={form.collector_name}
                            onChange={e => setForm(f => ({ ...f, collector_name: e.target.value }))}
                            placeholder="collector name" className={InputCls} />
                        </div>
                        <div className="w-32">
                          <label className="block text-[10px] uppercase text-gray-500 mb-1">Org</label>
                          <input value={form.org} onChange={e => setForm(f => ({ ...f, org: e.target.value }))}
                            placeholder="org name" className={InputCls} />
                        </div>
                        <div className="w-32">
                          <label className="block text-[10px] uppercase text-gray-500 mb-1">Log Group</label>
                          <input value={form.log_group} onChange={e => setForm(f => ({ ...f, log_group: e.target.value }))}
                            placeholder="Collector-A" className={InputCls} />
                        </div>
                        <div className="w-32">
                          <label className="block text-[10px] uppercase text-gray-500 mb-1">Site</label>
                          <input value={form.site} onChange={e => setForm(f => ({ ...f, site: e.target.value }))}
                            placeholder="optional" className={InputCls} />
                        </div>
                        <div className="flex-1 min-w-40">
                          <label className="block text-[10px] uppercase text-gray-500 mb-1">Notes</label>
                          <input value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                            placeholder="optional" className={InputCls} />
                        </div>
                        <div>
                          <label className="block text-[10px] uppercase text-gray-500 mb-1">Enabled</label>
                          <Toggle value={form.enabled} onChange={v => setForm(f => ({ ...f, enabled: v }))} />
                        </div>
                        <div className="flex gap-2">
                          <button onClick={submitApprove} disabled={saving}
                            className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded px-4 py-1.5 transition-colors">
                            {saving ? 'Approving…' : 'Approve'}
                          </button>
                          <button onClick={() => { setApproving(null); setForm(EMPTY_FORM); setError('') }}
                            className="text-xs border border-gray-700 text-gray-400 hover:text-white rounded px-3 py-1.5 transition-colors">
                            Cancel
                          </button>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
