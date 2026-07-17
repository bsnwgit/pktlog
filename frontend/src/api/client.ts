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
  // ── Auth ──────────────────────────────────────────────────────────────────
  // Deliberately bypasses request() — a bad password here is a normal login
  // failure, not an expired session, and must not trigger the 401 handler's
  // refresh-then-redirect-to-/login flow (that would hard-reload the login
  // page itself before the error message is even visible).
  login: async (username: string, password: string) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || res.statusText)
    }
    return res.json() as Promise<{ access_token: string; role: string }>
  },
  logout: () => request('/auth/logout', { method: 'POST' }),

  // ── Syslog ────────────────────────────────────────────────────────────────
  getSyslogStats: (hours = 24) =>
    request<SyslogStats>(`/syslog/stats?hours=${hours}`),

  searchSyslog: (params: SyslogSearchParams) =>
    request<SyslogSearchResult>(`/syslog/search?${buildQS(params)}`),

  getSyslogTimeseries: (hours = 6, bucketMinutes = 5, logGroup?: string) => {
    const qs = new URLSearchParams({ hours: String(hours), bucket_minutes: String(bucketMinutes) })
    if (logGroup) qs.set('log_group', logGroup)
    return request<SyslogTimeseriesPoint[]>(`/syslog/timeseries?${qs}`)
  },

  // ── Collector registry ────────────────────────────────────────────────────
  getCollectors: () => request<Collector[]>('/collectors/'),
  createCollector: (body: CollectorIn) =>
    request<Collector>('/collectors/', { method: 'POST', body: JSON.stringify(body) }),
  updateCollector: (ip: string, body: Partial<CollectorIn>) =>
    request<Collector>(`/collectors/${encodeURIComponent(ip)}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteCollector: (ip: string) =>
    request(`/collectors/${encodeURIComponent(ip)}`, { method: 'DELETE' }),
  exportCollectors: async (): Promise<void> => {
    const headers: Record<string, string> = {}
    if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`
    const res = await fetch('/api/collectors/export', { headers })
    if (!res.ok) throw new Error(`Export failed: ${res.status}`)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'pktlog-collectors.csv'
    a.click()
    URL.revokeObjectURL(url)
  },
  importCollectors: async (file: File): Promise<{ created: number; skipped: number; errors: string[] }> => {
    const formData = new FormData()
    formData.append('file', file)
    const headers: Record<string, string> = {}
    if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`
    const res = await fetch('/api/collectors/import-csv', { method: 'POST', headers, body: formData })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || res.statusText)
    }
    return res.json()
  },

  // ── Alerts ────────────────────────────────────────────────────────────────
  getAlertRules: () => request<AlertRule[]>('/alerts/rules'),
  getAlertEvents: (unackedOnly = false, since?: string, until?: string) => {
    const p = new URLSearchParams({ unacked_only: String(unackedOnly) })
    if (since) p.set('since', since)
    if (until) p.set('until', until)
    return request<AlertEvent[]>(`/alerts/events?${p.toString()}`)
  },
  ackEvent: (id: number) => request(`/alerts/events/${id}/ack`, { method: 'POST' }),
  ackAllEvents: () => request('/alerts/events/ack-all', { method: 'POST' }),

  // ── Settings ──────────────────────────────────────────────────────────────
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

  // ── Users ─────────────────────────────────────────────────────────────────
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

  // ── System ────────────────────────────────────────────────────────────────
  testStorageConnection: () =>
    request<{ ok: boolean; backend: string; message: string }>('/system/test-connection', { method: 'POST' }),
  getSuiteToken: () =>
    request<{ suite_token: string; has_token: boolean }>('/suite/token'),

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

  // ── App logs ──────────────────────────────────────────────────────────────
  getLogs: (params: LogQueryParams) =>
    request<LogResponse>(`/logs?${new URLSearchParams(params as any)}`),
  getLogStats: () =>
    request<LogStats>('/logs/stats'),
  clearLogs: () =>
    request<{ status: string }>('/logs', { method: 'DELETE' }),
  setLogLevel: (level: string) =>
    request<{ status: string; level: string }>(`/logs/level?level=${level}`, { method: 'POST' }),

  // ── User API keys / IP info ─────────────────────────────────────────────────
  getUserApiKeys: () => request<UserApiKey[]>('/user-api-keys'),
  setUserApiKey: (provider: string, api_key: string) =>
    request<UserApiKey>(`/user-api-keys/${provider}`, { method: 'PUT', body: JSON.stringify({ api_key }) }),
  testUserApiKey: (provider: string, api_key: string) =>
    request<{ status: string; detail: string }>(`/user-api-keys/${provider}/test`, { method: 'POST', body: JSON.stringify({ api_key }) }),
  getIpInfo: (ip: string) => request<IpInfoResult>(`/ip-info/${ip}`),
}

export interface IpInfoResult {
  ip: string
  ipinfo: Record<string, any> | null
  ipinfo_error: string | null
  abuseipdb: Record<string, any> | null
  abuseipdb_error: string | null
}

export interface UserApiKey {
  provider: string
  label: string
  api_key: string
  updated_at: string | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function buildQS(params: Record<string, string | number | boolean | undefined | null>): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  }
  return qs.toString()
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

// ── Types ─────────────────────────────────────────────────────────────────────

// Syslog
export interface SyslogRecord {
  timestamp: string
  received_at: string
  source_ip: string
  source_name: string
  dest_ip: string
  facility: number
  facility_name: string
  severity: number
  severity_name: string
  program: string
  pid: string
  message: string
  raw: string
  collector_ip: string
  collector_name: string
  org: string
  log_group: string
  site: string
}

export interface SyslogSearchResult {
  total: number
  limit: number
  offset: number
  records: SyslogRecord[]
}

export interface SyslogSeverityCount {
  severity: number
  severity_name: string
  count: number
}

export interface SyslogHostCount {
  source_ip: string
  source_name: string
  count: number
}

export interface SyslogProgramCount {
  program: string
  count: number
}

export interface SyslogCollectorStatus {
  collector_ip: string
  collector_name: string
  last_seen: string | null
}

export interface SyslogStats {
  hours: number
  count_by_severity: SyslogSeverityCount[]
  top_hosts: SyslogHostCount[]
  top_programs: SyslogProgramCount[]
  collector_last_seen: SyslogCollectorStatus[]
}

export interface SyslogTimeseriesPoint {
  bucket: string
  count: number
}

export type SyslogSearchParams = {
  start?: string
  end?: string
  source_ip?: string
  dest_ip?: string
  collector_ip?: string
  collector_name?: string
  org?: string
  log_group?: string
  site?: string
  severity_max?: number
  facility?: number
  program?: string
  q?: string
  limit?: number
  offset?: number
}

// Collector registry
export interface Collector {
  id: number
  collector_ip: string
  collector_name: string
  org: string
  log_group: string
  site: string
  notes: string
  enabled: number
  created_at: string
  updated_at: string
}

export interface CollectorIn {
  collector_ip: string
  collector_name: string
  org?: string
  log_group?: string
  site?: string
  notes?: string
  enabled?: boolean
}

// Alerts
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
  auto_resolved: number
}

// Users
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

// SSL
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

// App logs
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
  until?: string
  limit?: string
  offset?: string
}
