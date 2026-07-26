import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, Network } from 'lucide-react'
import { api, IpInfoResult, InternalIpInfoResult } from '../api/client'
import { isPrivateIp, isValidIpv4 } from '../utils/ip'

// MXToolbox Lookup responses share one envelope regardless of command
// (UID/Command/... plus Passed/Warnings/Failed/Timeouts result arrays) — a
// generic pass/warn/fail summary covers ptr/asn/blacklist without needing a
// bespoke renderer per command.
function mxtoolboxEntryText(entry: any): string {
  if (typeof entry === 'string') return entry
  return entry?.Info || entry?.Name || entry?.Description || entry?.Message || JSON.stringify(entry)
}

// Bold, all-caps, single-line section title — visually separates each
// provider's block in a modal that otherwise stacks four of them in a row.
function SectionTitle({ children }: { children: string }) {
  return (
    <p className="text-xs font-bold text-blue-300 uppercase tracking-wider mb-2 text-center whitespace-nowrap">
      {'*'.repeat(9)} {children} {'*'.repeat(9)}
    </p>
  )
}

function MxtoolboxSummary({ result, error }: { result: any; error?: string | null }) {
  if (error) return <p className="text-xs text-gray-400">{error}</p>
  if (!result) return <p className="text-xs text-gray-400">No data</p>
  const passed = result.Passed?.length ?? 0
  const warnings = result.Warnings ?? []
  const failed = result.Failed ?? []
  return (
    <div>
      <p className="text-sm text-white">
        {passed} passed
        {warnings.length > 0 && <span className="text-yellow-400"> · {warnings.length} warning{warnings.length === 1 ? '' : 's'}</span>}
        {failed.length > 0 && <span className="text-red-400"> · {failed.length} failed</span>}
      </p>
      {[...failed, ...warnings].slice(0, 5).map((e: any, i: number) => (
        <p key={i} className="text-xs text-gray-400 mt-1">{mxtoolboxEntryText(e)}</p>
      ))}
    </div>
  )
}

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
  const showIpinfoField = (f: string) => !data?.ipinfo_enabled_fields || data.ipinfo_enabled_fields.includes(f)
  const showIpapiIsField = (f: string) => !data?.ipapi_is_enabled_fields || data.ipapi_is_enabled_fields.includes(f)
  const showMxtoolboxField = (f: string) => !data?.mxtoolbox_enabled_fields || data.mxtoolbox_enabled_fields.includes(f)

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
              {data.ipinfo_enabled && (
              <div>
                <SectionTitle>IPINFO.IO</SectionTitle>
                {data.ipinfo_error
                  ? <ProviderError msg={data.ipinfo_error} />
                  : (
                    <div className="space-y-3">
                      {showIpinfoField('geolocation') && (
                        <div>
                          <Row label="City" value={data.ipinfo?.city} />
                          <Row label="Region" value={data.ipinfo?.region} />
                          <Row label="Country" value={data.ipinfo?.country} />
                          <Row label="Hostname" value={data.ipinfo?.hostname} />
                          <Row label="Timezone" value={data.ipinfo?.timezone} />
                          <Row label="Postal" value={data.ipinfo?.postal} />
                        </div>
                      )}
                      {showIpinfoField('asn') && (data.ipinfo?.asn || data.ipinfo?.org) && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">ASN / Org</p>
                          <Row label="Name" value={data.ipinfo?.asn?.name ?? data.ipinfo?.org} />
                          <Row label="Route" value={data.ipinfo?.asn?.route} />
                          <Row label="Type" value={data.ipinfo?.asn?.type} />
                        </div>
                      )}
                      {showIpinfoField('company') && data.ipinfo?.company && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Company</p>
                          <Row label="Name" value={data.ipinfo.company.name} />
                          <Row label="Domain" value={data.ipinfo.company.domain} />
                          <Row label="Type" value={data.ipinfo.company.type} />
                        </div>
                      )}
                      {showIpinfoField('privacy') && data.ipinfo?.privacy && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Privacy Detection</p>
                          <p className="text-sm text-white">
                            {[
                              data.ipinfo.privacy.vpn && 'VPN',
                              data.ipinfo.privacy.proxy && 'Proxy',
                              data.ipinfo.privacy.tor && 'Tor',
                              data.ipinfo.privacy.relay && 'Relay',
                              data.ipinfo.privacy.hosting && 'Hosting',
                            ].filter(Boolean).join(', ') || 'None detected'}
                            {data.ipinfo.privacy.service && <span className="text-gray-400"> ({data.ipinfo.privacy.service})</span>}
                          </p>
                        </div>
                      )}
                      {showIpinfoField('abuse') && data.ipinfo?.abuse && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Abuse Contact</p>
                          <Row label="Email" value={data.ipinfo.abuse.email} />
                          <Row label="Phone" value={data.ipinfo.abuse.phone} />
                          <Row label="Name" value={data.ipinfo.abuse.name} />
                          <Row label="Address" value={data.ipinfo.abuse.address} />
                        </div>
                      )}
                      {showIpinfoField('domains') && data.ipinfo?.domains?.domains?.length > 0 && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Hosted Domains</p>
                          <p className="text-sm text-white break-all">
                            {data.ipinfo?.domains.domains.slice(0, 5).join(', ')}
                            {data.ipinfo?.domains.total > 5 && <span className="text-gray-400"> +{data.ipinfo?.domains.total - 5} more</span>}
                          </p>
                        </div>
                      )}
                    </div>
                  )}
              </div>
              )}

              {data.ipapi_is_enabled && (
              <div>
                <SectionTitle>IPAPI.IS</SectionTitle>
                {data.ipapi_is_error
                  ? <ProviderError msg={data.ipapi_is_error} />
                  : (
                    <div className="space-y-3">
                      {showIpapiIsField('geolocation') && (
                        <div>
                          <Row label="City" value={data.ipapi_is?.location?.city} />
                          <Row label="State" value={data.ipapi_is?.location?.state} />
                          <Row label="Country" value={data.ipapi_is?.location?.country} />
                          <Row label="Timezone" value={data.ipapi_is?.location?.timezone} />
                          <Row label="Local Time" value={data.ipapi_is?.location?.local_time} />
                        </div>
                      )}
                      {showIpapiIsField('asn') && data.ipapi_is?.asn && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">ASN / Org</p>
                          <Row label="Name" value={data.ipapi_is.asn.org} />
                          <Row label="Route" value={data.ipapi_is.asn.route} />
                          <Row label="Type" value={data.ipapi_is.asn.type} />
                        </div>
                      )}
                      {showIpapiIsField('company') && data.ipapi_is?.company && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Company</p>
                          <Row label="Name" value={data.ipapi_is.company.name} />
                          <Row label="Domain" value={data.ipapi_is.company.domain} />
                          <Row label="Type" value={data.ipapi_is.company.type} />
                        </div>
                      )}
                      {showIpapiIsField('detection') && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Threat Detection</p>
                          <p className="text-sm text-white">
                            {[
                              data.ipapi_is?.is_vpn && 'VPN',
                              data.ipapi_is?.is_proxy && 'Proxy',
                              data.ipapi_is?.is_tor && 'Tor',
                              data.ipapi_is?.is_datacenter && 'Datacenter',
                              data.ipapi_is?.is_mobile && 'Mobile',
                              data.ipapi_is?.is_satellite && 'Satellite',
                              data.ipapi_is?.is_abuser && 'Known Abuser',
                            ].filter(Boolean).join(', ') || 'None detected'}
                          </p>
                          {data.ipapi_is?.is_vpn && data.ipapi_is?.vpn?.service && (
                            <p className="text-xs text-gray-400 mt-1">VPN service: {data.ipapi_is.vpn.service}</p>
                          )}
                          {data.ipapi_is?.is_datacenter && data.ipapi_is?.datacenter?.datacenter && (
                            <p className="text-xs text-gray-400 mt-1">Datacenter: {data.ipapi_is.datacenter.datacenter}</p>
                          )}
                        </div>
                      )}
                      {showIpapiIsField('abuse') && data.ipapi_is?.abuse && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Abuse Contact</p>
                          <Row label="Email" value={data.ipapi_is.abuse.email} />
                          <Row label="Phone" value={data.ipapi_is.abuse.phone} />
                          <Row label="Name" value={data.ipapi_is.abuse.name} />
                          <Row label="Address" value={data.ipapi_is.abuse.address} />
                        </div>
                      )}
                    </div>
                  )}
              </div>
              )}

              {data.abuseipdb_enabled && (
              <div>
                <SectionTitle>ABUSEIPDB</SectionTitle>
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
              )}

              {data.mxtoolbox_enabled && (
              <div>
                <SectionTitle>MXTOOLBOX</SectionTitle>
                {data.mxtoolbox_error
                  ? <ProviderError msg={data.mxtoolbox_error} />
                  : (
                    <div className="space-y-3">
                      {showMxtoolboxField('ptr') && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Reverse DNS (PTR)</p>
                          <MxtoolboxSummary result={data.mxtoolbox?.ptr} error={data.mxtoolbox?.ptr_error} />
                        </div>
                      )}
                      {showMxtoolboxField('asn') && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">ASN</p>
                          <MxtoolboxSummary result={data.mxtoolbox?.asn} error={data.mxtoolbox?.asn_error} />
                        </div>
                      )}
                      {showMxtoolboxField('blacklist') && (
                        <div>
                          <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">Blacklist Check</p>
                          <MxtoolboxSummary result={data.mxtoolbox?.blacklist} error={data.mxtoolbox?.blacklist_error} />
                        </div>
                      )}
                    </div>
                  )}
              </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Internal (pktIPAM) modal ────────────────────────────────────────────────────
function InternalIpInfoModal({ ip, onClose }: { ip: string; onClose: () => void }) {
  const navigate = useNavigate()
  const [data, setData]       = useState<InternalIpInfoResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    api.getInternalIpInfo(ip)
      .then(setData)
      .catch(e => setError(e.message ?? 'Lookup failed'))
      .finally(() => setLoading(false))
  }, [ip])

  const goToSettings = () => { onClose(); navigate('/settings?tab=security') }

  const Row = ({ label, value }: { label: string; value?: string | number | null }) => (
    value === undefined || value === null || value === '' ? null : (
      <div className="flex justify-between items-start py-1.5 border-b border-gray-800 last:border-0">
        <span className="text-xs text-white shrink-0 w-32">{label}</span>
        <span className="text-sm text-white text-right break-all">{value}</span>
      </div>
    )
  )

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-900 border border-purple-800/60 rounded-2xl w-full max-w-lg shadow-2xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <div>
            <h2 className="font-semibold text-white">pktIPAM Lookup</h2>
            <p className="text-xs font-mono text-purple-300 mt-0.5">{ip}</p>
          </div>
          <button onClick={onClose} className="text-white hover:text-white text-lg leading-none">✕</button>
        </div>

        <div className="px-5 py-4 space-y-5">
          {loading && <p className="text-sm text-white">Looking up…</p>}
          {error && <p className="text-sm text-red-400">{error}</p>}

          {data && !data.configured && (
            <p className="text-xs text-white">
              {data.error}
              <button onClick={goToSettings} className="ml-1 text-purple-400 hover:text-purple-300 underline">
                Go to Settings →
              </button>
            </p>
          )}

          {data && data.configured && data.error && (
            <p className="text-xs text-red-400">{data.error}</p>
          )}

          {data && data.configured && !data.error && !data.found && (
            <p className="text-xs text-white">No record of {ip} in pktIPAM.</p>
          )}

          {data && data.configured && !data.error && data.found && (
            <>
              <div>
                <p className="text-xs font-medium text-white uppercase tracking-wider mb-2">Inventory</p>
                <Row label="Subnet" value={data.subnet?.cidr} />
                <Row label="Site" value={data.subnet?.site} />
                <Row label="Status" value={data.ip_address?.status} />
                <Row label="Hostname" value={data.ip_address?.hostname} />
                <Row label="MAC Address" value={data.ip_address?.mac_address} />
                <Row label="Owner" value={data.ip_address?.owner} />
                <Row label="Description" value={data.ip_address?.description} />
              </div>

              {data.dhcp_leases.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-white uppercase tracking-wider mb-2">DHCP Lease</p>
                  <Row label="State" value={data.dhcp_leases[0].state} />
                  <Row label="Hostname" value={data.dhcp_leases[0].hostname} />
                  <Row label="MAC Address" value={data.dhcp_leases[0].mac_address} />
                  <Row label="Ends" value={data.dhcp_leases[0].ends_at} />
                </div>
              )}

              {data.dns_records.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-white uppercase tracking-wider mb-2">DNS Records</p>
                  {data.dns_records.map((r, i) => (
                    <Row key={i} label={r.record_type} value={`${r.name}.${r.zone}`} />
                  ))}
                </div>
              )}

              {data.arp_entries.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-white uppercase tracking-wider mb-2">Last Seen (ARP)</p>
                  <Row label="Device" value={data.arp_entries[0].device_label} />
                  <Row label="Interface" value={data.arp_entries[0].interface} />
                  <Row label="VLAN" value={data.arp_entries[0].vlan_tag} />
                </div>
              )}
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
    if (!isValidIpv4(ip)) {
      return <span className={className}>{ip}</span>
    }
    return (
      <>
        <button
          onClick={e => { e.stopPropagation(); setOpen(true) }}
          className={`${className} group inline-flex items-center gap-1 rounded px-1 -mx-1 hover:bg-purple-500/10 transition-colors`}
          title="Look up in pktIPAM"
        >
          <span className="underline decoration-purple-400/70 decoration-2 underline-offset-2 decoration-dashed group-hover:decoration-purple-300">{ip}</span>
          <Network className="w-3 h-3 text-purple-400 group-hover:text-purple-300 shrink-0" />
        </button>
        {open && <InternalIpInfoModal ip={ip} onClose={() => setOpen(false)} />}
      </>
    )
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
