import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import { api, IpInfoResult } from '../api/client'
import { isPrivateIp } from '../utils/ip'

// ── Modal ─────────────────────────────────────────────────────────────────────
function IpInfoModal({ ip, onClose }: { ip: string; onClose: () => void }) {
  const navigate = useNavigate()
  const [data, setData]       = useState<IpInfoResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    api.getIpInfo(ip)
      .then(setData)
      .catch(e => setError(e.message ?? 'Lookup failed'))
      .finally(() => setLoading(false))
  }, [ip])

  const goToApiKeys = () => { onClose(); navigate('/settings?tab=apikeys') }

  const score = data?.abuseipdb?.abuseConfidenceScore as number | undefined
  const scoreColor = score === undefined ? '' : score >= 50 ? 'text-red-400' : score >= 20 ? 'text-yellow-400' : 'text-green-400'

  const Row = ({ label, value }: { label: string; value?: string | number | null }) => (
    value === undefined || value === null || value === '' ? null : (
      <div className="flex justify-between items-start py-1.5 border-b border-gray-800 last:border-0">
        <span className="text-xs text-white shrink-0 w-32">{label}</span>
        <span className="text-sm text-white text-right break-all">{value}</span>
      </div>
    )
  )

  const ProviderError = ({ msg }: { msg: string }) => (
    <div className="text-xs text-white py-2">
      {msg}
      {msg.includes('Settings') && (
        <button onClick={goToApiKeys} className="ml-1 text-blue-400 hover:text-blue-300 underline">
          Go to API Keys →
        </button>
      )}
    </div>
  )

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-lg shadow-2xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <div>
            <h2 className="font-semibold text-white">IP Lookup</h2>
            <p className="text-xs font-mono text-blue-300 mt-0.5">{ip}</p>
          </div>
          <button onClick={onClose} className="text-white hover:text-white text-lg leading-none">✕</button>
        </div>

        <div className="px-5 py-4 space-y-5">
          {loading && <p className="text-sm text-white">Looking up…</p>}
          {error && <p className="text-sm text-red-400">{error}</p>}

          {data && (
            <>
              <div>
                <p className="text-xs font-medium text-white uppercase tracking-wider mb-2">ipinfo.io</p>
                {data.ipinfo_error
                  ? <ProviderError msg={data.ipinfo_error} />
                  : (
                    <div>
                      <Row label="City" value={data.ipinfo?.city} />
                      <Row label="Region" value={data.ipinfo?.region} />
                      <Row label="Country" value={data.ipinfo?.country} />
                      <Row label="Org / ASN" value={data.ipinfo?.org} />
                      <Row label="Hostname" value={data.ipinfo?.hostname} />
                      <Row label="Timezone" value={data.ipinfo?.timezone} />
                    </div>
                  )}
              </div>

              <div>
                <p className="text-xs font-medium text-white uppercase tracking-wider mb-2">AbuseIPDB</p>
                {data.abuseipdb_error
                  ? <ProviderError msg={data.abuseipdb_error} />
                  : (
                    <div>
                      <div className="flex justify-between items-start py-1.5 border-b border-gray-800">
                        <span className="text-xs text-white shrink-0 w-32">Abuse Confidence</span>
                        <span className={`text-sm font-semibold text-right ${scoreColor}`}>{score ?? '—'}%</span>
                      </div>
                      <Row label="Total Reports" value={data.abuseipdb?.totalReports} />
                      <Row label="Country" value={data.abuseipdb?.countryCode} />
                      <Row label="ISP" value={data.abuseipdb?.isp} />
                      <Row label="Usage Type" value={data.abuseipdb?.usageType} />
                      <Row label="Domain" value={data.abuseipdb?.domain} />
                      <Row label="Last Reported" value={data.abuseipdb?.lastReportedAt} />
                    </div>
                  )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Link ──────────────────────────────────────────────────────────────────────
export default function IpLink({ ip, className = '' }: { ip: string; className?: string }) {
  const [open, setOpen] = useState(false)

  if (isPrivateIp(ip)) {
    return <span className={className}>{ip}</span>
  }

  return (
    <>
      <button
        onClick={e => { e.stopPropagation(); setOpen(true) }}
        className={`${className} group inline-flex items-center gap-1 rounded px-1 -mx-1 hover:bg-blue-500/10 transition-colors`}
        title="Look up IP details"
      >
        <span className="underline decoration-blue-400/70 decoration-2 underline-offset-2 group-hover:decoration-blue-300">{ip}</span>
        <Search className="w-3 h-3 text-blue-400 group-hover:text-blue-300 shrink-0" />
      </button>
      {open && <IpInfoModal ip={ip} onClose={() => setOpen(false)} />}
    </>
  )
}

// ── Linkify ───────────────────────────────────────────────────────────────────
// Splits free-text (e.g. a backend-generated alert message) on embedded IPv4
// addresses and wraps each one in an IpLink, leaving the surrounding text as
// plain string fragments — for messages where the IP isn't in its own
// dedicated field.
const IPV4_RE = /\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b/g

export function linkifyIps(text: string): (string | JSX.Element)[] {
  const parts: (string | JSX.Element)[] = []
  let lastIndex = 0
  let i = 0
  for (const match of text.matchAll(IPV4_RE)) {
    const index = match.index ?? 0
    if (index > lastIndex) parts.push(text.slice(lastIndex, index))
    parts.push(<IpLink key={`ip-${i++}`} ip={match[0]} />)
    lastIndex = index + match[0].length
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex))
  return parts
}
