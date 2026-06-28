import { useEffect, useRef, useState } from 'react'
import { api, DeviceSummary, User, UserIn, SslStatus } from '../api/client'
import { useAutoRefresh } from '../store/autoRefresh'
import { useAuth } from '../store/auth'

// ── Generic helpers ────────────────────────────────────────────────────────────
type Settings = Record<string, unknown>

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-4 items-start py-4 border-b border-gray-800 last:border-0">
      <div>
        <p className="text-sm font-medium text-white">{label}</p>
        {hint && <p className="text-xs text-white mt-0.5">{hint}</p>}
      </div>
      <div className="col-span-2">{children}</div>
    </div>
  )
}

function TextInput({ value, onChange, placeholder = '', secret = false, mono = false }: {
  value: string; onChange: (v: string) => void
  placeholder?: string; secret?: boolean; mono?: boolean
}) {
  return (
    <input
      type={secret ? 'password' : 'text'}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      className={`w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 ${mono ? 'font-mono' : ''}`}
    />
  )
}

function NumberInput({ value, onChange, min, max }: {
  value: number; onChange: (v: number) => void; min?: number; max?: number
}) {
  return (
    <input
      type="number" min={min} max={max}
      value={value}
      onChange={e => onChange(parseInt(e.target.value) || 0)}
      className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
    />
  )
}

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

function SelectInput({ value, onChange, options }: {
  value: string; onChange: (v: string) => void; options: Array<{ value: string; label: string }>
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

function RestartServiceRow() {
  const [state, setState] = useState<'idle' | 'restarting' | 'done' | 'error'>('idle')

  const restart = async () => {
    if (state === 'restarting') return
    setState('restarting')
    try {
      await api.restartService()
      setState('done')
      // After a few seconds, show reconnecting state until page reload
      setTimeout(() => setState('idle'), 8000)
    } catch {
      setState('error')
      setTimeout(() => setState('idle'), 4000)
    }
  }

  return (
    <div className="grid grid-cols-3 gap-4 items-start py-4 border-b border-gray-800">
      <div>
        <p className="text-sm font-medium text-white">Restart Service</p>
        <p className="text-xs text-white mt-0.5">Apply backend changes or recover from errors</p>
      </div>
      <div className="col-span-2 flex items-center gap-3">
        <button
          onClick={restart}
          disabled={state === 'restarting'}
          className="px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:bg-gray-700 disabled:text-white text-white text-sm font-medium rounded-lg transition-colors"
        >
          {state === 'restarting' ? 'Restarting…' : 'Restart Service'}
        </button>
        {state === 'done' && (
          <span className="text-sm text-amber-400">Service is restarting — reload the page in ~5 seconds</span>
        )}
        {state === 'error' && (
          <span className="text-sm text-red-400">Restart failed — check server logs</span>
        )}
      </div>
    </div>
  )
}

// ── Section wrapper with Save ─────────────────────────────────────────────────
function Section({
  title, children, onSave, saving, saved, error,
}: {
  title: string
  children: React.ReactNode
  onSave: () => Promise<void>
  saving: boolean
  saved: boolean
  error: string
}) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
      </div>
      <div className="px-6 py-2">
        {children}
      </div>
      <div className="px-6 py-4 border-t border-gray-800 flex items-center gap-3">
        <button
          onClick={onSave}
          disabled={saving}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg px-5 py-2 transition-colors"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        {saved && <span className="text-xs text-green-400">Saved</span>}
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    </div>
  )
}

// ── Per-tab save state ────────────────────────────────────────────────────────
interface SaveState { saving: boolean; saved: boolean; error: string }
const INIT: SaveState = { saving: false, saved: false, error: '' }

function SendTestButton({ channel }: { channel: string }) {
  const [status, setStatus] = useState<'idle' | 'loading' | 'sent' | 'failed' | 'skipped'>('idle')
  const [detail, setDetail] = useState('')

  const run = async () => {
    setStatus('loading')
    setDetail('')
    try {
      const res = await api.testNotification(channel)
      setStatus(res.status as 'sent' | 'failed' | 'skipped')
      setDetail(res.detail || '')
    } catch (e) {
      setStatus('failed')
      setDetail(String(e))
    }
  }

  return (
    <div className="flex items-center gap-3 mt-2 mb-1">
      <button
        onClick={run}
        disabled={status === 'loading'}
        className="px-3 py-1.5 text-xs rounded-lg border border-gray-600 bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {status === 'loading' ? 'Sending…' : 'Send Test'}
      </button>
      {status === 'sent'    && <span className="text-xs text-green-400">✓ Sent{detail ? ` — ${detail}` : ''}</span>}
      {status === 'skipped' && <span className="text-xs text-yellow-400">⚠ Skipped — {detail}</span>}
      {status === 'failed'  && <span className="text-xs text-red-400">✗ Failed — {detail}</span>}
    </div>
  )
}

function useSave(keys: string[], settings: Settings, onSuccess: () => void) {
  const [state, setState] = useState<SaveState>(INIT)

  const save = async () => {
    setState({ saving: true, saved: false, error: '' })
    try {
      const subset: Settings = {}
      for (const k of keys) if (k in settings) subset[k] = settings[k]
      await api.bulkUpdateSettings(subset)
      setState({ saving: false, saved: true, error: '' })
      onSuccess()
      setTimeout(() => setState(s => ({ ...s, saved: false })), 3000)
    } catch (e: any) {
      setState({ saving: false, saved: false, error: e.message || 'Save failed' })
    }
  }

  return { ...state, save }
}

// ── Drag-and-drop cert/key textarea ──────────────────────────────────────────
function CertTextarea({ value, onChange, rows = 4, placeholder = 'MIIDp…', secret = false }: {
  value: string; onChange: (v: string) => void; rows?: number; placeholder?: string; secret?: boolean
}) {
  const [dragging, setDragging] = useState(false)
  const [revealed, setRevealed] = useState(false)

  const stripPem = (raw: string) =>
    raw
      .replace(/-----BEGIN[^-]+-----/g, '')
      .replace(/-----END[^-]+-----/g, '')
      .replace(/\s+/g, '')

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const text = reader.result as string
      onChange(stripPem(text))
      setRevealed(false)
    }
    reader.readAsText(file)
  }

  // When secret=true and a value is stored, show only a status indicator — no reveal
  if (secret && value && !revealed) {
    return (
      <div className="flex items-center gap-2">
        <div className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-green-400 font-mono">
          ✓ Certificate saved
        </div>
        <button
          type="button"
          onClick={() => setRevealed(true)}
          className="text-xs text-blue-400 hover:text-blue-300 whitespace-nowrap px-2 py-1 border border-gray-700 rounded-lg bg-gray-800"
        >
          Replace
        </button>
        <button
          type="button"
          onClick={() => onChange('')}
          className="text-xs text-red-400 hover:text-red-300 whitespace-nowrap px-2 py-1 border border-gray-700 rounded-lg bg-gray-800"
        >
          Clear
        </button>
      </div>
    )
  }

  return (
    <div
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={`relative rounded-lg transition-colors ${dragging ? 'ring-2 ring-blue-400 bg-blue-950/30' : ''}`}
    >
      {secret && revealed && (
        <div className="flex justify-end mb-1">
          <button type="button" onClick={() => setRevealed(false)} className="text-xs text-gray-500 hover:text-gray-300">Cancel</button>
        </div>
      )}
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        rows={rows}
        placeholder={placeholder}
        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono resize-y"
      />
      {dragging && (
        <div className="absolute inset-0 flex items-center justify-center rounded-lg pointer-events-none">
          <p className="text-blue-300 text-sm font-medium bg-gray-900/80 px-3 py-1 rounded">Drop to import</p>
        </div>
      )}
      <p className="text-xs text-gray-600 mt-1">Paste content or drag &amp; drop a .pem / .crt / .cer file</p>
    </div>
  )
}

// ── SAML metadata paste box ───────────────────────────────────────────────────
function MetadataPasteBox({ onParsed }: {
  onParsed: (r: { entity_id: string; sso_url: string; cert: string }) => void
}) {
  const [xml, setXml]       = useState('')
  const [status, setStatus] = useState<'idle' | 'ok' | 'error'>('idle')
  const [msg, setMsg]       = useState('')

  const handleChange = (raw: string) => {
    setXml(raw)
    if (!raw.trim()) { setStatus('idle'); setMsg(''); return }
    const result = parseIdpMetadata(raw)
    if (result.error) {
      setStatus('error')
      setMsg(result.error)
    } else {
      onParsed(result)
      setStatus('ok')
      setMsg('Entity ID, SSO URL, and certificate populated below.')
    }
  }

  return (
    <div className="space-y-1.5">
      <textarea
        value={xml}
        onChange={e => handleChange(e.target.value)}
        rows={5}
        placeholder={'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" …>'}
        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono resize-y"
      />
      {status === 'ok'    && <p className="text-xs text-emerald-400">✓ {msg}</p>}
      {status === 'error' && <p className="text-xs text-red-400">✗ {msg}</p>}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
type TabId = 'general' | 'storage' | 'backup' | 'ingest' | 'auth' | 'notifications' | 'devices' | 'integrations' | 'users'

const TABS: Array<{ id: TabId; label: string; adminOnly?: boolean }> = [
  { id: 'general',       label: 'General' },
  { id: 'devices',       label: 'Collectors' },
  { id: 'storage',       label: 'Storage' },
  { id: 'backup',        label: 'Backup' },
  { id: 'ingest',        label: 'Ingest' },
  { id: 'auth',          label: 'Auth' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'integrations',  label: 'Integrations' },
  { id: 'users',         label: 'Users', adminOnly: true },
]

// ── SAML IdP metadata parser ──────────────────────────────────────────────────
function parseIdpMetadata(xml: string): {
  entity_id: string; sso_url: string; cert: string; error?: string
} {
  try {
    const doc = new DOMParser().parseFromString(xml, 'application/xml')
    if (doc.querySelector('parsererror')) return { entity_id: '', sso_url: '', cert: '', error: 'Invalid XML — check the metadata and try again.' }

    const root = doc.querySelector('EntityDescriptor') ?? doc.documentElement
    const entity_id = root.getAttribute('entityID') ?? ''

    // Prefer HTTP-Redirect binding; fall back to any SSO service
    let sso_url = ''
    const ssoNodes = Array.from(doc.querySelectorAll('SingleSignOnService'))
    const redirect = ssoNodes.find(n => (n.getAttribute('Binding') ?? '').includes('HTTP-Redirect'))
    sso_url = (redirect ?? ssoNodes[0])?.getAttribute('Location') ?? ''

    // Prefer signing key; fall back to any X509Certificate
    let cert = ''
    const keyDescs = Array.from(doc.querySelectorAll('KeyDescriptor'))
    const signingKd = keyDescs.find(kd => !kd.getAttribute('use') || kd.getAttribute('use') === 'signing')
    const x509El = signingKd?.querySelector('X509Certificate') ?? doc.querySelector('X509Certificate')
    cert = x509El?.textContent?.replace(/\s+/g, '') ?? ''

    if (!entity_id && !sso_url && !cert)
      return { entity_id: '', sso_url: '', cert: '', error: 'No SAML IdP data found in this XML.' }

    return { entity_id, sso_url, cert }
  } catch {
    return { entity_id: '', sso_url: '', cert: '', error: 'Failed to parse XML.' }
  }
}

export default function Settings() {
  const { user: me }          = useAuth()
  const isAdmin               = me?.role === 'admin'
  const [tab, setTab]         = useState<TabId>('general')
  const [settings, setSettings] = useState<Settings>({})
  const [loading, setLoading] = useState(true)
  // Tracks whether the user has made unsaved edits.
  // silentLoad skips the refresh while dirty so in-progress form work isn't overwritten.
  const dirtyRef = useRef(false)

  const load = async () => {
    setLoading(true)
    try { setSettings(await api.getSettings()) } finally {
      setLoading(false)
      dirtyRef.current = false
    }
  }
  // Silent refresh — updates settings without triggering the loading spinner.
  // Skipped entirely when the user has unsaved changes.
  const silentLoad = async () => {
    if (dirtyRef.current) return
    try { setSettings(await api.getSettings()) } catch {}
  }
  useEffect(() => { load() }, [])
  // Background poll: re-fetch settings every 60s so the page stays current
  // if another admin saves changes or a CLI update is made to the DB.
  useEffect(() => {
    const t = setInterval(silentLoad, 60_000)
    return () => clearInterval(t)
  }, [])

  const set = (key: string, value: unknown) => {
    dirtyRef.current = true
    setSettings(s => ({ ...s, [key]: value }))
  }

  const str  = (k: string, fallback = '') => (settings[k] as string) ?? fallback
  const num  = (k: string, fallback = 0)  => (settings[k] as number) ?? fallback
  const bool = (k: string, fallback = false) => (settings[k] as boolean) ?? fallback

  // Per-tab save helpers
  const generalSave = useSave(['app_name', 'base_url', 'timezone', 'anthropic_api_key', 'ai_model'], settings, load)
  const storageSave = useSave([
    'storage_backend', 'retention_days_raw', 'retention_days_hourly', 'alert_event_retention_days',
  ], settings, load)
  const backupSave = useSave([
    'backup_enabled', 'backup_interval_hours', 'backup_rotation_count', 'backup_path', 'backup_include_clickhouse',
  ], settings, load)
  const [testConnRunning, setTestConnRunning] = useState(false)
  const [testConnResult, setTestConnResult]   = useState<{ ok: boolean; message: string } | null>(null)
  const [cleanupRunning, setCleanupRunning] = useState(false)
  const [cleanupResult, setCleanupResult]   = useState<string | null>(null)
  const [exportRunning, setExportRunning]   = useState(false)
  const [exportError, setExportError]       = useState<string | null>(null)
  const [importFile, setImportFile]         = useState<File | null>(null)
  const [importRunning, setImportRunning]   = useState(false)
  const [importResult, setImportResult]     = useState<Record<string, string> | null>(null)
  const [importError, setImportError]       = useState<string | null>(null)
  const [backupRunning, setBackupRunning]   = useState(false)
  const [backupResult, setBackupResult]     = useState<string | null>(null)
  const [backups, setBackups]               = useState<Array<{ name: string; path: string; size_bytes: number; files: string[] }>>([])
  const [backupsLoaded, setBackupsLoaded]   = useState(false)

  const testConnection = async () => {
    setTestConnRunning(true)
    setTestConnResult(null)
    try {
      const r = await api.testStorageConnection()
      setTestConnResult({ ok: r.ok, message: r.message })
    } catch (e: any) {
      setTestConnResult({ ok: false, message: e.message || 'Request failed' })
    } finally {
      setTestConnRunning(false)
    }
  }

  const runCleanup = async () => {
    setCleanupRunning(true)
    setCleanupResult(null)
    try {
      const r = await api.runCleanup()
      const parts: string[] = []
      if (r.flows_eligible > 0)
        parts.push(`${r.flows_eligible.toLocaleString()} flows queued for deletion`)
      else
        parts.push('No flows beyond retention threshold')
      if (r.hourly_eligible > 0)
        parts.push(`${r.hourly_eligible.toLocaleString()} hourly rollup rows queued`)
      if (r.alert_events_deleted > 0)
        parts.push(`${r.alert_events_deleted} alert events purged`)
      setCleanupResult(parts.join(' · '))
    } catch (e: any) {
      setCleanupResult(`Error: ${e.message}`)
    } finally {
      setCleanupRunning(false)
    }
  }
  const runBackupNow = async () => {
    setBackupRunning(true)
    setBackupResult(null)
    try {
      const r = await api.runBackupNow()
      setBackupResult(`Saved to ${r.path} — ${r.files.join(', ')}`)
      const list = await api.listBackups()
      setBackups(list)
      setBackupsLoaded(true)
    } catch (e: any) {
      setBackupResult(`Error: ${e.message}`)
    } finally {
      setBackupRunning(false)
    }
  }

  const loadBackups = async () => {
    try {
      const list = await api.listBackups()
      setBackups(list)
      setBackupsLoaded(true)
    } catch { /* ignore */ }
  }

  const runImport = async () => {
    if (!importFile) return
    setImportRunning(true)
    setImportResult(null)
    setImportError(null)
    try {
      const result = await api.importBundle(importFile)
      setImportResult(result)
    } catch (e: any) {
      setImportError(e.message || 'Import failed')
    } finally {
      setImportRunning(false)
    }
  }

  const runExport = async () => {
    setExportRunning(true)
    setExportError(null)
    try {
      const { blob, filename } = await api.exportConfig()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setExportError(e.message || 'Export failed')
    } finally {
      setExportRunning(false)
    }
  }

  const ingestSave  = useSave([
    'ingest_method', 'ingest_token', 'ingest_http_port',
    'ingest_udp_port_netflow', 'ingest_udp_port_sflow',
    'allowed_hosts', 'ws_stream_raw_flows', 'ws_max_raw_flows',
  ], settings, load)
  const authSave = useSave([
    'auth_local_enabled', 'session_timeout_minutes',
    'okta_saml_enabled', 'okta_saml_idp_entity_id', 'okta_saml_idp_sso_url',
    'okta_saml_idp_cert', 'okta_saml_sp_entity_id', 'okta_saml_sp_cert', 'okta_saml_sp_key',
  ], settings, load)
  const notifySave = useSave([
    'notify_slack_enabled', 'notify_slack_webhook_url', 'notify_slack_channel',
    'notify_email_enabled', 'notify_email_smtp_host', 'notify_email_smtp_port',
    'notify_email_smtp_tls', 'notify_email_username', 'notify_email_password',
    'notify_email_from', 'notify_email_default_to',
    'notify_pagerduty_enabled', 'notify_pagerduty_integration_key',
    'notify_webhook_enabled', 'notify_webhook_url',
    'notify_webhook_method', 'notify_webhook_payload_template',
    'notify_tracecat_enabled', 'notify_tracecat_webhook_url', 'notify_tracecat_api_token',
  ], settings, load)
  const integrationsSave = useSave(
    ['lucid_api_token', 'ssl_enabled', 'ssl_certfile', 'ssl_keyfile'],
    settings, load
  )

  const { tick } = useAutoRefresh()
  useEffect(() => { if (tick > 0) silentLoad() }, [tick])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 text-white">
        <p className="text-sm">Loading settings…</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold text-white">Settings</h1>

      {/* Tab bar */}
      <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1 w-fit overflow-x-auto">
        {TABS.filter(t => !t.adminOnly || isAdmin).map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`text-sm px-4 py-1.5 rounded-lg whitespace-nowrap transition-colors ${
              tab === t.id ? 'bg-gray-700 text-white' : 'text-white hover:text-white'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* General */}
      {tab === 'general' && (
        <Section title="General" onSave={generalSave.save} saving={generalSave.saving} saved={generalSave.saved} error={generalSave.error}>
          <Field label="App name" hint="Displayed in browser tab and header">
            <TextInput value={str('app_name', 'pktLog')} onChange={v => set('app_name', v)} />
          </Field>
          <Field label="Base URL" hint="Used for redirect URIs and notification links">
            <TextInput value={str('base_url')} onChange={v => set('base_url', v)} placeholder="http://172.23.80.5:8768" />
          </Field>
          <Field label="Timezone" hint="Affects display of timestamps in the UI">
            <SelectInput
              value={str('timezone', 'UTC')}
              onChange={v => set('timezone', v)}
              options={[
                { value: 'UTC', label: 'UTC' },
                { value: 'America/New_York', label: 'Eastern (ET)' },
                { value: 'America/Chicago', label: 'Central (CT)' },
                { value: 'America/Denver', label: 'Mountain (MT)' },
                { value: 'America/Los_Angeles', label: 'Pacific (PT)' },
              ]}
            />
          </Field>

          <div className="pt-4 pb-1">
            <p className="text-xs font-semibold text-white uppercase tracking-wider">
              AI Assistant (Claude)
            </p>
          </div>
          <Field
            label="Anthropic API key"
            hint="Required for the in-app AI assistant. Get a key at console.anthropic.com. Separate from Claude Enterprise."
          >
            <TextInput
              value={str('anthropic_api_key')}
              onChange={v => set('anthropic_api_key', v)}
              placeholder="sk-ant-…"
              secret
              mono
            />
          </Field>
          <Field label="AI model" hint="Model used for the assistant. Haiku is fast and cost-effective.">
            <SelectInput
              value={str('ai_model', 'claude-haiku-4-5-20251001')}
              onChange={v => set('ai_model', v)}
              options={[
                { value: 'claude-haiku-4-5-20251001', label: 'Claude Haiku (fast, low cost)' },
                { value: 'claude-sonnet-4-6', label: 'Claude Sonnet (balanced)' },
                { value: 'claude-opus-4-8', label: 'Claude Opus (most capable)' },
              ]}
            />
          </Field>
          <RestartServiceRow />
        </Section>
      )}

      {/* Storage */}
      {tab === 'storage' && (
        <Section title="Storage" onSave={storageSave.save} saving={storageSave.saving} saved={storageSave.saved} error={storageSave.error}>
          <Field label="Backend" hint="DuckDB is the default; ClickHouse requires a separate installation. A service restart is required after changing this setting.">
            <SelectInput
              value={str('storage_backend', 'duckdb')}
              onChange={v => set('storage_backend', v)}
              options={[
                { value: 'duckdb', label: 'DuckDB (default)' },
                { value: 'clickhouse', label: 'ClickHouse (requires separate install)' },
              ]}
            />
          </Field>
          <Field label="Test connection" hint="Verify the backend is reachable and the flows table exists">
            <div className="flex items-center gap-3 flex-wrap">
              <button
                onClick={testConnection}
                disabled={testConnRunning}
                className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm rounded-lg px-4 py-2 transition-colors"
              >
                {testConnRunning ? 'Testing…' : 'Test Connection'}
              </button>
              {testConnResult && (
                <span className={`text-xs ${testConnResult.ok ? 'text-green-400' : 'text-red-400'}`}>
                  {testConnResult.ok ? '✓ ' : '✗ '}{testConnResult.message}
                </span>
              )}
            </div>
          </Field>
          <Field label="Raw flow retention" hint="Days to keep individual flow records">
            <div className="flex items-center gap-3">
              <NumberInput value={num('retention_days_raw', 90)} onChange={v => set('retention_days_raw', v)} min={1} max={3650} />
              <span className="text-sm text-white">days</span>
            </div>
          </Field>
          <Field label="Hourly rollup retention" hint="Days to keep per-hour aggregated data">
            <div className="flex items-center gap-3">
              <NumberInput value={num('retention_days_hourly', 365)} onChange={v => set('retention_days_hourly', v)} min={1} max={3650} />
              <span className="text-sm text-white">days</span>
            </div>
          </Field>
          <Field label="Alert event retention" hint="Days to keep fired alert events and notification logs before auto-purge">
            <div className="flex items-center gap-3">
              <NumberInput value={num('alert_event_retention_days', 90)} onChange={v => set('alert_event_retention_days', v)} min={1} max={3650} />
              <span className="text-sm text-white">days</span>
            </div>
          </Field>
          <Field label="Manual cleanup" hint="Immediately apply current retention settings — ClickHouse TTL mutation is queued asynchronously">
            <div className="flex items-center gap-3 flex-wrap">
              <button
                onClick={runCleanup}
                disabled={cleanupRunning}
                className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm rounded-lg px-4 py-2 transition-colors"
              >
                {cleanupRunning ? 'Running…' : 'Run Cleanup Now'}
              </button>
              {cleanupResult && (
                <span className={`text-xs ${cleanupResult.startsWith('Error') ? 'text-red-400' : 'text-green-400'}`}>
                  {cleanupResult}
                </span>
              )}
            </div>
          </Field>
        </Section>
      )}

      {/* Backup */}
      {tab === 'backup' && (
        <Section title="Backup" onSave={backupSave.save} saving={backupSave.saving} saved={backupSave.saved} error={backupSave.error}>
          <Field label="Auto backup" hint="Run a scheduled backup on the O2 server at the configured interval">
            <Toggle value={bool('backup_enabled')} onChange={v => set('backup_enabled', v)} />
          </Field>
          <Field label="Interval" hint="Hours between automatic backup runs">
            <div className="flex items-center gap-3">
              <NumberInput value={num('backup_interval_hours', 24)} onChange={v => set('backup_interval_hours', v)} min={1} max={720} />
              <span className="text-sm text-white">hours</span>
            </div>
          </Field>
          <Field label="Rotation count" hint="Number of snapshots to keep — oldest deleted when exceeded">
            <NumberInput value={num('backup_rotation_count', 5)} onChange={v => set('backup_rotation_count', v)} min={1} max={100} />
          </Field>
          <Field label="Backup path" hint="Directory on O2 where snapshots are stored">
            <TextInput value={str('backup_path', '/mnt/software/pktlog_backups')} onChange={v => set('backup_path', v)} mono />
          </Field>
          <Field label="Include ClickHouse flows" hint="Export full flow history into each snapshot (can be large)">
            <Toggle value={bool('backup_include_clickhouse', true)} onChange={v => set('backup_include_clickhouse', v)} />
          </Field>
          <Field label="Manual backup" hint="Trigger a backup run immediately using current settings">
            <div className="space-y-3">
              <div className="flex items-center gap-3 flex-wrap">
                <button
                  onClick={runBackupNow}
                  disabled={backupRunning}
                  className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm rounded-lg px-4 py-2 transition-colors"
                >
                  {backupRunning ? 'Running…' : 'Run Backup Now'}
                </button>
                {!backupsLoaded && !backupRunning && (
                  <button onClick={loadBackups} className="text-xs text-white hover:text-white underline">
                    Show snapshots
                  </button>
                )}
              </div>
              {backupResult && (
                <p className={`text-xs ${backupResult.startsWith('Error') ? 'text-red-400' : 'text-green-400'}`}>
                  {backupResult}
                </p>
              )}
              {backupsLoaded && (
                <div className="space-y-1">
                  {backups.length === 0 ? (
                    <p className="text-xs text-white">No snapshots found.</p>
                  ) : backups.map(b => (
                    <div key={b.name} className="flex items-center gap-3 text-xs text-white">
                      <span className="font-mono">{b.name}</span>
                      <span className="text-white">{(b.size_bytes / 1024 / 1024).toFixed(1)} MB</span>
                      <span className="text-white">{b.files.join(', ')}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Field>
          <Field label="Export bundle" hint="Download pktlog.db + config.yaml + ClickHouse flow history as a .tar.gz. Large datasets may take a minute to generate.">
            <div className="flex items-center gap-3 flex-wrap">
              <button
                onClick={runExport}
                disabled={exportRunning}
                className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm rounded-lg px-4 py-2 transition-colors"
              >
                {exportRunning ? 'Generating…' : 'Download Export'}
              </button>
              {exportError && (
                <span className="text-xs text-red-400">{exportError}</span>
              )}
            </div>
          </Field>
          <Field label="Restore from bundle" hint="Upload a pktlog export .tar.gz to restore SQLite, config, and flow history. Restart service after restore for config changes to take effect.">
            <div className="space-y-3">
              <div className="flex items-center gap-3 flex-wrap">
                <label className="bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg px-4 py-2 transition-colors cursor-pointer">
                  {importFile ? importFile.name : 'Choose .tar.gz…'}
                  <input
                    type="file"
                    accept=".tar.gz,.tgz"
                    className="hidden"
                    onChange={e => {
                      setImportFile(e.target.files?.[0] ?? null)
                      setImportResult(null)
                      setImportError(null)
                    }}
                  />
                </label>
                <button
                  onClick={runImport}
                  disabled={!importFile || importRunning}
                  className="bg-amber-700 hover:bg-amber-600 disabled:opacity-50 text-white text-sm rounded-lg px-4 py-2 transition-colors"
                >
                  {importRunning ? 'Restoring…' : 'Restore'}
                </button>
              </div>
              {importError && (
                <p className="text-xs text-red-400">{importError}</p>
              )}
              {importResult && (
                <div className="text-xs space-y-1">
                  {Object.entries(importResult).map(([k, v]) => (
                    <p key={k}>
                      <span className="text-white capitalize">{k}:</span>{' '}
                      <span className={v.startsWith('error') ? 'text-red-400' : 'text-green-400'}>{v}</span>
                    </p>
                  ))}
                  <p className="text-amber-400 mt-1">Restart the service to apply any config changes.</p>
                </div>
              )}
            </div>
          </Field>
        </Section>
      )}

      {/* Ingest */}
      {tab === 'ingest' && (
        <Section title="Ingest" onSave={ingestSave.save} saving={ingestSave.saving} saved={ingestSave.saved} error={ingestSave.error}>
          <Field label="Ingest method" hint="HTTP POST is recommended; requires no firewall changes">
            <SelectInput
              value={str('ingest_method', 'http')}
              onChange={v => set('ingest_method', v)}
              options={[
                { value: 'http', label: 'HTTP POST (recommended)' },
                { value: 'udp', label: 'Direct UDP' },
                { value: 'both', label: 'Both' },
              ]}
            />
          </Field>
          <Field label="Ingest token" hint="Bearer token required for HTTP POST endpoint. Leave blank to show current (masked).">
            <TextInput
              value={str('ingest_token')}
              onChange={v => set('ingest_token', v)}
              placeholder="Enter new token to change…"
              secret
              mono
            />
          </Field>
          <Field label="HTTP port" hint="Port pktLog listens on">
            <NumberInput value={num('ingest_http_port', 8768)} onChange={v => set('ingest_http_port', v)} min={1} max={65535} />
          </Field>
          <Field label="UDP NetFlow port">
            <NumberInput value={num('ingest_udp_port_netflow', 2055)} onChange={v => set('ingest_udp_port_netflow', v)} min={1} max={65535} />
          </Field>
          <Field label="UDP sFlow port">
            <NumberInput value={num('ingest_udp_port_sflow', 6343)} onChange={v => set('ingest_udp_port_sflow', v)} min={1} max={65535} />
          </Field>
          <Field label="Allowed source IPs" hint="Comma-separated IPs or CIDRs. Empty = allow all.">
            <TextInput
              value={Array.isArray(settings['allowed_hosts']) ? (settings['allowed_hosts'] as string[]).join(', ') : ''}
              onChange={v => set('allowed_hosts', v.split(',').map(s => s.trim()).filter(Boolean))}
              placeholder="172.23.80.11, 10.56.57.181"
              mono
            />
          </Field>

          <div className="pt-4 pb-1">
            <p className="text-xs font-semibold text-white uppercase tracking-wider">
              WebSocket Live Push
            </p>
          </div>
          <Field label="Stream raw flows" hint="Push each ingested flow batch over WebSocket to connected browsers. Disable if bandwidth is a concern.">
            <Toggle value={bool('ws_stream_raw_flows')} onChange={v => set('ws_stream_raw_flows', v)} />
          </Field>
          {bool('ws_stream_raw_flows') && (
            <Field label="Max flows per push" hint="Cap the number of flow records sent in each WebSocket message (1–1000)">
              <div className="flex items-center gap-3">
                <NumberInput value={num('ws_max_raw_flows', 100)} onChange={v => set('ws_max_raw_flows', v)} min={1} max={1000} />
                <span className="text-sm text-white">flows</span>
              </div>
            </Field>
          )}
        </Section>
      )}

      {/* Auth */}
      {tab === 'auth' && (
        <Section title="Authentication" onSave={authSave.save} saving={authSave.saving} saved={authSave.saved} error={authSave.error}>
          <Field label="Local auth" hint="Username/password login using local accounts">
            <Toggle value={bool('auth_local_enabled', true)} onChange={v => set('auth_local_enabled', v)} />
          </Field>
          <Field label="Session timeout">
            <div className="flex items-center gap-3">
              <NumberInput value={num('session_timeout_minutes', 480)} onChange={v => set('session_timeout_minutes', v)} min={5} max={10080} />
              <span className="text-sm text-white">minutes</span>
            </div>
          </Field>

          <div className="pt-4 pb-2">
            <p className="text-xs font-semibold text-white uppercase tracking-wider">Okta SAML 2.0 SSO</p>
          </div>
          <Field label="Enable SAML SSO">
            <Toggle value={bool('okta_saml_enabled')} onChange={v => set('okta_saml_enabled', v)} />
          </Field>
          {bool('okta_saml_enabled') && (
            <>
              {/* ── Metadata paste helper ── */}
              <Field label="Paste IdP Metadata XML" hint="Paste the full XML from Okta → Sign On → Identity Provider metadata. Fields below will auto-fill.">
                <MetadataPasteBox onParsed={(r) => {
                  if (r.entity_id) set('okta_saml_idp_entity_id', r.entity_id)
                  if (r.sso_url)   set('okta_saml_idp_sso_url', r.sso_url)
                  if (r.cert)      set('okta_saml_idp_cert', r.cert)
                }} />
              </Field>

              <Field label="IdP Entity ID" hint="From Okta metadata: Identity Provider Issuer">
                <TextInput value={str('okta_saml_idp_entity_id')} onChange={v => set('okta_saml_idp_entity_id', v)} placeholder="http://www.okta.com/..." mono />
              </Field>
              <Field label="IdP SSO URL" hint="From Okta metadata: Identity Provider Single Sign-On URL">
                <TextInput value={str('okta_saml_idp_sso_url')} onChange={v => set('okta_saml_idp_sso_url', v)} placeholder="https://yourorg.okta.com/app/.../sso/saml" mono />
              </Field>
              <Field label="IdP X.509 Certificate" hint="PEM headers are stripped automatically">
                <CertTextarea value={str('okta_saml_idp_cert')} onChange={v => set('okta_saml_idp_cert', v)} rows={4} secret />
              </Field>
              <Field label="SP Entity ID" hint="Leave blank to use the auto-generated metadata URL">
                <TextInput value={str('okta_saml_sp_entity_id')} onChange={v => set('okta_saml_sp_entity_id', v)} placeholder={`${str('base_url')}/api/auth/saml/metadata`} mono />
              </Field>
              <Field label="ACS URL (read-only)" hint="Register this URL as the Single Sign-On URL in your Okta app">
                <div className="flex items-center gap-2">
                  <input
                    readOnly
                    value={`${str('base_url')}/api/auth/saml/callback`}
                    className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-400 font-mono cursor-default"
                  />
                  <a
                    href={`${str('base_url')}/api/auth/saml/metadata`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-blue-400 hover:text-blue-300 whitespace-nowrap"
                  >
                    View SP metadata ↗
                  </a>
                </div>
              </Field>
              <Field label="SP Certificate" hint="Optional: for signed authentication requests">
                <CertTextarea value={str('okta_saml_sp_cert')} onChange={v => set('okta_saml_sp_cert', v)} rows={3} placeholder="Leave blank if not signing requests" secret />
              </Field>
              <Field label="SP Private Key" hint="Optional: private key for signing requests (kept secret)">
                <CertTextarea value={str('okta_saml_sp_key')} onChange={v => set('okta_saml_sp_key', v)} rows={3} placeholder="Leave blank if not signing requests" secret />
              </Field>
            </>
          )}
        </Section>
      )}

      {/* Notifications */}
      {tab === 'notifications' && (
        <Section title="Notifications" onSave={notifySave.save} saving={notifySave.saving} saved={notifySave.saved} error={notifySave.error}>
          {/* Slack */}
          <div className="pt-2 pb-1">
            <p className="text-xs font-semibold text-white uppercase tracking-wider">Slack</p>
          </div>
          <Field label="Enable Slack">
            <Toggle value={bool('notify_slack_enabled')} onChange={v => set('notify_slack_enabled', v)} />
          </Field>
          {bool('notify_slack_enabled') && (
            <>
              <Field label="Webhook URL">
                <TextInput value={str('notify_slack_webhook_url')} onChange={v => set('notify_slack_webhook_url', v)} placeholder="https://hooks.slack.com/services/…" secret mono />
              </Field>
              <Field label="Channel" hint="Override channel (optional)">
                <TextInput value={str('notify_slack_channel', '#alerts')} onChange={v => set('notify_slack_channel', v)} placeholder="#alerts" />
              </Field>
              <SendTestButton channel="slack" />
            </>
          )}

          {/* Email */}
          <div className="pt-4 pb-1">
            <p className="text-xs font-semibold text-white uppercase tracking-wider">Email (SMTP)</p>
          </div>
          <Field label="Enable email">
            <Toggle value={bool('notify_email_enabled')} onChange={v => set('notify_email_enabled', v)} />
          </Field>
          {bool('notify_email_enabled') && (
            <>
              <Field label="SMTP host">
                <TextInput value={str('notify_email_smtp_host')} onChange={v => set('notify_email_smtp_host', v)} placeholder="smtp.yourorg.com" mono />
              </Field>
              <Field label="SMTP port">
                <NumberInput value={num('notify_email_smtp_port', 587)} onChange={v => set('notify_email_smtp_port', v)} min={1} max={65535} />
              </Field>
              <Field label="Use TLS">
                <Toggle value={bool('notify_email_smtp_tls', true)} onChange={v => set('notify_email_smtp_tls', v)} />
              </Field>
              <Field label="Username">
                <TextInput value={str('notify_email_username')} onChange={v => set('notify_email_username', v)} mono />
              </Field>
              <Field label="Password">
                <TextInput value={str('notify_email_password')} onChange={v => set('notify_email_password', v)} secret />
              </Field>
              <Field label="From address">
                <TextInput value={str('notify_email_from')} onChange={v => set('notify_email_from', v)} placeholder="pktlog@yourorg.com" />
              </Field>
              <Field label="Default to" hint="Comma-separated email addresses">
                <TextInput
                  value={Array.isArray(settings['notify_email_default_to']) ? (settings['notify_email_default_to'] as string[]).join(', ') : ''}
                  onChange={v => set('notify_email_default_to', v.split(',').map(s => s.trim()).filter(Boolean))}
                  placeholder="noc@yourorg.com, security@yourorg.com"
                />
              </Field>
              <SendTestButton channel="email" />
            </>
          )}

          {/* PagerDuty */}
          <div className="pt-4 pb-1">
            <p className="text-xs font-semibold text-white uppercase tracking-wider">PagerDuty</p>
          </div>
          <Field label="Enable PagerDuty">
            <Toggle value={bool('notify_pagerduty_enabled')} onChange={v => set('notify_pagerduty_enabled', v)} />
          </Field>
          {bool('notify_pagerduty_enabled') && (
            <>
              <Field label="Integration key" hint="Events API v2 integration key">
                <TextInput value={str('notify_pagerduty_integration_key')} onChange={v => set('notify_pagerduty_integration_key', v)} secret mono />
              </Field>
              <SendTestButton channel="pagerduty" />
            </>
          )}

          {/* Webhook */}
          <div className="pt-4 pb-1">
            <p className="text-xs font-semibold text-white uppercase tracking-wider">Webhook</p>
          </div>
          <Field label="Enable webhook">
            <Toggle value={bool('notify_webhook_enabled')} onChange={v => set('notify_webhook_enabled', v)} />
          </Field>
          {bool('notify_webhook_enabled') && (
            <>
              <Field label="URL">
                <TextInput value={str('notify_webhook_url')} onChange={v => set('notify_webhook_url', v)} placeholder="https://yourservice.com/pktlog-alert" mono />
              </Field>
              <Field label="Method">
                <SelectInput
                  value={str('notify_webhook_method', 'POST')}
                  onChange={v => set('notify_webhook_method', v)}
                  options={[{ value: 'POST', label: 'POST' }, { value: 'PUT', label: 'PUT' }]}
                />
              </Field>
              <Field label="Payload template" hint="Jinja2 template; available vars: alert_name, message, severity, fired_at">
                <textarea
                  value={str('notify_webhook_payload_template')}
                  onChange={e => set('notify_webhook_payload_template', e.target.value)}
                  rows={4}
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </Field>
              <SendTestButton channel="webhook" />
            </>
          )}

          {/* TraceCat */}
          <div className="pt-2 pb-1">
            <p className="text-sm font-medium text-white">TraceCat SOAR</p>
          </div>
          <Field label="Enable TraceCat">
            <Toggle value={bool('notify_tracecat_enabled')} onChange={v => set('notify_tracecat_enabled', v)} />
          </Field>
          {bool('notify_tracecat_enabled') && (
            <>
              <Field label="Webhook URL" hint="Paste the workflow webhook URL from TraceCat → Workflow → Trigger">
                <TextInput value={str('notify_tracecat_webhook_url')} onChange={v => set('notify_tracecat_webhook_url', v)} placeholder="https://tracecat.yourorg.com/api/v1/webhooks/…" mono />
              </Field>
              <Field label="API token" hint="Bearer token for TraceCat API authentication (optional if webhook is public)">
                <TextInput value={str('notify_tracecat_api_token')} onChange={v => set('notify_tracecat_api_token', v)} secret />
              </Field>
              <SendTestButton channel="tracecat" />
            </>
          )}
        </Section>
      )}

      {/* Devices tab */}
      {tab === 'devices' && <DevicesTab />}

      {/* Users tab — admin only */}
      {tab === 'users' && isAdmin && <UsersTab />}

      {/* Integrations */}
      {tab === 'integrations' && (
        <Section title="Integrations" onSave={integrationsSave.save} saving={integrationsSave.saving} saved={integrationsSave.saved} error={integrationsSave.error}>

          <div className="pt-2 pb-1">
            <p className="text-xs font-semibold text-white uppercase tracking-wider">Lucidchart</p>
          </div>
          <Field label="API token" hint="Personal Access Token from lucid.co → Account → API Tokens. Required for topology export to Lucidchart.">
            <TextInput
              value={str('lucid_api_token')}
              onChange={v => set('lucid_api_token', v)}
              placeholder="eyJ…"
              secret
              mono
            />
          </Field>

          <div className="pt-4 pb-1">
            <p className="text-xs font-semibold text-white uppercase tracking-wider">SSL / TLS</p>
          </div>
          <div className="py-3">
            <SslPanel />
          </div>
        </Section>
      )}
    </div>
  )
}

// ── SSL certificate upload ─────────────────────────────────────────────────────

function SslDropZone({ label, accept, file, onFile, dragging, onDrag }: {
  label: string; accept: string; file: File | null
  onFile: (f: File) => void; dragging: boolean; onDrag: (v: boolean) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <div
      className={`flex-1 border-2 border-dashed rounded-xl p-5 flex flex-col items-center justify-center gap-2 cursor-pointer transition-colors select-none ${
        dragging    ? 'border-blue-500 bg-blue-500/10'
        : file      ? 'border-green-600 bg-green-600/10'
        : 'border-gray-700 hover:border-gray-600'
      }`}
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); onDrag(true) }}
      onDragLeave={() => onDrag(false)}
      onDrop={e => { e.preventDefault(); onDrag(false); const f = e.dataTransfer.files[0]; if (f) onFile(f) }}
    >
      <input ref={inputRef} type="file" accept={accept} className="hidden"
        onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f) }} />
      {file ? (
        <>
          <svg className="w-6 h-6 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
          </svg>
          <p className="text-xs font-medium text-green-400 text-center break-all">{file.name}</p>
          <p className="text-xs text-white">{(file.size / 1024).toFixed(1)} KB</p>
        </>
      ) : (
        <>
          <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m6.75 12H9m1.5-12H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"/>
          </svg>
          <p className="text-xs font-medium text-white text-center">{label}</p>
          <p className="text-xs text-white">Drop or click to browse</p>
        </>
      )}
    </div>
  )
}

function SslPanel() {
  const [status, setStatus]       = useState<SslStatus | null>(null)
  const [mode, setMode]           = useState<'pem' | 'pfx'>('pfx')
  // PEM mode
  const [certFile, setCertFile]   = useState<File | null>(null)
  const [keyFile,  setKeyFile]    = useState<File | null>(null)
  const [certDrag, setCertDrag]   = useState(false)
  const [keyDrag,  setKeyDrag]    = useState(false)
  // PFX mode
  const [pfxFile,  setPfxFile]    = useState<File | null>(null)
  const [pfxDrag,  setPfxDrag]    = useState(false)
  const [passphrase, setPassphrase] = useState('')
  // Shared
  const [uploading, setUploading] = useState(false)
  const [removing,  setRemoving]  = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    api.getSslStatus().then(setStatus).catch(() => setStatus({ installed: false }))
  }, [])

  const uploadPem = async () => {
    if (!certFile || !keyFile) return
    setUploading(true); setMsg(null)
    try {
      const s = await api.uploadSsl(certFile, keyFile)
      setStatus(s); setCertFile(null); setKeyFile(null)
      setMsg({ ok: true, text: 'Certificate installed. Restart the service (General tab) to enable HTTPS.' })
    } catch (e: any) {
      setMsg({ ok: false, text: e.message ?? 'Upload failed' })
    } finally { setUploading(false) }
  }

  const uploadPfx = async () => {
    if (!pfxFile || !passphrase) return
    setUploading(true); setMsg(null)
    try {
      const s = await api.uploadSslPfx(pfxFile, passphrase)
      setStatus(s); setPfxFile(null); setPassphrase('')
      setMsg({ ok: true, text: 'Certificate installed from PFX. Restart the service (General tab) to enable HTTPS.' })
    } catch (e: any) {
      setMsg({ ok: false, text: e.message ?? 'Upload failed' })
    } finally { setUploading(false) }
  }

  const remove = async () => {
    setRemoving(true); setMsg(null)
    try {
      await api.deleteSsl()
      setStatus({ installed: false })
      setMsg({ ok: true, text: 'Certificate removed. Restart service to disable HTTPS.' })
    } catch (e: any) {
      setMsg({ ok: false, text: e.message ?? 'Remove failed' })
    } finally { setRemoving(false) }
  }

  const daysLeft = status?.days_until_expiry ?? 9999
  const expColor = daysLeft < 0 ? 'text-red-400' : daysLeft < 30 ? 'text-yellow-400' : 'text-green-400'
  const expBadge = daysLeft < 0 ? 'Expired' : daysLeft < 30 ? `Expires in ${daysLeft}d` : `Valid · ${daysLeft}d left`

  const pemReady = !!(certFile && keyFile)
  const pfxReady = !!(pfxFile && passphrase)

  return (
    <div className="space-y-4">
      {/* Current cert status */}
      {status?.installed ? (
        <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0"></span>
              <span className="text-sm font-medium text-white">Certificate installed</span>
            </div>
            <span className={`text-xs font-medium ${expColor}`}>{expBadge}</span>
          </div>
          {status.subject && <p className="text-xs text-white font-mono">{status.subject}</p>}
          {status.issuer  && <p className="text-xs text-white">Issued by: {status.issuer}</p>}
          {status.expires && <p className="text-xs text-white">Expires: {status.expires}</p>}
          {status.error   && <p className="text-xs text-red-400">Warning: {status.error}</p>}
          <button onClick={remove} disabled={removing}
            className="text-xs text-red-400 hover:text-red-300 disabled:opacity-50 pt-1">
            {removing ? 'Removing…' : '× Remove certificate'}
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-sm text-white">
          <span className="w-2 h-2 rounded-full bg-gray-600 flex-shrink-0"></span>
          No certificate installed · running HTTP
        </div>
      )}

      {/* Mode toggle */}
      <div className="flex items-center gap-1 bg-gray-800 border border-gray-700 rounded-lg p-1 w-fit">
        <button
          onClick={() => setMode('pfx')}
          className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${mode === 'pfx' ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'}`}>
          PFX / P12
        </button>
        <button
          onClick={() => setMode('pem')}
          className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${mode === 'pem' ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'}`}>
          PEM (cert + key)
        </button>
      </div>

      {mode === 'pfx' ? (
        /* ── PFX mode ── */
        <div className="space-y-3">
          <SslDropZone label="PFX / P12 file (.pfx, .p12)" accept=".pfx,.p12"
            file={pfxFile} onFile={setPfxFile} dragging={pfxDrag} onDrag={setPfxDrag} />
          <div>
            <label className="block text-xs text-gray-400 mb-1">Passphrase</label>
            <input
              type="password"
              value={passphrase}
              onChange={e => setPassphrase(e.target.value)}
              placeholder="PFX passphrase"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex items-center gap-3">
            <button onClick={uploadPfx} disabled={!pfxReady || uploading}
              className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-40 transition-colors">
              {uploading ? 'Uploading…' : 'Upload & Install'}
            </button>
            {!pfxReady && (
              <span className="text-xs text-gray-500">
                {!pfxFile ? 'Drop a PFX file above' : 'Enter the passphrase'}
              </span>
            )}
          </div>
        </div>
      ) : (
        /* ── PEM mode ── */
        <div className="space-y-3">
          <div className="flex gap-3">
            <SslDropZone label="Certificate (.crt / .pem)" accept=".crt,.pem,.cer"
              file={certFile} onFile={setCertFile} dragging={certDrag} onDrag={setCertDrag} />
            <SslDropZone label="Private Key (.key / .pem)" accept=".key,.pem"
              file={keyFile} onFile={setKeyFile} dragging={keyDrag} onDrag={setKeyDrag} />
          </div>
          <div className="flex items-center gap-3">
            <button onClick={uploadPem} disabled={!pemReady || uploading}
              className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-40 transition-colors">
              {uploading ? 'Uploading…' : 'Upload & Install'}
            </button>
            {!pemReady && (
              <span className="text-xs text-gray-500">Drop both cert and key files above</span>
            )}
          </div>
        </div>
      )}

      {msg && <p className={`text-xs ${msg.ok ? 'text-green-400' : 'text-red-400'}`}>{msg.text}</p>}

      <p className="text-xs text-gray-500 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 leading-relaxed">
        After uploading, restart the service from the <strong className="text-white">General</strong> tab.
        The service wrapper auto-detects cert files on startup — no additional config needed.
      </p>
    </div>
  )
}

// ── Devices tab — managed devices + live collector stats ──────────────────────
interface Device { id: number; ip: string; name: string; site: string; notes: string; allowed: boolean }

const STATUS_DOT: Record<string, string> = {
  online: 'bg-green-400', stale: 'bg-yellow-400', offline: 'bg-red-400',
}
const STATUS_TEXT: Record<string, string> = {
  online: 'text-green-400', stale: 'text-yellow-400', offline: 'text-red-400',
}

function samplerStatus(lastSeen: string | null | undefined): 'online' | 'stale' | 'offline' {
  if (!lastSeen) return 'offline'
  const ago = (Date.now() - new Date(lastSeen).getTime()) / 1000
  if (ago < 120) return 'online'
  if (ago < 600) return 'stale'
  return 'offline'
}

function fmtDevBytes(b: number): string {
  if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB'
  if (b >= 1e6) return (b / 1e6).toFixed(1) + ' MB'
  if (b >= 1e3) return (b / 1e3).toFixed(1) + ' KB'
  return b + ' B'
}

function fmtRelative(ts: string | null | undefined): string {
  if (!ts) return '—'
  const secs = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function DevicesTab() {
  const [devices, setDevices]     = useState<Device[]>([])
  const [summaries, setSummaries] = useState<DeviceSummary[]>([])
  const [editing, setEditing]     = useState<Device | null>(null)
  const [adding, setAdding]       = useState(false)
  const [saving, setSaving]       = useState(false)
  const [error, setError]         = useState('')
  const [csvImporting, setCsvImporting] = useState(false)
  const [csvResult, setCsvResult]       = useState<{ created: number; updated: number; skipped: number; errors: Array<{ row: number; reason: string }> } | null>(null)
  const [csvError, setCsvError]         = useState<string | null>(null)
  const [deviceFilter, setDeviceFilter] = useState('')
  const [unknownSamplers, setUnknownSamplers] = useState<Array<{ sampler_ip: string; flows_per_sec: number; last_seen: string }>>([])
  const [dismissedSamplers, setDismissedSamplers] = useState<Array<{ sampler_ip: string; dismissed_at: string }>>([])
  const [knownSites, setKnownSites] = useState<string[]>([])
  const [registeringIp, setRegisteringIp] = useState<string | null>(null)
  const [dismissedExpanded, setDismissedExpanded] = useState(false)

  const EMPTY: Device = { id: 0, ip: '', name: '', site: '', notes: '', allowed: true }

  const loadDevices   = async () => { try { setDevices(await api.getDevices()) } catch {} }
  const loadSummaries = async () => { try { setSummaries(await api.getDeviceSummaries()) } catch {} }
  const loadUnknown   = async () => {
    try {
      const [data, sites] = await Promise.all([api.getUnknownSamplers(), api.getDeviceSites()])
      setUnknownSamplers(data.unknown)
      setDismissedSamplers(data.dismissed)
      setKnownSites(sites)
    } catch {}
  }

  useEffect(() => {
    loadDevices()
    loadSummaries()
    loadUnknown()
    const t1 = setInterval(loadSummaries, 15_000)
    // Refresh device list + unknown samplers every 30s (new registrations, dismissals from another session)
    const t2 = setInterval(() => { loadDevices(); loadUnknown() }, 30_000)
    return () => { clearInterval(t1); clearInterval(t2) }
  }, [])

  const save = async (d: Device) => {
    setSaving(true)
    setError('')
    try {
      const body = { ip: d.ip, name: d.name, site: d.site, notes: d.notes, allowed: d.allowed }
      if (d.id) {
        await api.updateDevice(d.id, body)
      } else {
        await api.createDevice(body)
      }
      setEditing(null)
      setAdding(false)
      await loadDevices()
    } catch (e: any) {
      setError(e.message || 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  const del = async (id: number) => {
    if (!confirm('Remove this device?')) return
    await api.deleteDevice(id)
    await loadDevices()
  }

  const dismiss = async (ip: string) => {
    try { await api.dismissSampler(ip); await loadUnknown() } catch {}
  }
  const reconsider = async (ip: string) => {
    try { await api.undismissSampler(ip); await loadUnknown() } catch {}
  }

  // Inline registration form for unknown samplers
  const InlineRegisterForm = ({ ip, onSaved, onCancel }: { ip: string; onSaved: () => void; onCancel: () => void }) => {
    const [name, setName] = useState('')
    const [site, setSite] = useState(knownSites[0] || '')
    const [customSite, setCustomSite] = useState('')
    const [saving, setSaving] = useState(false)
    const [err, setErr] = useState('')
    const effectiveSite = site === '__custom__' ? customSite : site
    const submit = async () => {
      setSaving(true); setErr('')
      try {
        await api.createDevice({ ip, name, site: effectiveSite, notes: '', allowed: true })
        await loadDevices()
        onSaved()
      } catch (e: any) { setErr(e.message || 'Failed to register') }
      finally { setSaving(false) }
    }
    return (
      <div className="mt-3 space-y-3 border-t border-gray-700 pt-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Name</label>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Core Router"
              className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Site</label>
            {knownSites.length > 0 ? (
              <select value={site} onChange={e => setSite(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500">
                {knownSites.map(s => <option key={s} value={s}>{s}</option>)}
                <option value="__custom__">Custom…</option>
              </select>
            ) : (
              <input value={customSite} onChange={e => setCustomSite(e.target.value)} placeholder="site name"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500" />
            )}
            {site === '__custom__' && (
              <input value={customSite} onChange={e => setCustomSite(e.target.value)} placeholder="site name" className="mt-1 w-full bg-gray-800 border border-gray-700 rounded px-2.5 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500" />
            )}
          </div>
        </div>
        {err && <p className="text-xs text-red-400">{err}</p>}
        <div className="flex gap-2">
          <button onClick={submit} disabled={saving}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs rounded px-3 py-1.5 transition-colors">
            {saving ? 'Saving…' : 'Register'}
          </button>
          <button onClick={onCancel}
            className="text-xs border border-gray-700 text-gray-400 hover:text-white rounded px-3 py-1.5 transition-colors">
            Cancel
          </button>
        </div>
      </div>
    )
  }

  const handleCsvImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setCsvImporting(true)
    setCsvResult(null)
    setCsvError(null)
    try {
      const result = await api.importDevicesCsv(file)
      setCsvResult(result)
      await loadDevices()
    } catch (err: any) {
      setCsvError(err.message || 'Import failed')
    } finally {
      setCsvImporting(false)
    }
  }

  const downloadTemplate = () => {
    const csv = 'ip,name,site,notes,allowed\n192.168.1.1,Core Switch,medical,Main distribution switch,true\n10.0.0.1,Edge Router,dental,,true\n'
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'devices-template.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  // Join: managed devices + live summaries keyed by IP
  const summaryMap = Object.fromEntries(summaries.map(s => [s.sampler_ip, s]))

  const DeviceForm = ({ d }: { d: Device }) => {
    const [form, setForm] = useState<Device>(d)
    const f = <K extends keyof Device>(k: K, v: Device[K]) => setForm(x => ({ ...x, [k]: v }))
    return (
      <tr>
        <td colSpan={8} className="px-4 py-4 bg-gray-800/50">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
            {([['IP', 'ip', '192.168.1.1'], ['Name', 'name', 'Core Switch'], ['Site', 'site', 'medical']] as const).map(([label, key, ph]) => (
              <div key={key}>
                <label className="block text-xs text-white mb-1">{label}</label>
                <input
                  value={form[key] as string}
                  onChange={e => f(key, e.target.value)}
                  placeholder={ph}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
            ))}
            <div className="flex items-end gap-4">
              <div>
                <label className="block text-xs text-white mb-1">Allowed</label>
                <Toggle value={form.allowed} onChange={v => f('allowed', v)} />
              </div>
            </div>
          </div>
          <div className="mb-3">
            <label className="block text-xs text-white mb-1">Notes</label>
            <input
              value={form.notes}
              onChange={e => f('notes', e.target.value)}
              placeholder="Optional notes"
              className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          {error && <p className="text-xs text-red-400 mb-2">{error}</p>}
          <div className="flex gap-2">
            <button onClick={() => save(form)} disabled={saving} className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs rounded px-3 py-1.5">
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => { setEditing(null); setAdding(false) }} className="text-white hover:text-white text-xs border border-gray-700 rounded px-3 py-1.5">
              Cancel
            </button>
          </div>
        </td>
      </tr>
    )
  }

  return (
    <div>
      {/* Unknown Samplers Panel */}
      {(unknownSamplers.length > 0 || dismissedSamplers.length > 0) && (
        <div className="mb-4 bg-gray-900 border border-amber-800/40 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-amber-400 inline-block animate-pulse" />
            <p className="text-sm font-medium text-white">Unknown samplers</p>
            {unknownSamplers.length > 0 && (
              <span className="text-xs bg-amber-900/30 text-amber-400 border border-amber-700/40 rounded px-1.5 py-0.5">
                {unknownSamplers.length} need{unknownSamplers.length === 1 ? 's' : ''} review
              </span>
            )}
          </div>

          {unknownSamplers.length === 0 ? (
            <p className="px-4 py-3 text-sm text-gray-400">All active samplers are registered or dismissed.</p>
          ) : (
            <div className="divide-y divide-gray-800/50">
              {unknownSamplers.map(s => (
                <div key={s.sampler_ip} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-mono text-white">{s.sampler_ip}</p>
                      <p className="text-xs text-gray-400">
                        {s.flows_per_sec.toFixed(1)} flows/s · last seen {fmtRelative(s.last_seen)}
                      </p>
                    </div>
                    {registeringIp !== s.sampler_ip && (
                      <div className="flex gap-2 flex-shrink-0">
                        <button onClick={() => setRegisteringIp(s.sampler_ip)}
                          className="text-xs bg-blue-600 hover:bg-blue-500 text-white rounded px-3 py-1.5 transition-colors">
                          Register
                        </button>
                        <button onClick={() => dismiss(s.sampler_ip)}
                          className="text-xs border border-gray-700 text-gray-400 hover:text-white rounded px-3 py-1.5 transition-colors">
                          Dismiss
                        </button>
                      </div>
                    )}
                  </div>
                  {registeringIp === s.sampler_ip && (
                    <InlineRegisterForm
                      ip={s.sampler_ip}
                      onSaved={async () => { setRegisteringIp(null); await loadUnknown() }}
                      onCancel={() => setRegisteringIp(null)}
                    />
                  )}
                </div>
              ))}
            </div>
          )}

          {dismissedSamplers.length > 0 && (
            <div className="border-t border-gray-800">
              <button
                onClick={() => setDismissedExpanded(!dismissedExpanded)}
                className="w-full px-4 py-2 flex items-center justify-between text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                <span>Dismissed ({dismissedSamplers.length})</span>
                <span>{dismissedExpanded ? '▲' : '▼'}</span>
              </button>
              {dismissedExpanded && (
                <div className="divide-y divide-gray-800/50 border-t border-gray-800">
                  {dismissedSamplers.map(s => (
                    <div key={s.sampler_ip} className="px-4 py-2 flex items-center justify-between">
                      <div>
                        <p className="text-sm font-mono text-gray-400">{s.sampler_ip}</p>
                        <p className="text-xs text-gray-500">dismissed {fmtRelative(s.dismissed_at)}</p>
                      </div>
                      <button onClick={() => reconsider(s.sampler_ip)}
                        className="text-xs text-gray-400 hover:text-white transition-colors">
                        Reconsider
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <p className="text-xs text-gray-500">Live stats refresh every 15s</p>
        <div className="flex items-center gap-2">
          <input
            value={deviceFilter}
            onChange={e => setDeviceFilter(e.target.value)}
            placeholder="Filter devices…"
            className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-gray-600 w-40 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          {deviceFilter && <button onClick={() => setDeviceFilter('')} className="text-xs text-white hover:text-white">✕</button>}
          <button onClick={downloadTemplate} className="text-xs text-white hover:text-white border border-gray-700 hover:border-gray-500 rounded-lg px-3 py-2 transition-colors">
            Download template
          </button>
          <label className={`text-xs rounded-lg px-3 py-2 transition-colors cursor-pointer border ${csvImporting ? 'opacity-50 cursor-not-allowed border-gray-700 text-gray-500' : 'border-gray-700 text-white hover:border-gray-500 hover:text-white'}`}>
            {csvImporting ? 'Importing…' : 'Import CSV'}
            <input type="file" accept=".csv" className="hidden" onChange={handleCsvImport} disabled={csvImporting} />
          </label>
          <button onClick={() => { setAdding(true); setEditing(null) }} className="bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg px-4 py-2">
            + Add device
          </button>
        </div>
      </div>
      {csvResult && (
        <div className="mb-3 text-xs rounded-lg border border-gray-700 bg-gray-800/50 px-4 py-3 space-y-1">
          <p className="text-white font-medium">Import complete</p>
          <p className="text-white">
            <span className="text-green-400">{csvResult.created} created</span>
            {' · '}
            <span className="text-blue-400">{csvResult.updated} updated</span>
            {csvResult.skipped > 0 && <><span className="text-white"> · </span><span className="text-amber-400">{csvResult.skipped} skipped</span></>}
          </p>
          {csvResult.errors.length > 0 && (
            <div className="mt-1 space-y-0.5">
              {csvResult.errors.map((e, i) => (
                <p key={i} className="text-red-400">Row {e.row}: {e.reason}</p>
              ))}
            </div>
          )}
        </div>
      )}
      {csvError && (
        <p className="mb-3 text-xs text-red-400 px-1">{csvError}</p>
      )}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800">
              <th className="px-4 py-3 w-6"></th>
              <th className="px-4 py-3 text-left text-xs font-medium text-white">Device</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-white">Site</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-white">Status</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-white">Flows/s</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-white">Bytes/hr</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-white">Ingest</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {adding && <DeviceForm d={EMPTY} />}
            {(deviceFilter.trim()
              ? devices.filter(d => {
                  const q = deviceFilter.toLowerCase()
                  return d.ip.includes(q) || d.name.toLowerCase().includes(q) || d.site.toLowerCase().includes(q)
                })
              : devices
            ).map(d => {
              const s = summaryMap[d.ip]
              const status = samplerStatus(s?.last_seen)
              return editing?.id === d.id ? (
                <DeviceForm key={`edit-${d.id}`} d={d} />
              ) : (
                <tr key={d.id} className="hover:bg-gray-800/30 transition-colors">
                  <td className="px-4 py-3">
                    <span className={`w-2 h-2 rounded-full inline-block ${STATUS_DOT[status]}`} />
                  </td>
                  <td className="px-4 py-3">
                    <p className="text-white font-medium">{d.name || d.ip}</p>
                    <p className="text-xs font-mono text-blue-300">{d.ip}</p>
                  </td>
                  <td className="px-4 py-3 text-white text-sm">{d.site || '—'}</td>
                  <td className={`px-4 py-3 capitalize text-xs font-medium ${STATUS_TEXT[status]}`}>{status}</td>
                  <td className="px-4 py-3 text-right font-mono text-white text-xs">{s ? s.flows_per_sec.toFixed(1) : '—'}</td>
                  <td className="px-4 py-3 text-right font-mono text-white text-xs">{s ? fmtDevBytes(s.bytes_last_hour) : '—'}</td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded ${d.allowed ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                      {d.allowed ? 'Allowed' : 'Blocked'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-3">
                      <button onClick={() => { setEditing(d); setAdding(false) }} className="text-xs text-white hover:text-blue-400 transition-colors">Edit</button>
                      <button onClick={() => del(d.id)} className="text-xs text-white hover:text-red-400 transition-colors">Remove</button>
                    </div>
                  </td>
                </tr>
              )
            })}
            {devices.length === 0 && !adding && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-sm text-white">No devices yet</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Users tab — local account management (admin only) ─────────────────────────
const ROLES = ['admin', 'viewer', 'analyst']

function badge(active: boolean) {
  return active
    ? 'bg-green-900/40 text-green-400 border border-green-700/40'
    : 'bg-gray-800 text-white border border-gray-700'
}

function roleBadge(role: string) {
  const map: Record<string, string> = {
    admin:   'bg-blue-900/40 text-blue-300 border border-blue-700/40',
    viewer:  'bg-gray-800 text-white border border-gray-700',
    analyst: 'bg-purple-900/40 text-purple-300 border border-purple-700/40',
  }
  return map[role] ?? 'bg-gray-800 text-white border border-gray-700'
}

interface UserModalProps {
  user?: User | null
  onClose: () => void
  onSaved: () => void
}

function UserModal({ user, onClose, onSaved }: UserModalProps) {
  const editing = !!user
  const [form, setForm] = useState<UserIn>({
    username: user?.username ?? '',
    email:    user?.email ?? '',
    role:     user?.role ?? 'viewer',
    password: '',
  })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const set = (k: keyof UserIn, v: string) => setForm(f => ({ ...f, [k]: v }))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!editing && !form.password) { setError('Password required for new users'); return }
    setSaving(true)
    try {
      const payload = { ...form, password: form.password || undefined }
      if (editing) await api.updateUser(user!.id, payload)
      else         await api.createUser(payload)
      onSaved()
    } catch (err: any) {
      setError(err.message ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-md p-6" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-white mb-5">{editing ? `Edit — ${user!.username}` : 'New User'}</h2>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="text-xs text-white block mb-1">Username</label>
            <input value={form.username} onChange={e => set('username', e.target.value)} required
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="text-xs text-white block mb-1">Email</label>
            <input type="email" value={form.email} onChange={e => set('email', e.target.value)} required
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="text-xs text-white block mb-1">
              Password {editing && <span className="text-white">(leave blank to keep current)</span>}
            </label>
            <input type="password" value={form.password} onChange={e => set('password', e.target.value)}
              placeholder={editing ? '••••••••' : 'Required'}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="text-xs text-white block mb-1">Role</label>
            <select value={form.role} onChange={e => set('role', e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          {error && <p className="text-red-400 text-xs">{error}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose}
              className="px-4 py-2 text-sm text-white hover:text-white transition-colors">
              Cancel
            </button>
            <button type="submit" disabled={saving}
              className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors disabled:opacity-50">
              {saving ? 'Saving…' : (editing ? 'Save Changes' : 'Create User')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

interface ResetPwProps { user: User; onClose: () => void }

function ResetPasswordModal({ user, onClose }: ResetPwProps) {
  const [pw, setPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (pw.length < 6) { setErr('Password must be at least 6 characters'); return }
    if (pw !== confirmPw) { setErr('Passwords do not match'); return }
    setSaving(true)
    try {
      await api.resetUserPassword(user.id, pw)
      onClose()
    } catch (e: any) {
      setErr(e.message ?? 'Failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-sm p-6" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-white mb-1">Reset Password</h2>
        <p className="text-sm text-white mb-5">Set a new password for <span className="text-white font-medium">{user.username}</span></p>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="text-xs text-white block mb-1">New Password</label>
            <input type="password" value={pw} onChange={e => setPw(e.target.value)} required autoFocus
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label className="text-xs text-white block mb-1">Confirm Password</label>
            <input type="password" value={confirmPw} onChange={e => setConfirmPw(e.target.value)} required
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
          </div>
          {err && <p className="text-red-400 text-xs">{err}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-white hover:text-white transition-colors">Cancel</button>
            <button type="submit" disabled={saving}
              className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors disabled:opacity-50">
              {saving ? 'Saving…' : 'Set Password'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function UsersTab() {
  const { user: me } = useAuth()
  const [users, setUsers]   = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [modal, setModal]   = useState<'create' | User | null>(null)
  const [confirm, setConfirm] = useState<User | null>(null)
  const [resetPw, setResetPw] = useState<User | null>(null)
  const [error, setError]   = useState('')
  const [userFilter, setUserFilter]     = useState('')
  const [userSortKey, setUserSortKey]   = useState<keyof User | null>(null)
  const [userSortDir, setUserSortDir]   = useState<'asc' | 'desc'>('asc')

  const toggleUserSort = (key: keyof User) => {
    if (userSortKey === key) setUserSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setUserSortKey(key); setUserSortDir('asc') }
  }

  const load = () => {
    setLoading(true)
    api.getUsers().then(setUsers).catch(e => setError(e.message)).finally(() => setLoading(false))
  }

  useEffect(load, [])

  const toggle = async (u: User) => {
    try {
      if (u.is_active) await api.deactivateUser(u.id)
      else             await api.activateUser(u.id)
      load()
    } catch (e: any) { setError(e.message) }
  }

  const del = async (u: User) => {
    try {
      await api.deleteUser(u.id)
      setConfirm(null)
      load()
    } catch (e: any) { setError(e.message) }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <p className="text-xs text-gray-500">Local accounts only — Okta SSO users are managed in Okta</p>
        <div className="flex items-center gap-2 ml-auto">
          <input
            value={userFilter}
            onChange={e => setUserFilter(e.target.value)}
            placeholder="Filter users…"
            className="bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-gray-600 w-40 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          {userFilter && <button onClick={() => setUserFilter('')} className="text-xs text-white hover:text-white">✕</button>}
          <button onClick={() => setModal('create')}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors">
            <span className="text-base leading-none">+</span> Add User
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-700/50 text-red-400 text-sm rounded-lg px-4 py-2 flex items-center justify-between">
          {error}
          <button onClick={() => setError('')} className="ml-4 text-red-600 hover:text-red-400">✕</button>
        </div>
      )}

      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-32 text-white text-sm">Loading…</div>
        ) : (
          (() => {
            const USER_COLS: Array<{ label: string; key: keyof User | null; cls?: string }> = [
              { label: 'User',       key: 'username' },
              { label: 'Email',      key: 'email' },
              { label: 'Role',       key: 'role' },
              { label: 'Status',     key: 'is_active' },
              { label: 'Last Login', key: 'last_login' },
              { label: '',           key: null, cls: 'px-5 py-3' },
            ]
            const displayedUsers = users
              .filter(u => {
                if (!userFilter) return true
                const q = userFilter.toLowerCase()
                return u.username.toLowerCase().includes(q) ||
                  u.email.toLowerCase().includes(q) ||
                  u.role.toLowerCase().includes(q)
              })
              .sort((a, b) => {
                if (!userSortKey) return 0
                const av = a[userSortKey] as any
                const bv = b[userSortKey] as any
                if (typeof av === 'boolean') return userSortDir === 'asc' ? (av ? 1 : 0) - (bv ? 1 : 0) : (bv ? 1 : 0) - (av ? 1 : 0)
                if (typeof av === 'number') return userSortDir === 'asc' ? av - bv : bv - av
                return userSortDir === 'asc'
                  ? String(av ?? '').localeCompare(String(bv ?? ''))
                  : String(bv ?? '').localeCompare(String(av ?? ''))
              })
            return (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800">
                {USER_COLS.map(col => (
                  <th
                    key={col.label}
                    onClick={() => col.key && toggleUserSort(col.key)}
                    className={`text-left px-5 py-3 text-xs font-medium uppercase tracking-wider select-none
                      ${col.key ? `cursor-pointer ${userSortKey === col.key ? 'text-blue-400' : 'text-white hover:text-gray-200'}` : (col.cls ?? 'text-white')}`}
                  >
                    {col.label}
                    {userSortKey === col.key && col.key && <span className="ml-1">{userSortDir === 'asc' ? '↑' : '↓'}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {displayedUsers.map(u => (
                <tr key={u.id} className="hover:bg-gray-800/30 transition-colors">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2.5">
                      <div className="w-7 h-7 rounded-full bg-blue-700/50 flex items-center justify-center text-xs font-bold text-blue-300">
                        {u.username[0].toUpperCase()}
                      </div>
                      <div>
                        <p className="text-white font-medium">{u.username}</p>
                        {u.username === me?.username && <p className="text-xs text-white">you</p>}
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-3.5 text-white">{u.email}</td>
                  <td className="px-5 py-3.5">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${roleBadge(u.role)}`}>
                      {u.role}
                    </span>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${badge(u.is_active)}`}>
                      {u.is_active ? 'Active' : 'Disabled'}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-white text-xs">
                    {u.last_login ? new Date(u.last_login).toLocaleString() : 'Never'}
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => setResetPw(u)} title="Reset Password"
                        className="p-1.5 text-white hover:text-purple-400 hover:bg-purple-900/20 rounded transition-colors">
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z"/>
                        </svg>
                      </button>
                      <button onClick={() => setModal(u)} title="Edit"
                        className="p-1.5 text-white hover:text-blue-400 hover:bg-blue-900/20 rounded transition-colors">
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125"/>
                        </svg>
                      </button>
                      {u.username !== me?.username && (
                        <button onClick={() => toggle(u)} title={u.is_active ? 'Disable' : 'Enable'}
                          className={`p-1.5 rounded transition-colors ${
                            u.is_active
                              ? 'text-white hover:text-yellow-400 hover:bg-yellow-900/20'
                              : 'text-white hover:text-green-400 hover:bg-green-900/20'
                          }`}>
                          {u.is_active
                            ? <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/></svg>
                            : <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                          }
                        </button>
                      )}
                      {u.username !== me?.username && (
                        <button onClick={() => setConfirm(u)} title="Delete"
                          className="p-1.5 text-white hover:text-red-400 hover:bg-red-900/20 rounded transition-colors">
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0"/>
                          </svg>
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
            )
          })()
        )}
      </div>

      <p className="text-xs text-white">
        Roles: <strong className="text-white">admin</strong> — full access &nbsp;·&nbsp;
        <strong className="text-white">analyst</strong> — read + export &nbsp;·&nbsp;
        <strong className="text-white">viewer</strong> — read-only
      </p>

      {resetPw && <ResetPasswordModal user={resetPw} onClose={() => setResetPw(null)} />}

      {modal !== null && (
        <UserModal
          user={modal === 'create' ? null : modal}
          onClose={() => setModal(null)}
          onSaved={() => { setModal(null); load() }}
        />
      )}

      {confirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 max-w-sm w-full">
            <h3 className="text-white font-semibold mb-2">Delete user?</h3>
            <p className="text-white text-sm mb-5">
              <strong className="text-white">{confirm.username}</strong> will be permanently removed.
              This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setConfirm(null)}
                className="px-4 py-2 text-sm text-white hover:text-white">Cancel</button>
              <button onClick={() => del(confirm)}
                className="px-4 py-2 text-sm bg-red-600 hover:bg-red-500 text-white rounded-lg">Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
