/**
 * pktLog API client — typed fetch wrappers.
 * Access token is stored in memory (not localStorage).
 */

let _accessToken: string | null = null
let _tokenRole: string | null = null

export function setToken(token: string, role: string) {
  _accessToken = token
  _tokenRole = role
}

export function clearToken() {
  _accessToken = null
  _tokenRole = null
}

export function getRole(): string | null {
  return _tokenRole
}

export function isAuthenticated(): boolean {
  return _accessToken !== null
}

export function getToken(): string | null {
  return _accessToken
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }

  if (_accessToken) {
    headers['Authorization'] = `Bearer ${_accessToken}`
  }

  const res = await fetch(`/api${path}`, { ...options, headers })

  if (res.status === 401) {
    const refreshed = await tryRefresh()
    if (refreshed) {
      headers['Authorization'] = `Bearer ${_accessToken}`
      const retry = await fetch(`/api${path}`, { ...options, headers })
      if (!retry.ok) throw new Error(`${retry.status} ${retry.statusText}`)
      return retry.json()
    }
    clearToken()
    window.location.href = '/login'
    throw new Error('Session expired')
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }

  if (res.status === 204) return null as T
  return res.json()
}

async function tryRefresh(): Promise<boolean> {
  try {
    const res = await fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
    if (!res.ok) return false
    const data = await res.json()
    setToken(data.access_token, data.role)
    return true
  } catch {
    return false
  }
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; role: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request('/auth/logout', { method: 'POST' }),

  getDeviceSummaries: () => request<DeviceSummary[]>('/flows/devices'),
  purgeSampler: (sampler_ip: string) =>
    request<null>(`/flows/samplers/${encodeURIComponent(sampler_ip)}`, { method: 'DELETE' }),
  getFlowRate: () => request<{ flows_per_sec: number }>('/flows/rate'),
  getTimeSeries: (params: TimeSeriesParams) =>
    request<TimeSeriesPoint[]>(`/flows/timeseries?${new URLSearchParams(params as any)}`),
  getTopTalkers: (params: TopTalkersParams) =>
    request<TopTalker[]>(`/flows/top-talkers?${new URLSearchParams(params as any)}`),
  searchFlows: (params: SearchParams) =>
    request<FlowRecord[]>(`/flows/search?${new URLSearchParams(params as any)}`),
  getLastSeen: () => request<Record<string, string>>('/flows/last-seen'),
  getTopology: (params: TopologyParams) =>
    request<TopologyResponse>(`/flows/topology?${new URLSearchParams(params as any)}`),
  getProtocolStats: (params: { window?: string; sampler_ip?: string }) =>
    request<ProtocolStat[]>(`/flows/protocols?${new URLSearchParams(params as any)}`),
  getTopPorts: (params: TopPortsParams) =>
    request<PortStat[]>(`/flows/ports/top?${new URLSearchParams(params as any)}`),
  getDailyTimeseries: (days: number, sampler_ip?: string) =>
    request<TimeSeriesPoint[]>(`/flows/timeseries/daily?days=${days}${sampler_ip ? `&sampler_ip=${sampler_ip}` : ''}`),
  getHourlyTimeseries: (window: string, sampler_ip?: string) =>
    request<TimeSeriesPoint[]>(`/flows/timeseries/hourly?window=${window}${sampler_ip ? `&sampler_ip=${sampler_ip}` : ''}`),

  getDevices: () => request<Device[]>('/devices/'),
  createDevice: (d: DeviceIn) => request<Device>('/devices/', { method: 'POST', body: JSON.stringify(d) }),
  updateDevice: (id: number, d: DeviceIn) =>
    request<Device>(`/devices/${id}`, { method: 'PUT', body: JSON.stringify(d) }),
  deleteDevice: (id: number) => request(`/devices/${id}`, { method: 'DELETE' }),
  getUnknownSamplers: () =>
    request<{ unknown: Array<{ sampler_ip: string; flows_per_sec: number; last_seen: string }>; dismissed: Array<{ sampler_ip: string; dismissed_at: string }> }>('/devices/unknown-samplers'),
  getDeviceSites: () => request<string[]>('/devices/sites'),
  dismissSampler: (ip: string) => request<{ dismissed: string }>(`/devices/dismiss/${encodeURIComponent(ip)}`, { method: 'POST' }),
  undismissSampler: (ip: string) => request<null>(`/devices/dismiss/${encodeURIComponent(ip)}`, { method: 'DELETE' }),

  getAlertRules: () => request<AlertRule[]>('/alerts/rules'),
  getAlertEvents: (unackedOnly = false) =>
    request<AlertEvent[]>(`/alerts/events?unacked_only=${unackedOnly}`),
  ackEvent: (id: number) => request(`/alerts/events/${id}/ack`, { method: 'POST' }),
  ackAllEvents: () => request('/alerts/events/ack-all', { method: 'POST' }),

  getSettings: () => request<Record<string, unknown>>('/settings/'),
  updateSetting: (key: string, value: unknown) =>
    request(`/settings/${key}`, { method: 'PUT', body: JSON.stringify({ value }) }),
  bulkUpdateSettings: (updates: Record<string, unknown>) =>
    request('/settings/bulk', { method: 'POST', body: JSON.stringify(updates) }),
  testNotification: (channel: string) =>
    request<{ status: string; detail: string }>('/settings/test-notification', {
      method: 'POST',
      body: JSON.stringify({ channel }),
    }),

  getUsers: () => request<User[]>('/users/'),
  getMe: () => request<User>('/users/me'),
  createUser: (body: UserIn) => request<User>('/users/', { method: 'POST', body: JSON.stringify(body) }),
  updateUser: (id: number, body: UserIn) => request<User>(`/users/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteUser: (id: number) => request(`/users/${id}`, { method: 'DELETE' }),
  activateUser: (id: number) => request(`/users/${id}/activate`, { method: 'PATCH' }),
  deactivateUser: (id: number) => request(`/users/${id}/deactivate`, { method: 'PATCH' }),
  resetUserPassword: (id: number, newPassword: string) =>
    request(`/users/${id}/reset-password`, { method: 'PATCH', body: JSON.stringify({ new_password: newPassword }) }),
  changeMyPassword: (currentPassword: string, newPassword: string) =>
    request('/users/me/password', { method: 'PATCH', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) }),

  testStorageConnection: () =>
    request<{ ok: boolean; backend: string; message: string }>('/system/test-connection', { method: 'POST' }),

  restartService: () =>
    request<{ status: string; message: string }>('/system/restart', { method: 'POST' }),

  runCleanup: () =>
    request<{
      flows_eligible: number
      hourly_eligible: number
      alert_events_deleted: number
      notification_log_deleted: number
      clickhouse_status: string
      status: string
    }>('/system/cleanup', { method: 'POST' }),

  runBackupNow: () =>
    request<{ status: string; path: string; files: string[]; kept: number }>('/system/backup', { method: 'POST' }),

  listBackups: () =>
    request<Array<{ name: string; path: string; size_bytes: number; files: string[] }>>('/system/backup/list'),

  importBundle: async (file: File): Promise<Record<string, string>> => {
    const formData = new FormData()
    formData.append('file', file)
    const headers: Record<string, string> = {}
    if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`
    const res = await fetch('/api/system/import', { method: 'POST', headers, body: formData })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || res.statusText)
    }
    return res.json()
  },

  exportConfig: async (): Promise<{ blob: Blob; filename: string }> => {
    const headers: Record<string, string> = {}
    if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`
    const res = await fetch('/api/system/export', { headers })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') ?? ''
    const match = cd.match(/filename="([^"]+)"/)
    const filename = match ? match[1] : 'pktlog-export.tar.gz'
    return { blob, filename }
  },

  createLucidchart: (params: URLSearchParams) =>
    request<{ edit_url: string; document_id: string }>(
      `/flows/topology/lucidchart?${params}`,
      { method: 'POST' }
    ),

  getSslStatus: () => request<SslStatus>('/system/ssl/status'),
  uploadSsl: async (cert: File, key: File): Promise<SslStatus> => {
    const formData = new FormData()
    formData.append('cert', cert)
    formData.append('key', key)
    const headers: Record<string, string> = {}
    if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`
    const res = await fetch('/api/system/ssl/upload', { method: 'POST', headers, body: formData })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || res.statusText)
    }
    return res.json()
  },
  deleteSsl: () => request<SslStatus>('/system/ssl/cert', { method: 'DELETE' }),
  uploadSslPfx: async (pfx: File, passphrase: string): Promise<SslStatus> => {
    const formData = new FormData()
    formData.append('pfx', pfx)
    formData.append('passphrase', passphrase)
    const headers: Record<string, string> = {}
    if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`
    const res = await fetch('/api/system/ssl/upload-pfx', { method: 'POST', headers, body: formData })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || res.statusText)
    }
    return res.json()
  },

  importDevicesCsv: async (file: File): Promise<{ created: number; updated: number; skipped: number; errors: Array<{ row: number; reason: string }> }> => {
    const formData = new FormData()
    formData.append('file', file)
    const headers: Record<string, string> = {}
    if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`
    const res = await fetch('/api/devices/import', { method: 'POST', headers, body: formData })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || res.statusText)
    }
    return res.json()
  },

  getLogs: (params: LogQueryParams) =>
    request<LogResponse>(`/logs?${new URLSearchParams(params as any)}`),

  getLogStats: () =>
    request<LogStats>('/logs/stats'),

  clearLogs: () =>
    request<{ status: string }>('/logs', { method: 'DELETE' }),

  setLogLevel: (level: string) =>
    request<{ status: string; level: string }>(`/logs/level?level=${level}`, { method: 'POST' }),
}

export interface DeviceSummary {
  sampler_ip: string
  sampler_name: string
  site: string
  bytes_last_hour: number
  packets_last_hour: number
  flows_last_hour: number
  flows_per_sec: number
  last_seen: string | null
}

export interface TimeSeriesPoint {
  timestamp: string
  bytes: number
  packets: number
  flow_count: number
}

export interface TopTalker {
  src_ip: string
  dst_ip: string
  dst_port: number
  protocol: number
  bytes: number
  packets: number
  flow_count: number
}

export interface FlowRecord {
  timestamp: string
  sampler_ip: string
  sampler_name: string
  src_ip: string
  dst_ip: string
  src_port: number
  dst_port: number
  protocol: number
  bytes: number
  packets: number
  duration_ms: number
}

export interface Device {
  id: number
  ip: string
  name: string
  site: string
  notes: string
  allowed: boolean
  created_at: string
  updated_at: string
}

export interface DeviceIn {
  ip: string; name: string; site: string; notes: string; allowed: boolean
}

export interface AlertRule {
  id: number
  name: string
  description: string
  enabled: boolean
  rule_type: string
  conditions: Record<string, unknown>
  severity: string
  channels: string[]
  cooldown_min: number
  last_fired: string | null
}

export interface AlertEvent {
  id: number
  rule_id: number
  rule_name: string
  severity: string
  message: string
  details: Record<string, unknown>
  fired_at: string
  acked_at: string | null
  resolved_at: string | null
  auto_resolved: number  // 1 = engine auto-resolved, 0 = not
}

export interface UserIn {
  username: string
  email: string
  password?: string
  role: string
}

export interface User {
  id: number
  username: string
  email: string
  role: string
  is_active: boolean
  created_at: string
  last_login: string | null
  has_password: boolean
  auth_provider: string
}

export interface SslStatus {
  installed: boolean
  expires?: string
  expires_iso?: string
  days_until_expiry?: number
  subject?: string
  issuer?: string
  error?: string
  status?: string
}

export interface ProtocolStat {
  protocol: number
  name: string
  bytes: number
  packets: number
  flow_count: number
  pct_bytes: number
}

export interface PortStat {
  port: number
  protocol: number
  proto_name: string
  service_name: string
  bytes: number
  packets: number
  flow_count: number
  pct_bytes: number
}

export interface TopologyNode {
  id: string
  sampler_name: string
  site: string
  bytes: number
  flows: number
  is_sampler: boolean
}

export interface TopologyEdge {
  source: string
  target: string
  bytes: number
  packets: number
  flows: number
  protocol: number
  dst_port: number
}

export interface TopologyResponse {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
}

export type TopologyParams = { window?: string; sampler_ip?: string; min_bytes?: string; limit?: string }
export type TimeSeriesParams = { sampler_ip?: string; window?: string; dst_port?: string; protocol?: string; site?: string }
export type TopTalkersParams = { sampler_ip?: string; window?: string; limit?: string }
export type SearchParams = {
  src_ip?: string; dst_ip?: string; src_port?: string; dst_port?: string
  protocol?: string; sampler_ip?: string; window?: string; limit?: string
}
export type TopPortsParams = { window?: string; sampler_ip?: string; site?: string; limit?: string }

export interface LogRecord {
  id: number
  ts: string
  level: string
  level_no: number
  logger: string
  message: string
  exc_info: string | null
}

export interface LogResponse {
  total: number
  limit: number
  offset: number
  records: LogRecord[]
}

export interface LogStats {
  total: number
  by_level: Record<string, number>
  loggers: string[]
  latest_ts: string | null
}

export type LogQueryParams = {
  level?: string
  logger?: string
  search?: string
  since?: string
  limit?: string
  offset?: string
}

export async function downloadExport(
  path: string,
  params: Record<string, string>,
  filename: string,
): Promise<string | null> {
  const qs = new URLSearchParams(params).toString()
  const url = `/api${path}${qs ? '?' + qs : ''}`
  const headers: Record<string, string> = {}
  if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`
  try {
    const res = await fetch(url, { headers })
    if (!res.ok) return `Export failed: ${res.status} ${res.statusText}`
    const blob = await res.blob()
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(href)
    return null
  } catch (e) {
    return String(e)
  }
}