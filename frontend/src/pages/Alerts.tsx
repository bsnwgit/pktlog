import { useEffect, useState, useCallback, useRef } from 'react'
import { api, AlertRule, AlertEvent, getToken } from '../api/client'
import { useWebSocket, type WsMessage, type AlertFiredPayload } from '../hooks/useWebSocket'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(ts: string): string {
  return new Date(ts).toLocaleString([], {
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
  Traffic:        'bg-sky-500/15 text-sky-400 border border-sky-500/30',
  Security:       'bg-orange-500/15 text-orange-400 border border-orange-500/30',
  Infrastructure: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30',
}

const CHANNELS_AVAILABLE = ['inapp', 'slack', 'email', 'pagerduty', 'webhook', 'tracecat']

const RULE_TYPES: Array<{ value: string; label: string; group: string; hint: string }> = [
  // Traffic thresholds
  { value: 'threshold',          label: 'Threshold',             group: 'Traffic',        hint: 'Fire when a traffic metric exceeds a fixed value' },
  { value: 'rate_spike',         label: 'Rate spike',            group: 'Traffic',        hint: 'Fire when traffic spikes relative to a rolling baseline' },
  { value: 'top_talker',         label: 'Top talker',            group: 'Traffic',        hint: 'Fire when a single IP dominates traffic in the window' },
  { value: 'elephant_flow',      label: 'Elephant flow',         group: 'Traffic',        hint: 'Fire when individual flows carry an unusually large byte count' },
  { value: 'inter_site_traffic', label: 'Inter-site traffic',    group: 'Traffic',        hint: 'Fire on excessive cross-site traffic volume' },
  // Security / anomaly
  { value: 'port_protocol',      label: 'Port / protocol',       group: 'Security',       hint: 'Fire on any traffic observed on a specific port' },
  { value: 'connection_burst',   label: 'Connection burst',      group: 'Security',       hint: 'Fire when one IP makes an abnormal number of connections' },
  { value: 'port_scan',          label: 'Port scan',             group: 'Security',       hint: 'Fire when one IP contacts too many distinct destination ports' },
  { value: 'internal_spread',    label: 'Internal spread',       group: 'Security',       hint: 'Fire when one IP reaches too many distinct destinations' },
  { value: 'protocol_anomaly',   label: 'Protocol anomaly',      group: 'Security',       hint: 'Fire when unexpected protocol appears on a well-known port' },
  // Infrastructure
  { value: 'data_gap',           label: 'Data gap',              group: 'Infrastructure', hint: 'Fire when a known sampler stops sending flows' },
  { value: 'new_host',           label: 'New host',              group: 'Infrastructure', hint: 'Fire when an unrecognized device sends NetFlow data' },
  { value: 'ingest_rate_low',    label: 'Ingest rate low',       group: 'Infrastructure', hint: 'Fire when the overall ingest rate drops below a minimum' },
  { value: 'clickhouse_size',    label: 'ClickHouse table size', group: 'Infrastructure', hint: 'Fire when a ClickHouse table exceeds a storage threshold' },
]

function ruleGroup(rule_type: string): string {
  return RULE_TYPES.find(r => r.value === rule_type)?.group ?? 'Other'
}

const RULE_DEFAULTS: Record<string, { name: string; description: string }> = {
  threshold:          { name: 'Traffic threshold',         description: 'Alert when total traffic volume exceeds a set threshold' },
  rate_spike:         { name: 'Traffic rate spike',        description: 'Alert when traffic spikes above the rolling baseline by a multiplier' },
  top_talker:         { name: 'Top talker threshold',      description: 'Alert when a single source IP dominates traffic in the eval window' },
  elephant_flow:      { name: 'Elephant flow detected',    description: 'Alert when individual flows exceed a large-byte threshold' },
  inter_site_traffic: { name: 'Inter-site traffic spike',  description: 'Alert when cross-site traffic volume exceeds a threshold' },
  port_protocol:      { name: 'Port / protocol traffic',   description: 'Alert on any traffic observed on a specific port and protocol' },
  connection_burst:   { name: 'Connection burst',          description: 'Alert when a single IP makes an abnormal number of connections' },
  port_scan:          { name: 'Port scan detected',        description: 'Alert when a source IP hits an unusual number of distinct destination ports' },
  internal_spread:    { name: 'Internal spread detected',  description: 'Alert when a source IP reaches an unusual number of distinct destinations' },
  protocol_anomaly:   { name: 'Protocol anomaly',          description: 'Alert when unexpected protocol traffic is seen on a well-known port' },
  data_gap:           { name: 'Collector data gap',        description: 'Alert when a known sampler stops sending flows for a set period' },
  new_host:           { name: 'Unknown sampler detected',  description: 'Alert when an unrecognized host sends NetFlow data' },
  ingest_rate_low:    { name: 'Ingest rate too low',       description: 'Alert when the overall flow ingest rate drops below a minimum' },
  clickhouse_size:    { name: 'ClickHouse table too large', description: 'Alert when a ClickHouse table exceeds a storage size threshold' },
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

const METRIC_OPTS = [
  { value: 'bytes',   label: 'Bytes' },
  { value: 'packets', label: 'Packets' },
  { value: 'flows',   label: 'Flows' },
]

const OPERATOR_OPTS = [
  { value: 'gt',  label: '> greater than' },
  { value: 'gte', label: '≥ at least' },
  { value: 'lt',  label: '< less than' },
  { value: 'lte', label: '≤ at most' },
]

const PROTO_OPTS = [
  { value: 'any', label: 'Any protocol' },
  { value: 'TCP', label: 'TCP' },
  { value: 'UDP', label: 'UDP' },
  { value: 'ICMP', label: 'ICMP' },
]

const DIR_OPTS = [
  { value: 'dst', label: 'Destination port' },
  { value: 'src', label: 'Source port' },
  { value: 'any', label: 'Either direction' },
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
        <Field label="Silence threshold (minutes)" hint="Alert if a sampler sends no flows for this long">
          <TextInput type="number" value={String(g('silence_minutes', 10))} onChange={v => set('silence_minutes', parseInt(v) || 10)} />
        </Field>
      </div>
    )
  }

  if (ruleType === 'new_host') {
    return (
      <p className="text-xs text-gray-500 italic">No conditions — fires whenever an unrecognized sampler sends NetFlow data.</p>
    )
  }

  if (ruleType === 'threshold') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Metric">
          <SelectInput value={String(g('metric', 'bytes'))} onChange={v => set('metric', v)} options={METRIC_OPTS} />
        </Field>
        <Field label="Operator">
          <SelectInput value={String(g('operator', 'gt'))} onChange={v => set('operator', v)} options={OPERATOR_OPTS} />
        </Field>
        <Field label="Threshold value" hint="Bytes, packet count, or flow count depending on metric">
          <TextInput type="number" value={String(g('value', 0))} onChange={v => set('value', parseFloat(v) || 0)} />
        </Field>
        <Field label="Sampler IP (optional)" hint="Leave blank to check all samplers">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'rate_spike') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Metric">
          <SelectInput value={String(g('metric', 'bytes'))} onChange={v => set('metric', v)} options={METRIC_OPTS} />
        </Field>
        <Field label="Spike multiplier" hint="Alert when current rate is this many times the baseline">
          <TextInput type="number" value={String(g('multiplier', 3))} onChange={v => set('multiplier', parseFloat(v) || 3)} />
        </Field>
        <Field label="Baseline period (days)" hint="Rolling average over this many days">
          <TextInput type="number" value={String(g('baseline_days', 7))} onChange={v => set('baseline_days', parseInt(v) || 7)} />
        </Field>
        <Field label="Sampler IP (optional)">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'port_protocol') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Port number">
          <TextInput type="number" value={String(g('port', ''))} onChange={v => set('port', parseInt(v) || 0)} placeholder="e.g. 22" />
        </Field>
        <Field label="Protocol">
          <SelectInput value={String(g('protocol', 'any'))} onChange={v => set('protocol', v)} options={PROTO_OPTS} />
        </Field>
        <Field label="Direction">
          <SelectInput value={String(g('direction', 'dst'))} onChange={v => set('direction', v)} options={DIR_OPTS} />
        </Field>
        <Field label="Sampler IP (optional)">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'top_talker') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Metric">
          <SelectInput value={String(g('metric', 'bytes'))} onChange={v => set('metric', v)} options={METRIC_OPTS} />
        </Field>
        <Field label="Threshold" hint="Alert when any single IP exceeds this value in the eval window">
          <TextInput type="number" value={String(g('threshold', 0))} onChange={v => set('threshold', parseFloat(v) || 0)} />
        </Field>
        <Field label="Sampler IP (optional)">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'elephant_flow') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Min flow size (MB)" hint="Alert when any single flow record exceeds this size">
          <TextInput type="number" value={String(g('threshold_mb', 100))} onChange={v => set('threshold_mb', parseFloat(v) || 100)} placeholder="e.g. 100" />
        </Field>
        <Field label="Sampler IP (optional)">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'inter_site_traffic') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Metric">
          <SelectInput value={String(g('metric', 'bytes'))} onChange={v => set('metric', v)} options={METRIC_OPTS} />
        </Field>
        <Field label="Threshold" hint="Alert when inter-site traffic exceeds this in the eval window">
          <TextInput type="number" value={String(g('threshold', 0))} onChange={v => set('threshold', parseFloat(v) || 0)} />
        </Field>
        <Field label="Site A (optional)" hint="e.g. medical — leave blank to match all sites">
          <TextInput value={String(g('site_a', ''))} onChange={v => set('site_a', v)} placeholder="e.g. medical" />
        </Field>
        <Field label="Site B (optional)" hint="e.g. dental — leave blank to match all sites">
          <TextInput value={String(g('site_b', ''))} onChange={v => set('site_b', v)} placeholder="e.g. dental" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'connection_burst') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Max connections" hint="Alert when any single IP makes more connections than this in the eval window">
          <TextInput type="number" value={String(g('threshold_connections', 1000))} onChange={v => set('threshold_connections', parseInt(v) || 1000)} />
        </Field>
        <Field label="Sampler IP (optional)">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'port_scan') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Distinct port threshold" hint="Alert when any single src_ip hits more than this many unique dst ports in the eval window">
          <TextInput type="number" value={String(g('threshold_ports', 50))} onChange={v => set('threshold_ports', parseInt(v) || 50)} />
        </Field>
        <Field label="Sampler IP (optional)">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'internal_spread') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Distinct destination threshold" hint="Alert when any single src_ip reaches more than this many unique dst IPs">
          <TextInput type="number" value={String(g('threshold_ips', 30))} onChange={v => set('threshold_ips', parseInt(v) || 30)} />
        </Field>
        <Field label="Source subnet filter (optional)" hint="Only check src IPs within this CIDR, e.g. 10.0.0.0/8">
          <TextInput value={String(g('src_subnet', ''))} onChange={v => set('src_subnet', v)} placeholder="e.g. 10.0.0.0/8" />
        </Field>
        <Field label="Sampler IP (optional)">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'protocol_anomaly') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Port number" hint="Well-known port to monitor, e.g. 22 (SSH), 53 (DNS), 443 (HTTPS)">
          <TextInput type="number" value={String(g('port', ''))} onChange={v => set('port', parseInt(v) || 0)} placeholder="e.g. 22" />
        </Field>
        <Field label="Expected protocol" hint="Flows using any other protocol will trigger the alert">
          <SelectInput value={String(g('expected_proto', 'TCP'))} onChange={v => set('expected_proto', v)}
            options={[{ value: 'TCP', label: 'TCP' }, { value: 'UDP', label: 'UDP' }, { value: 'ICMP', label: 'ICMP' }]} />
        </Field>
        <Field label="Direction">
          <SelectInput value={String(g('direction', 'dst'))} onChange={v => set('direction', v)} options={DIR_OPTS} />
        </Field>
        <Field label="Sampler IP (optional)">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
        </Field>
      </div>
    )
  }

  if (ruleType === 'ingest_rate_low') {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Minimum flows/sec" hint="Alert when ingest rate falls below this threshold">
          <TextInput type="number" value={String(g('min_flows_per_sec', 1))} onChange={v => set('min_flows_per_sec', parseFloat(v) || 1)} placeholder="e.g. 10" />
        </Field>
        <Field label="Sampler IP (optional)" hint="Leave blank to check total ingest across all samplers">
          <TextInput value={String(g('sampler_ip', ''))} onChange={v => set('sampler_ip', v)} placeholder="e.g. 10.20.30.11" />
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
          <TextInput value={String(g('table', 'flows'))} onChange={v => set('table', v)} placeholder="flows" />
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

function fmtBytes(b: number): string {
  if (b >= 1_000_000_000) return `${(b/1_000_000_000).toFixed(1)} GB`
  if (b >= 1_000_000) return `${(b/1_000_000).toFixed(1)} MB`
  if (b >= 1_000) return `${(b/1_000).toFixed(1)} KB`
  return `${b} B`
}

type Contributor  = { src_ip: string; dst_ip: string; site?: string; sampler_ip?: string; value: number }
type TopSource    = { src_ip: string; dst_ip?: string; protocol?: string; sampler_ip?: string; flow_count?: number; value?: number }
type TopFlow      = { src_ip: string; dst_ip: string; bytes: number; protocol: string }
type TopDst       = { dst_ip: string; flow_count: number; bytes: number }
type SamplePort   = { dst_port: number; protocol: string; flow_count: number }

function MiniTable({ title, headers, rows }: {
  title: string
  headers: string[]
  rows: (string | number)[][]
}) {
  if (!rows.length) return null
  return (
    <div>
      <p className="text-xs text-gray-500 mb-1.5 uppercase tracking-wide">{title}</p>
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
                  {typeof cell === 'string' ? cell : cell.toLocaleString()}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DetailsPanel({ details }: { details: DetailMap }) {
  const contributors   = details.top_contributors as Contributor[]  | undefined
  const topSources     = details.top_sources      as TopSource[]    | undefined
  const topFlows       = details.top_flows        as TopFlow[]      | undefined
  const topDsts        = details.top_destinations as TopDst[]       | undefined
  const samplePorts    = details.sample_ports     as SamplePort[]   | undefined

  const TABLE_KEYS = new Set(['top_contributors','top_sources','top_flows','top_destinations','sample_ports'])
  const CHIP_IP_KEYS  = ['src_ip','sampler_ip']
  const CHIP_SITE_KEYS = ['site_a','site_b']
  const META_SKIP = new Set([...CHIP_IP_KEYS, ...CHIP_SITE_KEYS, ...TABLE_KEYS])

  const chips: [string,string,string][] = []
  CHIP_IP_KEYS.forEach(k => {
    const v = details[k] as string | undefined
    if (v && v !== 'all') chips.push([k, k === 'src_ip' ? 'Source IP' : 'Sampler', v])
  })
  CHIP_SITE_KEYS.forEach(k => {
    const v = details[k] as string | undefined
    if (v && v !== 'any') chips.push([k, k === 'site_a' ? 'Site A' : 'Site B', v])
  })

  const kvs = Object.entries(details).filter(([k]) => !META_SKIP.has(k))

  // Build top_sources table rows — handles both {src_ip, value} and {src_ip, dst_ip, protocol, sampler_ip, flow_count}
  const topSourceHasExtra = topSources && topSources[0] && ('dst_ip' in topSources[0] || 'protocol' in topSources[0])
  const topSourceHeaders = topSourceHasExtra
    ? ['Source', 'Destination', 'Proto', 'Sampler', 'Flows']
    : ['Source IP', 'Volume']
  const topSourceRows = (topSources || []).map(s =>
    topSourceHasExtra
      ? [s.src_ip, s.dst_ip ?? '—', s.protocol ?? '—', s.sampler_ip ?? '—', s.flow_count ?? 0]
      : [s.src_ip, s.value ?? 0]
  )

  // Build contributors table rows (inter-site)
  const contribHasSampler = contributors && contributors[0]?.sampler_ip !== undefined
  const contribHeaders = ['Source', 'Destination', 'Site', ...(contribHasSampler ? ['Sampler'] : []), 'Volume']
  const contribRows = (contributors || []).map(c => [
    c.src_ip, c.dst_ip, c.site ?? '—', ...(contribHasSampler ? [c.sampler_ip ?? '—'] : []), c.value
  ])

  return (
    <div className="mt-3 space-y-3">
      {/* Chips */}
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {chips.map(([k, label, v]) => (
            <span key={k} className="text-xs bg-blue-500/15 text-blue-300 border border-blue-500/25 px-2.5 py-0.5 rounded-full">
              <span className="text-blue-500/70 mr-1">{label}</span>{v}
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

      {/* Top contributors (inter-site) */}
      <MiniTable title="Top contributors" headers={contribHeaders} rows={contribRows} />

      {/* Top sources (threshold / rate_spike / port_protocol / protocol_anomaly) */}
      <MiniTable title="Top sources" headers={topSourceHeaders} rows={topSourceRows} />

      {/* Largest flows (elephant flow) */}
      <MiniTable
        title="Largest flows"
        headers={['Source', 'Destination', 'Proto', 'Bytes']}
        rows={(topFlows || []).map(f => [f.src_ip, f.dst_ip, f.protocol, fmtBytes(f.bytes)])}
      />

      {/* Top destinations (connection burst) */}
      <MiniTable
        title="Top destinations"
        headers={['Destination IP', 'Flows', 'Bytes']}
        rows={(topDsts || []).map(d => [d.dst_ip, d.flow_count, fmtBytes(d.bytes)])}
      />

      {/* Sample ports (port scan) */}
      <MiniTable
        title="Ports scanned (sample)"
        headers={['Dst Port', 'Proto', 'Flows']}
        rows={(samplePorts || []).map(p => [String(p.dst_port), p.protocol, p.flow_count])}
      />
    </div>
  )
}

// ── Alert event card ──────────────────────────────────────────────────────────
function EventCard({ event, onAck }: { event: AlertEvent; onAck: (id: number) => void }) {
  const [expanded, setExpanded] = useState(false)
  const isAcked     = Boolean(event.acked_at)
  const isResolved  = Boolean(event.resolved_at) && !isAcked
  const hasDetails  = Object.keys(event.details).length > 0

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
            <p className="text-sm text-white mt-0.5">{event.message}</p>
            {isResolved && (
              <p className="text-xs text-green-500/70 mt-0.5">Resolved {fmtTime(event.resolved_at!)}</p>
            )}
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          <span className="text-xs text-white">{fmtTime(event.fired_at)}</span>
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

// ── Main page ─────────────────────────────────────────────────────────────────
type Tab = 'active' | 'history' | 'rules'

export default function Alerts() {
  const [tab, setTab]               = useState<Tab>('active')
  const [events, setEvents]         = useState<AlertEvent[]>([])
  const [history, setHistory]       = useState<AlertEvent[]>([])
  const [rules, setRules]           = useState<AlertRule[]>([])
  const [loading, setLoading]       = useState(false)
  const [editRule, setEditRule]     = useState<AlertRule | null>(null)
  const [addingRule, setAddingRule] = useState(false)
  const [saving, setSaving]         = useState(false)
  const [error, setError]           = useState('')
  const [rulesFilter, setRulesFilter]       = useState('')
  const [rulesTopicFilter, setRulesTopicFilter] = useState('')
  const [rulesSortKey, setRulesSortKey]     = useState<keyof AlertRule | 'topic' | null>(null)
  const [rulesSortDir, setRulesSortDir]     = useState<'asc' | 'desc'>('asc')
  const [toasts, setToasts]             = useState<AlertFiredPayload[]>([])

  // Live alert toast — fires whenever the engine triggers an alert
  const handleWsMessage = useCallback((msg: WsMessage) => {
    if (msg.type === 'alert_fired') {
      setToasts(prev => [msg.data, ...prev].slice(0, 5))
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.event_id !== msg.data.event_id))
      }, 8000)
      loadEvents()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useWebSocket(handleWsMessage)

  const toggleRulesSort = (key: keyof AlertRule | 'topic') => {
    if (rulesSortKey === key) setRulesSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setRulesSortKey(key); setRulesSortDir('asc') }
  }

  const loadEvents = async () => {
    setLoading(true)
    try {
      const [active, all] = await Promise.all([
        api.getAlertEvents(true),
        api.getAlertEvents(false),
      ])
      setEvents(active)
      setHistory(all.filter(e => e.acked_at !== null))
    } finally {
      setLoading(false)
    }
  }

  const loadRules = async () => {
    const data = await api.getAlertRules()
    setRules(data)
  }

  useEffect(() => {
    loadEvents()
    loadRules()
  }, [])

  const handleAck = async (id: number) => {
    await api.ackEvent(id)
    await loadEvents()
  }

  const handleAckAll = async () => {
    await api.ackAllEvents()
    await loadEvents()
  }

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
                <span className="truncate opacity-80">{t.message}</span>
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
          <h1 className="text-xl font-bold text-white">Alerts</h1>
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
          <button
            onClick={() => setAddingRule(true)}
            className="bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg px-4 py-2 transition-colors"
          >
            + New rule
          </button>
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
          {loading && <p className="text-sm text-white">Loading…</p>}
          {!loading && events.length === 0 && (
            <div className="flex flex-col items-center justify-center h-32 text-white">
              <p className="text-2xl mb-2">✓</p>
              <p className="text-sm">No unacknowledged alerts</p>
            </div>
          )}
          {events.map(e => <EventCard key={e.id} event={e} onAck={handleAck} />)}
        </div>
      )}

      {/* History */}
      {tab === 'history' && (
        <div className="space-y-3">
          {history.length === 0 && !loading && (
            <div className="flex flex-col items-center justify-center h-32 text-white">
              <p className="text-sm">No alert history</p>
            </div>
          )}
          {history.map(e => <EventCard key={e.id} event={e} onAck={handleAck} />)}
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
            )
          })()}
        </div>
      )}
    </div>
  )
}
