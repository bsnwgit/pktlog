import { Fragment, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, Collector, CollectorIn, User, UserIn, SslStatus, UserApiKey, Integration, IntegrationInput } from '../api/client'
import { useAutoRefresh } from '../store/autoRefresh'
import { useAuth } from '../store/auth'
import { useTimezone } from '../hooks/useTimezone'
import HelpButton from '../components/HelpButton'
import { copyToClipboard } from '../utils/clipboard'
import IpLink from '../components/IpLink'

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

// ── Port field — lives in config.yaml, not the SQLite-backed settings; value
// is lifted to the parent so it saves through the General tab's one Save button ──
function PortField({ value, onChange, loaded }: { value: number; onChange: (v: number) => void; loaded: boolean }) {
  return (
    <Field label="Port" hint="Port the app listens on. Requires a service restart — the browser will need to follow the app to the new port/URL afterward.">
      {!loaded ? (
        <p className="text-xs text-white">Loading…</p>
      ) : (
        <NumberInput value={value} onChange={onChange} min={1} max={65535} />
      )}
    </Field>
  )
}

// ── Section wrapper with Save ─────────────────────────────────────────────────
function Section({
  title, help, children, onSave, saving, saved, error,
}: {
  title: string
  help?: { title: string; content: React.ReactNode }
  children: React.ReactNode
  onSave: () => Promise<void>
  saving: boolean
  saved: boolean
  error: string
}) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-800 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
        {help && <HelpButton title={help.title}>{help.content}</HelpButton>}
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
type TabId = 'general' | 'security' | 'data' | 'notifications' | 'apikeys' | 'devices' | 'ingest'

const TABS: Array<{ id: TabId; label: string; adminOnly?: boolean; gapBefore?: boolean }> = [
  { id: 'general',       label: 'General' },
  { id: 'security',      label: 'Security' },
  { id: 'data',          label: 'Data' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'apikeys',       label: 'User Keys' },
  { id: 'devices',       label: 'Collectors', gapBefore: true },
  { id: 'ingest',        label: 'Ingest' },
]

// ── Security tab — its own left-hand vertical tab strip ──────────────────────
type SecurityTabId = 'users' | 'auth' | 'suite' | 'ai' | 'ssl'
const SECURITY_TABS: Array<{ id: SecurityTabId; label: string; adminOnly?: boolean }> = [
  { id: 'users', label: 'Users', adminOnly: true },
  { id: 'auth',  label: 'Auth' },
  { id: 'suite', label: 'Suite Integration' },
  { id: 'ai',    label: 'AI Assistant' },
  { id: 'ssl',   label: 'SSL / TLS' },
]

// ── Data tab — its own left-hand vertical tab strip ───────────────────────────
type DataTabId = 'storage' | 'backups'
const DATA_TABS: Array<{ id: DataTabId; label: string }> = [
  { id: 'storage', label: 'Storage' },
  { id: 'backups', label: 'Backups' },
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


// ── Sibling pkt apps (outbound) ─────────────────────────────────────────────────
// Named connections to sibling pkt* apps pktlog pulls data from — currently
// just pktIPAM, for the internal-IP lookup in IpLink.tsx. Ported from
// pktflow's own "sibling pkt apps" pattern.
const APP_LABELS: Record<string, string> = {
  pktipam: 'pktIPAM',
}

interface IntegrationFormState {
  name: string; app_name: string; base_url: string; suite_token: string
}

const EMPTY_INTEGRATION: IntegrationFormState = { name: '', app_name: 'pktipam', base_url: '', suite_token: '' }

function IntegrationFormModal({ integration, onClose, onSaved }: {
  integration: Integration | null; onClose: () => void; onSaved: () => void
}) {
  const editing = !!integration
  const [form, setForm] = useState<IntegrationFormState>(
    editing ? { name: integration!.name, app_name: integration!.app_name, base_url: integration!.base_url, suite_token: '' }
            : { ...EMPTY_INTEGRATION }
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const setF = <K extends keyof IntegrationFormState>(k: K, v: IntegrationFormState[K]) => setForm(f => ({ ...f, [k]: v }))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      if (editing) {
        const body: Partial<IntegrationInput> = { name: form.name, base_url: form.base_url }
        if (form.suite_token) body.suite_token = form.suite_token
        await api.updateIntegration(integration!.id, body)
      } else {
        await api.createIntegration(form)
      }
      onSaved()
    } catch (e: any) {
      setError(e.message ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const inp = 'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500'

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-sm p-6" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-white mb-5">{editing ? `Edit — ${integration!.name}` : 'Add pktIPAM Connection'}</h2>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="text-xs text-white block mb-1">Name *</label>
            <input value={form.name} onChange={e => setF('name', e.target.value)} required autoFocus
              placeholder="e.g. Main pktIPAM" className={inp} />
          </div>
          <div>
            <label className="text-xs text-white block mb-1">Base URL *</label>
            <input value={form.base_url} onChange={e => setF('base_url', e.target.value)} required
              placeholder="http://aiserver:8761" className={inp} />
          </div>
          <div>
            <label className="text-xs text-white block mb-1">Suite Token {editing ? '(leave blank to keep)' : '*'}</label>
            <input type="password" value={form.suite_token} onChange={e => setF('suite_token', e.target.value)}
              required={!editing} placeholder="From that pktIPAM's Settings -> Integrations -> Suite Integration" className={inp} />
          </div>
          {error && <p className="text-red-400 text-xs">{error}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-white">Cancel</button>
            <button type="submit" disabled={saving} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg disabled:opacity-50">
              {saving ? 'Saving…' : (editing ? 'Save Changes' : 'Add Connection')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function SiblingIntegrations() {
  const [items, setItems] = useState<Integration[]>([])
  const [loading, setLoading] = useState(true)
  const [modal, setModal] = useState<'new' | Integration | null>(null)
  const [confirm, setConfirm] = useState<Integration | null>(null)
  const [testResult, setTestResult] = useState<Record<number, string>>({})
  const [error, setError] = useState('')

  const load = () => {
    setLoading(true)
    api.getIntegrations().then(setItems).catch(e => setError(e.message)).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const del = async (i: Integration) => {
    try {
      await api.deleteIntegration(i.id)
      setConfirm(null)
      load()
    } catch (e: any) {
      setError(e.message)
    }
  }

  const test = async (i: Integration) => {
    try {
      const result = await api.testIntegration(i.id)
      setTestResult(prev => ({ ...prev, [i.id]: result.healthy ? `OK — ${result.detail}` : `Failed — ${result.detail}` }))
    } catch (e: any) {
      setTestResult(prev => ({ ...prev, [i.id]: `Failed — ${e.message}` }))
    }
    load()
  }

  if (loading) return <p className="text-xs text-white animate-pulse py-3">Loading…</p>

  return (
    <div className="space-y-3 py-3">
      {error && (
        <div className="bg-red-900/30 border border-red-700/50 text-red-400 text-sm rounded-lg px-4 py-2 flex items-center justify-between">
          {error}<button onClick={() => setError('')} className="ml-4 text-red-600 hover:text-red-400">✕</button>
        </div>
      )}

      {items.map(i => (
        <div key={i.id} className="bg-gray-800/40 border border-gray-800 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <div>
              <p className="text-sm font-medium text-white">{i.name}</p>
              <p className="text-xs text-white">{APP_LABELS[i.app_name] ?? i.app_name} · {i.base_url || 'no URL set'}</p>
            </div>
            <span className={i.health_status === 'ok' ? 'text-xs text-emerald-400' : 'text-xs text-white'}>{i.health_status}</span>
          </div>
          <div className="flex items-center gap-3 mt-2">
            <button onClick={() => test(i)} className="text-xs text-white border border-gray-700 rounded-lg px-3 py-1.5 hover:bg-gray-800">Test Connection</button>
            <button onClick={() => setModal(i)} className="text-xs text-white hover:text-blue-400">Edit</button>
            <button onClick={() => setConfirm(i)} className="text-xs text-white hover:text-red-400">Delete</button>
            {testResult[i.id] && <span className="text-xs text-white">{testResult[i.id]}</span>}
          </div>
        </div>
      ))}
      {items.length === 0 && <p className="text-sm text-white py-2">No pktIPAM connections yet.</p>}

      <button onClick={() => setModal('new')}
        className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors">
        <span className="text-base leading-none">+</span> Add Connection
      </button>

      {modal !== null && (
        <IntegrationFormModal integration={modal === 'new' ? null : modal} onClose={() => setModal(null)}
          onSaved={() => { setModal(null); load() }} />
      )}

      {confirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setConfirm(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 max-w-sm w-full" onClick={e => e.stopPropagation()}>
            <h3 className="text-white font-semibold mb-2">Delete connection?</h3>
            <p className="text-white text-sm mb-5">
              Remove <strong className="text-white">{confirm.name}</strong>? Internal-IP lookups will fall back to
              any other enabled pktIPAM connection, or stop working if this was the only one.
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setConfirm(null)} className="px-4 py-2 text-sm text-white">Cancel</button>
              <button onClick={() => del(confirm)} className="px-4 py-2 text-sm bg-red-600 hover:bg-red-500 text-white rounded-lg">Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
// ── End Sibling pkt apps ──────────────────────────────────────────────────────

// ── Suite Integration component ───────────────────────────────────────────────
function PktHubTokenDisplay() {
  const [token, setToken]           = useState('')
  const [revealed, setRevealed]     = useState(false)
  const [copied, setCopied]         = useState(false)
  const [loaded, setLoaded]         = useState(false)
  const [regenerating, setRegen]    = useState(false)
  const [redirectUrl, setRedirectUrl] = useState('')
  const [redirectUrlSaved, setRedirectUrlSaved] = useState(false)
  const [savingUrl, setSavingUrl] = useState(false)

  const regenerate = async () => {
    if (!confirm('Generate a new token?\n\nThe current token will stop working immediately.\nYou will need to re-register this app in pktHub with the new token.')) return
    setRegen(true)
    try {
      const r = await fetch('/api/suite/token/regenerate', { method: 'POST', credentials: 'include' })
      const d = await r.json()
      if (d.suite_token) { setToken(d.suite_token); setRevealed(true) }
    } catch {}
    setRegen(false)
  }

  const saveRedirectUrl = async () => {
    setSavingUrl(true)
    try {
      await fetch('/api/suite/hub-redirect-url', {
        method: 'PATCH', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hub_redirect_url: redirectUrl }),
      })
      setRedirectUrlSaved(true)
      setTimeout(() => setRedirectUrlSaved(false), 3000)
    } catch {}
    setSavingUrl(false)
  }

  useEffect(() => {
    fetch('/api/suite/token', { credentials: 'include' })
      .then(r => r.json())
      .then(d => { setToken(d.suite_token || ''); setLoaded(true) })
      .catch(() => setLoaded(true))
    fetch('/api/suite/mode', { credentials: 'include' })
      .then(r => r.json())
      .then(d => { if (d.hub_redirect_url) setRedirectUrl(d.hub_redirect_url) })
      .catch(() => {})
  }, [])

  const masked = token
    ? token.slice(0, 6) + '\u2022'.repeat(28) + token.slice(-4)
    : ''

  return (
    <>
      <div className="grid grid-cols-3 gap-4 items-start py-3 border-b border-gray-800">
        <div>
          <p className="text-sm font-medium text-white">Suite Token</p>
          <p className="text-xs text-gray-500 mt-0.5">Copy to pktHub when registering this app</p>
        </div>
        <div className="col-span-2">
          {!loaded && <p className="text-xs text-gray-500 animate-pulse">Loading…</p>}
          {loaded && !token && (
            <p className="text-xs text-yellow-400">No token set — visit this page again after restarting the service.</p>
          )}
          {loaded && token && (
            <div className="flex items-center gap-2 flex-wrap">
              <code className="flex-1 min-w-0 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs font-mono text-gray-200 break-all">
                {revealed ? token : masked}
              </code>
              <button
                onClick={() => setRevealed(v => !v)}
                className="px-2 py-1.5 text-xs text-gray-400 hover:text-white border border-gray-700 rounded-lg bg-gray-800 whitespace-nowrap"
              >
                {revealed ? 'Hide' : 'Reveal'}
              </button>
              <button
                onClick={async () => {
                  const ok = await copyToClipboard(token)
                  if (ok) { setCopied(true); setTimeout(() => setCopied(false), 2000) }
                }}
                className="px-3 py-1.5 text-xs font-medium text-white rounded-lg whitespace-nowrap transition-colors"
                style={{ background: copied ? '#16a34a' : '#2563eb' }}
              >
                {copied ? '\u2713 Copied' : 'Copy Token'}
              </button>
              <button
                onClick={regenerate}
                disabled={regenerating}
                title="Generate a new token — you must re-register in pktHub after"
                className="px-2 py-1.5 text-xs font-medium text-red-400 hover:text-red-300 border border-red-800/60 hover:border-red-600 rounded-lg whitespace-nowrap disabled:opacity-40 transition-colors"
              >
                {regenerating ? '\u2026' : 'Regen'}
              </button>
            </div>
          )}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-4 items-start py-3 border-b border-gray-800">
        <div>
          <p className="text-sm font-medium text-white">Hub Redirect URL</p>
          <p className="text-xs text-gray-500 mt-0.5">Where users are sent when direct access is blocked in Managed Mode</p>
        </div>
        <div className="col-span-2">
          <div className="flex items-center gap-2">
            <input
              type="url"
              value={redirectUrl}
              onChange={e => setRedirectUrl(e.target.value)}
              placeholder="https://192.0.2.10:8765"
              className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm font-mono text-gray-200 focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={saveRedirectUrl}
              disabled={savingUrl}
              className="px-3 py-2 text-xs font-medium text-white rounded-lg whitespace-nowrap disabled:opacity-40 transition-colors"
              style={{ background: redirectUrlSaved ? '#16a34a' : '#2563eb' }}
            >
              {savingUrl ? '\u2026' : redirectUrlSaved ? '\u2713 Saved' : 'Save'}
            </button>
          </div>
          <p className="text-xs text-gray-600 mt-1.5">Configure before enabling Managed Mode in pktHub → App Registry.</p>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-4 items-start py-3">
        <div>
          <p className="text-sm font-medium text-white">How to register</p>
        </div>
        <div className="col-span-2 space-y-1 text-xs text-gray-400">
          <p>1. Copy the token above.</p>
          <p>2. In pktHub &#8594; App Manager &#8594; Register App, enter this app&#39;s URL and paste the token.</p>
          <p>3. pktHub will open this app through its proxy with users automatically signed in.</p>
          <p className="text-gray-500 mt-2 text-xs">&#9888; The token is permanent — it does <em>not</em> change on restart. Use <strong className="text-gray-400">Regenerate</strong> to revoke current access and issue a new token (re-register in pktHub afterwards).</p>
        </div>
      </div>
    </>
  )
}
// ── End Suite Integration ─────────────────────────────────────────────────────


export default function Settings() {
  const { user: me }          = useAuth()
  const isAdmin               = me?.role === 'admin'
  const [searchParams]        = useSearchParams()
  const [tab, setTab]         = useState<TabId>((searchParams.get('tab') as TabId) || 'general')
  const [securityTab, setSecurityTab] = useState<SecurityTabId>(isAdmin ? 'users' : 'auth')
  const [dataTab, setDataTab] = useState<DataTabId>('storage')
  // Deep-link from an "Unknown collector" alert event — pre-fills the
  // add-collector form on the Collectors tab; the user still decides the
  // rest of the fields and clicks Save themselves.
  const registerIp            = searchParams.get('register_ip') || ''
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
  // General tab's Port field lives in config.yaml (not the SQLite settings
  // blob) so it needs its own fetch, but saves through the same one button.
  const [portValue, setPortValue]   = useState(0)
  const [portLoaded, setPortLoaded] = useState(false)
  useEffect(() => {
    api.getPort().then(r => setPortValue(r.port)).catch(() => {}).finally(() => setPortLoaded(true))
  }, [])

  const [generalSaving, setGeneralSaving] = useState(false)
  const [generalSaved, setGeneralSaved]   = useState(false)
  const [generalError, setGeneralError]   = useState('')

  const saveGeneral = async () => {
    if (portValue < 1 || portValue > 65535) { setGeneralError('Enter a port between 1 and 65535'); return }
    setGeneralSaving(true); setGeneralSaved(false); setGeneralError('')
    try {
      const subset: Settings = {}
      for (const k of ['app_name', 'base_url', 'timezone']) if (k in settings) subset[k] = settings[k]
      await api.bulkUpdateSettings(subset)
      await api.setPort(portValue)
      await load()
      setGeneralSaved(true)
      setTimeout(() => setGeneralSaved(false), 3000)
    } catch (e: any) {
      setGeneralError(e.message || 'Save failed')
    } finally {
      setGeneralSaving(false)
    }
  }

  const aiAssistantSave = useSave(['anthropic_api_key', 'ai_model'], settings, load)
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
    'syslog_port', 'retention_days_raw', 'journal_max_gb',
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
  const lucidSave = useSave(['lucid_api_token'], settings, load)

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
      <div className="flex items-center gap-1 bg-gray-900 border border-gray-800 rounded-xl p-1 w-fit overflow-x-auto">
        {TABS.filter(t => !t.adminOnly || isAdmin).map(t => (
          <Fragment key={t.id}>
            {t.gapBefore && <div className="w-px self-stretch bg-gray-700 mx-2" />}
            <button
              onClick={() => setTab(t.id)}
              className={`text-sm px-4 py-1.5 rounded-lg whitespace-nowrap transition-colors ${
                tab === t.id ? 'bg-gray-700 text-white' : 'text-white hover:text-white'
              }`}
            >
              {t.label}
            </button>
          </Fragment>
        ))}
      </div>

      {/* General */}
      {tab === 'general' && (
        <Section title="General" onSave={saveGeneral} saving={generalSaving} saved={generalSaved} error={generalError}
          help={{
            title: 'General — How It Works',
            content: <>
              <p><span className="text-gray-300 font-medium">Base URL</span> feeds the SAML ACS/metadata URLs on the Auth tab and any links posted in Slack/Email/webhook notifications — set it to the actual externally-reachable address before configuring SSO or notifications, or those will point at the wrong place.</p>
              <p><span className="text-gray-300 font-medium">Port</span> only takes effect after a restart. Changing it moves the app to a new URL; the browser won't follow automatically.</p>
            </>,
          }}
        >
          <Field label="App name" hint="Displayed in browser tab and header">
            <TextInput value={str('app_name', 'pktLog')} onChange={v => set('app_name', v)} />
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
          <PortField value={portValue} onChange={setPortValue} loaded={portLoaded} />
          <Field label="Base URL" hint="Used for redirect URIs and notification links">
            <TextInput value={str('base_url')} onChange={v => set('base_url', v)} placeholder="http://192.0.2.10:8768" />
          </Field>
          <RestartServiceRow />
        </Section>
      )}

      {/* Security */}
      {tab === 'security' && (
        <div className="flex gap-4 items-start">
          <div className="flex flex-col gap-1.5 w-48 flex-shrink-0">
            {SECURITY_TABS.filter(st => !st.adminOnly || isAdmin).map(st => (
              <button
                key={st.id}
                onClick={() => setSecurityTab(st.id)}
                className={`text-sm px-4 py-2 rounded-lg border text-left whitespace-nowrap transition-colors ${
                  securityTab === st.id
                    ? 'bg-gray-800 border-blue-500 text-white'
                    : 'bg-gray-900 border-gray-800 text-white hover:border-gray-600'
                }`}
              >
                {st.label}
              </button>
            ))}
          </div>

          <div className="flex-1 min-w-0">
            {securityTab === 'users' && isAdmin && <UsersTab />}

            {securityTab === 'auth' && (
              <Section title="Authentication" onSave={authSave.save} saving={authSave.saving} saved={authSave.saved} error={authSave.error}
                help={{
                  title: 'Authentication — How It Works',
                  content: <>
                    <p><span className="text-gray-300 font-medium">Local auth</span> and <span className="text-gray-300 font-medium">SAML SSO</span> aren't mutually exclusive — both can be on at once. Turning Local auth off forces everyone through SSO.</p>
                    <p>SAML users are <span className="text-gray-300 font-medium">auto-provisioned</span> on first successful login — no separate "create user" step.</p>
                    <p>Setting this up: paste Okta's IdP metadata XML to auto-fill the IdP fields, then register the <span className="text-gray-300 font-medium">ACS URL</span> shown here as the Single Sign-On URL in your Okta app. Both the ACS URL and SP metadata link derive from <span className="text-gray-300 font-medium">Base URL</span> on the General tab — set that correctly first.</p>
                  </>,
                }}
              >
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
                        <a href={`${str('base_url')}/api/auth/saml/metadata`} target="_blank" rel="noreferrer"
                          className="text-xs text-blue-400 hover:text-blue-300 whitespace-nowrap">
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

            {securityTab === 'suite' && (
              <div className="bg-gray-900 border border-gray-800 rounded-xl px-6 py-4">
                <div className="flex items-center gap-2 mb-4">
                  <h2 className="text-sm font-semibold text-white">Suite Integration</h2>
                  <HelpButton title="Suite Integration — How It Works">
                    <p>One-directional discovery: copy the Suite Token here into pktHub's App Manager when registering this app, so pktHub can proxy into it with users already signed in. Regenerating the token immediately revokes the old one.</p>
                  </HelpButton>
                </div>
                <PktHubTokenDisplay />
              </div>
            )}

            {securityTab === 'suite' && (
              <div className="bg-gray-900 border border-gray-800 rounded-xl px-6 py-4">
                <div className="flex items-center gap-2 mb-4">
                  <h2 className="text-sm font-semibold text-white">Sibling pkt Apps</h2>
                  <HelpButton title="Sibling pkt Apps — How It Works">
                    <p>The other direction from the Suite Token above: pktlog calling into pktIPAM to look up internal (private) IP addresses — subnet, hostname, DHCP lease, DNS records — the same way it already looks up external IPs via ipinfo.io/AbuseIPDB.</p>
                    <p className="mt-2">In pktIPAM, go to Settings &#8594; Integrations &#8594; Suite Integration and copy its Suite Token, then add a connection here with pktIPAM's base URL and that token. You can add more than one named pktIPAM connection; the first enabled one is used for lookups.</p>
                  </HelpButton>
                </div>
                <SiblingIntegrations />
              </div>
            )}

            {securityTab === 'ai' && (
              <Section title="AI Assistant" onSave={aiAssistantSave.save} saving={aiAssistantSave.saving} saved={aiAssistantSave.saved} error={aiAssistantSave.error}
                help={{
                  title: 'AI Assistant — How It Works',
                  content: <>
                    <p><span className="text-gray-300 font-medium">AI Assistant</span> needs its own Anthropic API key (console.anthropic.com, separate from a Claude Enterprise seat) before the in-app chat panel does anything. Haiku is the default: fastest/cheapest for syslog-context questions.</p>
                  </>,
                }}
              >
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
                      { value: 'claude-sonnet-5', label: 'Claude Sonnet (balanced)' },
                      { value: 'claude-opus-4-8', label: 'Claude Opus (most capable)' },
                    ]}
                  />
                </Field>
              </Section>
            )}

            {securityTab === 'ssl' && (
              <div className="bg-gray-900 border border-gray-800 rounded-xl px-6 py-4">
                <div className="flex items-center gap-2 mb-4">
                  <h2 className="text-sm font-semibold text-white">SSL / TLS</h2>
                  <HelpButton title="SSL/TLS — How It Works">
                    <p>Accepts either a combined PFX/P12 file or a separate PEM cert+key pair — the running service auto-detects and loads whichever was uploaded at startup.</p>
                  </HelpButton>
                </div>
                <SslPanel sslEnabled={bool('ssl_enabled')} onToggleSSL={v => { set('ssl_enabled', v); api.bulkUpdateSettings({ ssl_enabled: v }).catch(() => {}) }} />
              </div>
            )}
          </div>
        </div>
      )}

      {/* Data */}
      {tab === 'data' && (
        <div className="flex gap-4 items-start">
          <div className="flex flex-col gap-1.5 w-48 flex-shrink-0">
            {DATA_TABS.map(dt => (
              <button
                key={dt.id}
                onClick={() => setDataTab(dt.id)}
                className={`text-sm px-4 py-2 rounded-lg border text-left whitespace-nowrap transition-colors ${
                  dataTab === dt.id
                    ? 'bg-gray-800 border-blue-500 text-white'
                    : 'bg-gray-900 border-gray-800 text-white hover:border-gray-600'
                }`}
              >
                {dt.label}
              </button>
            ))}
          </div>

          <div className="flex-1 min-w-0">
      {dataTab === 'storage' && (
        <Section title="Storage" onSave={storageSave.save} saving={storageSave.saving} saved={storageSave.saved} error={storageSave.error}
          help={{
            title: 'Storage — How It Works',
            content: <>
              <p>Switching <span className="text-gray-300 font-medium">Backend</span> requires a service restart to actually take effect — the running process picks its storage driver once at startup, so saving this field alone won't move any data.</p>
              <p><span className="text-gray-300 font-medium">ClickHouse is the actual default</span> for this app — DuckDB is the embedded alternative for smaller/simpler deployments with no external service to run.</p>
              <p>Retention days apply per-tier — raw syslog records are usually kept far shorter than hourly rollups. <span className="text-gray-300 font-medium">Manual cleanup</span> applies current thresholds immediately instead of waiting for the next scheduled pass; on ClickHouse the actual deletion is a queued TTL mutation, so it may not be instant even after this returns.</p>
            </>,
          }}
        >
          <Field label="Backend" hint="ClickHouse is the default; DuckDB is embedded and needs no separate service. A service restart is required after changing this setting.">
            <SelectInput
              value={str('storage_backend', 'clickhouse')}
              onChange={v => set('storage_backend', v)}
              options={[
                { value: 'clickhouse', label: 'ClickHouse (default)' },
                { value: 'duckdb', label: 'DuckDB (embedded, no external service)' },
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
      {dataTab === 'backups' && (
        <Section title="Backup" onSave={backupSave.save} saving={backupSave.saving} saved={backupSave.saved} error={backupSave.error}
          help={{
            title: 'Backup — How It Works',
            content: <>
              <p>A backup always includes the SQLite database (settings, collectors, users, alert rules) and <code className="text-gray-400">config.yaml</code>. <span className="text-gray-300 font-medium">Include ClickHouse flows</span> additionally exports full syslog history — worth disabling if you only care about configuration, since log history is usually the largest part by far.</p>
              <p><span className="text-gray-300 font-medium">Rotation count</span> caps how many snapshots (scheduled or manual) stay on disk — the oldest is deleted automatically once you exceed it.</p>
              <p><span className="text-gray-300 font-medium">Export bundle</span> is a one-off download, separate from the rotation-managed snapshots above. <span className="text-amber-500 font-medium">Restore always requires a service restart</span> afterward for config changes in the bundle to apply.</p>
            </>,
          }}
        >
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
            <TextInput value={str('backup_path')} onChange={v => set('backup_path', v)} mono />
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
          </div>
        </div>
      )}

      {/* Ingest */}
      {tab === 'ingest' && (
        <Section title="Ingest" onSave={ingestSave.save} saving={ingestSave.saving} saved={ingestSave.saved} error={ingestSave.error}
          help={{
            title: 'Ingest — How It Works',
            content: <>
              <p>The syslog listener accepts <span className="text-gray-300 font-medium">both UDP and TCP</span> on the same configured port — most network devices default to UDP, but some (and any that need delivery guarantees) use TCP.</p>
              <p><span className="text-amber-500 font-medium">Changing the port requires a service restart</span> to take effect — the listener binds once at process startup.</p>
              <p>Regardless of ingest volume, a record is only stored if its sender's IP is <span className="text-gray-300 font-medium">present and enabled in the collector registry</span> (Collectors tab) — this settings page controls the listener itself, not what's allowed through.</p>
              <p>The <span className="text-gray-300 font-medium">journal cap</span> is a safety net, not primary storage — it only fills up when ClickHouse is unreachable and events can't be written directly; once the cap is hit, the oldest journaled files are dropped rather than growing disk usage unbounded.</p>
            </>,
          }}
        >
          <Field label="Syslog port" hint="UDP + TCP port pktLog listens on for incoming syslog messages. Changing requires a service restart.">
            <div className="flex items-center gap-3">
              <NumberInput value={num('syslog_port', 8761)} onChange={v => set('syslog_port', v)} min={1} max={65535} />
              <span className="text-xs text-gray-500">UDP + TCP</span>
            </div>
          </Field>
          <Field label="Raw log retention" hint="How long to keep syslog events in ClickHouse before TTL expiry.">
            <div className="flex items-center gap-3">
              <NumberInput value={num('retention_days_raw', 90)} onChange={v => set('retention_days_raw', v)} min={1} max={3650} />
              <span className="text-sm text-white">days</span>
            </div>
          </Field>
          <Field label="Journal cap" hint="Maximum disk space for the ingest file journal (used when ClickHouse is unreachable). Oldest files are dropped when the cap is hit.">
            <div className="flex items-center gap-3">
              <NumberInput value={num('journal_max_gb', 5)} onChange={v => set('journal_max_gb', v)} min={1} max={500} />
              <span className="text-sm text-white">GB</span>
            </div>
          </Field>
        </Section>
      )}

      {/* Notifications */}
      {tab === 'notifications' && (
        <Section title="Notifications" onSave={notifySave.save} saving={notifySave.saving} saved={notifySave.saved} error={notifySave.error}
          help={{
            title: 'Notifications — How It Works',
            content: <>
              <p>These five channels — Slack, Email, PagerDuty, generic Webhook, and TraceCat SOAR — are what an <span className="text-gray-300 font-medium">Alert rule</span> (Alerts page) actually dispatches to when it fires. Enabling a channel here doesn't send anything by itself; it makes the channel available to alert rules.</p>
              <p><span className="text-gray-300 font-medium">Send Test</span> is a real dispatch, not a dry run — it posts to Slack, sends actual SMTP, fires a PagerDuty event, etc., using whatever's currently filled in above even if unsaved.</p>
              <p><span className="text-gray-300 font-medium">Webhook payload template</span> is Jinja2 — reference <code className="text-gray-400">alert_name</code>, <code className="text-gray-400">message</code>, <code className="text-gray-400">severity</code>, and <code className="text-gray-400">fired_at</code>.</p>
            </>,
          }}
        >
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

      {/* Collectors tab */}
      {tab === 'devices' && <CollectorRegistryTab prefillIp={registerIp} />}

      {/* User Keys tab — personal keys plus the app-wide Lucidchart token */}
      {tab === 'apikeys' && (
        <ApiKeysTab
          lucidToken={str('lucid_api_token')}
          onLucidChange={v => set('lucid_api_token', v)}
          lucidSave={lucidSave}
        />
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

function SslPanel({ sslEnabled, onToggleSSL }: { sslEnabled: boolean; onToggleSSL: (v: boolean) => void }) {
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
      {/* Enable HTTPS toggle — always visible */}
      <div className="flex items-center justify-between bg-gray-800 border border-gray-700 rounded-xl px-4 py-3">
        <div>
          <p className="text-sm font-medium text-white">Enable HTTPS</p>
          <p className="text-xs text-gray-400">Requires a certificate · restart service to apply</p>
        </div>
        <button
          onClick={() => onToggleSSL(!sslEnabled)}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${sslEnabled ? 'bg-blue-600' : 'bg-gray-600'}`}
        >
          <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${sslEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
        </button>
      </div>

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

// ── Collector Registry tab ────────────────────────────────────────────────────

function CollectorRegistryTab({ prefillIp = '' }: { prefillIp?: string }) {
  const [collectors, setCollectors] = useState<Collector[]>([])
  const [editing, setEditing]       = useState<Collector | null>(null)
  const [adding, setAdding]         = useState(false)
  const [saving, setSaving]         = useState(false)
  const [error, setError]           = useState('')
  const [exporting, setExporting]   = useState(false)
  const [importResult, setImportResult] = useState<{ created: number; skipped: number; errors: string[] } | null>(null)
  const importFileRef = useRef<HTMLInputElement>(null)

  const EMPTY: Partial<CollectorIn> = {
    collector_ip: '', collector_name: '', org: '', log_group: '', site: '', notes: '',
  }
  const [addForm, setAddForm] = useState<Partial<CollectorIn>>(EMPTY)

  const load = async () => {
    try { setCollectors(await api.getCollectors()) } catch {}
  }

  useEffect(() => { load() }, [])

  // Deep-linked from an "Unknown collector" alert event — open the add
  // form with just the IP filled in; the user decides everything else.
  useEffect(() => {
    if (prefillIp) {
      setEditing(null)
      setAddForm({ ...EMPTY, collector_ip: prefillIp })
      setAdding(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillIp])

  const saveEdit = async (c: Collector) => {
    setSaving(true); setError('')
    try {
      await api.updateCollector(c.collector_ip, {
        collector_name: c.collector_name, org: c.org,
        log_group: c.log_group, site: c.site, notes: c.notes,
        enabled: !!c.enabled,
      })
      setEditing(null)
      await load()
    } catch (e: any) { setError(e.message || 'Save failed') }
    finally { setSaving(false) }
  }

  const saveAdd = async () => {
    if (!addForm.collector_ip) { setError('IP is required'); return }
    setSaving(true); setError('')
    try {
      await api.createCollector(addForm as CollectorIn)
      setAdding(false)
      setAddForm(EMPTY)
      await load()
    } catch (e: any) { setError(e.message || 'Save failed') }
    finally { setSaving(false) }
  }

  const del = async (ip: string) => {
    if (!confirm(`Remove collector ${ip}?`)) return
    try { await api.deleteCollector(ip); await load() } catch {}
  }

  const handleDownloadTemplate = () => {
    const rows = [
      ['collector_ip', 'collector_name', 'org', 'log_group', 'site', 'notes', 'enabled'],
      ['10.0.1.5', 'collector-branch-a', 'YourOrg', 'Branch-A', 'Site1', '', 'true'],
      ['10.0.2.5', 'collector-branch-b', 'YourOrg', 'Branch-B', 'Site1', '', 'true'],
    ]
    const csv = rows.map(r => r.map(v => `"${v}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'pktlog-collectors-template.csv'; a.click()
    URL.revokeObjectURL(url)
  }

  const handleExport = async () => {
    setExporting(true)
    try { await api.exportCollectors() }
    catch (e: any) { setError(e.message) }
    finally { setExporting(false) }
  }

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    try {
      const result = await api.importCollectors(file)
      setImportResult(result)
      if (result.created > 0) await load()
    } catch (e: any) { setError(e.message) }
  }

  const InputCls = 'w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500'

  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <p className="text-sm font-semibold text-white">Collector Registry</p>
        <HelpButton title="Collector Registry — How It Works">
          <p>This is the <span className="text-gray-300 font-medium">ingest allowlist</span>, not just a labeling table — syslog from a sender IP that's absent here, or present but not marked Enabled, is dropped before storage entirely. It never reaches ClickHouse, and won't show up unlabeled either; it simply doesn't exist as far as the rest of the app is concerned.</p>
          <p>An unregistered sender instead fires an "Unknown collector" alert with a one-click registration link, so nothing silently vanishes without at least a notification.</p>
          <p>CSV import/export and the downloadable template are for bulk provisioning — useful when onboarding many syslog sources at once instead of one by one.</p>
        </HelpButton>
      </div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs text-gray-500">
          Gateway for what's allowed to persist — a device can be sending syslog data on the wire,
          but nothing is stored unless its IP is listed here and marked Enabled. Also maps collector
          IPs to names and the org/group/site hierarchy used for log enrichment.
        </p>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button onClick={handleExport} disabled={exporting}
            className="px-3 py-2 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors disabled:opacity-50">
            {exporting ? 'Exporting…' : '↓ Export CSV'}
          </button>
          <div className="flex items-center gap-1">
            <button onClick={() => importFileRef.current?.click()}
              className="px-3 py-2 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors rounded-r-none border-r border-gray-600">
              ↑ Import CSV
            </button>
            <button onClick={handleDownloadTemplate} title="Download CSV template"
              className="px-2 py-2 text-xs bg-gray-700 hover:bg-gray-600 text-gray-400 hover:text-white rounded-lg transition-colors rounded-l-none"
              aria-label="Download template">
              template
            </button>
          </div>
          <input ref={importFileRef} type="file" accept=".csv" className="hidden" onChange={handleImportFile} />
          {!adding && (
            <button onClick={() => { setAdding(true); setEditing(null); setError('') }}
              className="bg-blue-600 hover:bg-blue-500 text-white text-xs rounded-lg px-4 py-2 transition-colors">
              + Add collector
            </button>
          )}
        </div>
      </div>

      {error && <p className="mb-3 text-xs text-red-400">{error}</p>}

      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-500">
              <th className="px-4 py-3 text-left font-medium">IP</th>
              <th className="px-4 py-3 text-left font-medium">Name</th>
              <th className="px-4 py-3 text-left font-medium">Org</th>
              <th className="px-4 py-3 text-left font-medium">Log Group</th>
              <th className="px-4 py-3 text-left font-medium">Site</th>
              <th className="px-4 py-3 text-left font-medium">Enabled</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {/* Add row */}
            {adding && (
              <tr className="bg-gray-800/40">
                <td className="px-3 py-2">
                  <input value={addForm.collector_ip ?? ''} onChange={e => setAddForm(f => ({ ...f, collector_ip: e.target.value }))}
                    placeholder="10.0.0.1" className={InputCls + ' font-mono'} />
                </td>
                <td className="px-3 py-2">
                  <input value={addForm.collector_name ?? ''} onChange={e => setAddForm(f => ({ ...f, collector_name: e.target.value }))}
                    placeholder="collector name" className={InputCls} />
                </td>
                <td className="px-3 py-2">
                  <input value={addForm.org ?? ''} onChange={e => setAddForm(f => ({ ...f, org: e.target.value }))}
                    placeholder="org name" className={InputCls} />
                </td>
                <td className="px-3 py-2">
                  <input value={addForm.log_group ?? ''} onChange={e => setAddForm(f => ({ ...f, log_group: e.target.value }))}
                    placeholder="Collector-A" className={InputCls} />
                </td>
                <td className="px-3 py-2">
                  <input value={addForm.site ?? ''} onChange={e => setAddForm(f => ({ ...f, site: e.target.value }))}
                    placeholder="optional" className={InputCls} />
                </td>
                <td className="px-3 py-2">
                  <Toggle value={addForm.enabled ?? true} onChange={v => setAddForm(f => ({ ...f, enabled: v }))} />
                </td>
                <td className="px-3 py-2">
                  <div className="flex gap-2">
                    <button onClick={saveAdd} disabled={saving}
                      className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded px-3 py-1.5 transition-colors">
                      {saving ? 'Saving…' : 'Add'}
                    </button>
                    <button onClick={() => { setAdding(false); setAddForm(EMPTY); setError('') }}
                      className="text-xs border border-gray-700 text-gray-400 hover:text-white rounded px-3 py-1.5 transition-colors">
                      Cancel
                    </button>
                  </div>
                </td>
              </tr>
            )}

            {collectors.length === 0 && !adding && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-600">
                  No collectors registered
                </td>
              </tr>
            )}

            {collectors.map(c => editing?.collector_ip === c.collector_ip ? (
              <tr key={c.collector_ip} className="bg-gray-800/40">
                <td className="px-3 py-2">
                  <p className="text-xs font-mono text-gray-400 px-2">{c.collector_ip}</p>
                </td>
                <td className="px-3 py-2">
                  <input value={editing.collector_name} onChange={e => setEditing({ ...editing, collector_name: e.target.value })}
                    className={InputCls} />
                </td>
                <td className="px-3 py-2">
                  <input value={editing.org} onChange={e => setEditing({ ...editing, org: e.target.value })}
                    className={InputCls} />
                </td>
                <td className="px-3 py-2">
                  <input value={editing.log_group} onChange={e => setEditing({ ...editing, log_group: e.target.value })}
                    className={InputCls} />
                </td>
                <td className="px-3 py-2">
                  <input value={editing.site} onChange={e => setEditing({ ...editing, site: e.target.value })}
                    className={InputCls} />
                </td>
                <td className="px-3 py-2">
                  <Toggle value={!!editing.enabled} onChange={v => setEditing({ ...editing, enabled: v ? 1 : 0 })} />
                </td>
                <td className="px-3 py-2">
                  <div className="flex gap-2">
                    <button onClick={() => saveEdit(editing)} disabled={saving}
                      className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded px-3 py-1.5 transition-colors">
                      {saving ? 'Saving…' : 'Save'}
                    </button>
                    <button onClick={() => { setEditing(null); setError('') }}
                      className="text-xs border border-gray-700 text-gray-400 hover:text-white rounded px-3 py-1.5 transition-colors">
                      Cancel
                    </button>
                  </div>
                </td>
              </tr>
            ) : (
              <tr key={c.collector_ip} className="hover:bg-gray-800/30 transition-colors">
                <td className="px-4 py-3 font-mono text-xs text-blue-300"><IpLink ip={c.collector_ip} className="text-blue-300" /></td>
                <td className="px-4 py-3 text-sm text-white">{c.collector_name || '—'}</td>
                <td className="px-4 py-3 text-sm text-gray-300">{c.org || '—'}</td>
                <td className="px-4 py-3 text-sm text-gray-300">{c.log_group || '—'}</td>
                <td className="px-4 py-3 text-sm text-gray-300">{c.site || '—'}</td>
                <td className="px-4 py-3">
                  <span className={`text-xs px-2 py-0.5 rounded ${c.enabled ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                    {c.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-3">
                    <button onClick={() => { setEditing({ ...c }); setAdding(false); setError('') }}
                      className="text-xs text-gray-400 hover:text-blue-400 transition-colors">Edit</button>
                    <button onClick={() => del(c.collector_ip)}
                      className="text-xs text-gray-400 hover:text-red-400 transition-colors">Remove</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {importResult && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 py-8 px-4" onClick={() => setImportResult(null)}>
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 max-w-lg w-full" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-white mb-3">Import complete</h3>
            <div className="space-y-1 mb-4">
              <p className="text-sm text-green-400">✓ {importResult.created} collector{importResult.created !== 1 ? 's' : ''} created</p>
              {importResult.skipped > 0 && (
                <p className="text-sm text-yellow-400">⚠ {importResult.skipped} row{importResult.skipped !== 1 ? 's' : ''} skipped</p>
              )}
            </div>
            {importResult.errors.length > 0 && (
              <div className="bg-gray-800 rounded-lg px-3 py-2 max-h-36 overflow-y-auto mb-4">
                {importResult.errors.map((e, i) => (
                  <p key={i} className="text-xs text-red-400 font-mono">{e}</p>
                ))}
              </div>
            )}
            <div className="bg-gray-800/60 rounded-lg px-3 py-2 mb-4">
              <p className="text-xs font-medium text-gray-400 mb-1">CSV columns (header row required)</p>
              <p className="text-xs font-mono text-gray-500 break-all">collector_ip, collector_name, org, log_group, site, notes, enabled</p>
            </div>
            <div className="flex items-center justify-between">
              <button onClick={handleDownloadTemplate} className="text-xs text-blue-400 hover:text-blue-300 transition-colors">
                ↓ Download template
              </button>
              <button onClick={() => setImportResult(null)} className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg">
                Done
              </button>
            </div>
          </div>
        </div>
      )}
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

// Providers whose response the user can filter down to specific sections in
// the IP Lookup modal. Keyed by provider id; each entry's field keys match
// what the backend's IPINFO_FIELDS / IPAPI_IS_FIELDS constants accept.
const FIELD_SETS: Record<string, { key: string; label: string }[]> = {
  ipinfo: [
    { key: 'geolocation', label: 'Geolocation' },
    { key: 'asn',         label: 'ASN / Org' },
    { key: 'company',     label: 'Company' },
    { key: 'privacy',     label: 'Privacy Detection (VPN/Proxy/Tor)' },
    { key: 'abuse',       label: 'Abuse Contact' },
    { key: 'domains',     label: 'Hosted Domains' },
  ],
  ipapi_is: [
    { key: 'geolocation', label: 'Geolocation' },
    { key: 'asn',         label: 'ASN / Org' },
    { key: 'company',     label: 'Company' },
    { key: 'detection',   label: 'Threat Detection (VPN/Proxy/Tor/Datacenter)' },
    { key: 'abuse',       label: 'Abuse Contact' },
  ],
  mxtoolbox: [
    { key: 'ptr',       label: 'Reverse DNS (PTR)' },
    { key: 'asn',       label: 'ASN' },
    { key: 'blacklist', label: 'Blacklist Check' },
  ],
}
const setFieldsApi: Record<string, (fields: string[]) => Promise<UserApiKey>> = {
  ipinfo: api.setIpinfoFields,
  ipapi_is: api.setIpapiIsFields,
  mxtoolbox: api.setMxtoolboxFields,
}
// The 4 providers with a section in the IP Lookup modal — AbuseIPDB has no
// per-field checkboxes (single score, not multiple sections) but still gets
// the modal-section on/off toggle. IPQualityScore isn't wired into the modal
// at all, so it gets neither.
const MODAL_PROVIDERS = ['ipinfo', 'ipapi_is', 'abuseipdb', 'mxtoolbox']

function ApiKeysTab({ lucidToken, onLucidChange, lucidSave }: {
  lucidToken: string
  onLucidChange: (v: string) => void
  lucidSave: { saving: boolean; saved: boolean; error: string; save: () => Promise<void> }
}) {
  const { user } = useAuth()
  const [keys, setKeys]       = useState<UserApiKey[]>([])
  const [loading, setLoading] = useState(true)
  const [drafts, setDrafts]   = useState<Record<string, string>>({})
  const [saving, setSaving]   = useState<Record<string, boolean>>({})
  const [saved, setSaved]     = useState<Record<string, boolean>>({})
  const [error, setError]     = useState<Record<string, string>>({})
  const [testing, setTesting] = useState<Record<string, boolean>>({})
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; detail: string }>>({})
  const [fieldsError, setFieldsError] = useState('')

  async function handleToggleField(provider: string, fieldKey: string, checked: boolean) {
    const providerKey = keys.find(k => k.provider === provider)
    const current = providerKey?.enabled_fields ?? FIELD_SETS[provider].map(f => f.key)
    const next = checked ? [...current, fieldKey] : current.filter(f => f !== fieldKey)
    setFieldsError('')
    try {
      const updated = await setFieldsApi[provider](next)
      setKeys(prev => prev.map(k => k.provider === provider ? updated : k))
    } catch (err: any) {
      setFieldsError(err.message ?? 'Failed to save')
    }
  }

  async function handleToggleFreeTier(checked: boolean) {
    setFieldsError('')
    try {
      const updated = await api.setIpapiIsFreeTier(checked)
      setKeys(prev => prev.map(k => k.provider === 'ipapi_is' ? updated : k))
    } catch (err: any) {
      setFieldsError(err.message ?? 'Failed to save')
    }
  }

  async function handleToggleEnabled(provider: string, checked: boolean) {
    setFieldsError('')
    try {
      const updated = await api.setProviderEnabled(provider, checked)
      setKeys(prev => prev.map(k => k.provider === provider ? updated : k))
    } catch (err: any) {
      setFieldsError(err.message ?? 'Failed to save')
    }
  }

  function load() {
    setLoading(true)
    api.getUserApiKeys()
      .then(rows => { setKeys(rows); setDrafts(Object.fromEntries(rows.map(r => [r.provider, r.api_key]))) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  async function handleSave(provider: string) {
    setSaving(s => ({ ...s, [provider]: true }))
    setError(e => ({ ...e, [provider]: '' }))
    try {
      const updated = await api.setUserApiKey(provider, drafts[provider] ?? '')
      setKeys(prev => prev.map(k => k.provider === provider ? updated : k))
      setSaved(s => ({ ...s, [provider]: true }))
      setTimeout(() => setSaved(s => ({ ...s, [provider]: false })), 2000)
    } catch (err: any) {
      setError(e => ({ ...e, [provider]: err.message ?? 'Save failed' }))
    } finally {
      setSaving(s => ({ ...s, [provider]: false }))
    }
  }

  async function handleTest(provider: string) {
    setTesting(t => ({ ...t, [provider]: true }))
    setTestResult(r => ({ ...r, [provider]: undefined as any }))
    try {
      const res = await api.testUserApiKey(provider, drafts[provider] ?? '')
      setTestResult(r => ({ ...r, [provider]: { ok: res.status === 'ok', detail: res.detail } }))
    } catch (err: any) {
      setTestResult(r => ({ ...r, [provider]: { ok: false, detail: err.message ?? 'Test failed' } }))
    } finally {
      setTesting(t => ({ ...t, [provider]: false }))
    }
  }

  const inp = 'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono'

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <h2 className="text-lg font-semibold text-white">User Keys</h2>
        <HelpButton title="User Keys — How It Works">
          <p>External API keys for lookup tools (IP reputation, geolocation, etc.) are <span className="text-gray-300 font-medium">personal, not shared</span> — each user stores their own key here under their own account, and only that user's own requests use it. Nobody else, including admins, can see the key's value.</p>
          <p>Leave a field blank and save to clear a key.</p>
        </HelpButton>
      </div>
      <p className="text-sm text-white">
        Signed in as <span className="text-white font-medium">{user?.username}</span> — these keys apply to your account only.
      </p>

      {loading ? (
        <p className="text-sm text-white">Loading…</p>
      ) : (
        <div className="space-y-4 max-w-lg">
          {keys.map(k => {
            const isFreeTier = k.provider === 'ipapi_is' && k.free_tier
            return (
            <div key={k.provider}>
              <label className="block text-xs text-white mb-1">{k.label}</label>
              {MODAL_PROVIDERS.includes(k.provider) && (
                <label className="flex items-center gap-2 text-xs text-white cursor-pointer mb-1.5">
                  <input
                    type="checkbox"
                    checked={k.enabled}
                    onChange={e => handleToggleEnabled(k.provider, e.target.checked)}
                    className="accent-blue-600"
                  />
                  Show this provider in the IP Lookup modal
                </label>
              )}
              {k.provider === 'ipapi_is' && (
                <label className="flex items-center gap-2 text-xs text-white cursor-pointer mb-1.5">
                  <input
                    type="checkbox"
                    checked={k.free_tier}
                    onChange={e => handleToggleFreeTier(e.target.checked)}
                    className="accent-blue-600"
                  />
                  Use free tier (no key required, ~1,000 lookups/day)
                </label>
              )}
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={drafts[k.provider] ?? ''}
                  onChange={e => setDrafts(d => ({ ...d, [k.provider]: e.target.value }))}
                  placeholder="Not set"
                  disabled={isFreeTier}
                  className={`${inp} ${isFreeTier ? 'opacity-40 cursor-not-allowed' : ''}`}
                />
                <button
                  onClick={() => handleTest(k.provider)}
                  disabled={isFreeTier || testing[k.provider] || !(drafts[k.provider] ?? '').trim()}
                  className="shrink-0 bg-gray-800 hover:bg-gray-700 border border-gray-700 text-white text-sm font-medium rounded-lg px-4 py-2 transition-colors disabled:opacity-50"
                >
                  {testing[k.provider] ? 'Testing…' : 'Test'}
                </button>
                <button
                  onClick={() => handleSave(k.provider)}
                  disabled={isFreeTier || saving[k.provider]}
                  className="shrink-0 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg px-4 py-2 transition-colors"
                >
                  {saving[k.provider] ? 'Saving…' : 'Save'}
                </button>
              </div>
              {saved[k.provider] && <p className="text-xs text-green-400 mt-1">Saved</p>}
              {error[k.provider] && <p className="text-xs text-red-400 mt-1">{error[k.provider]}</p>}
              {testResult[k.provider] && (
                <p className={`text-xs mt-1 ${testResult[k.provider].ok ? 'text-green-400' : 'text-red-400'}`}>
                  {testResult[k.provider].ok ? '✓ ' : '✗ '}{testResult[k.provider].detail}
                </p>
              )}
              {FIELD_SETS[k.provider] && (
                <div className="mt-3 pl-1">
                  <p className="text-xs text-gray-500 mb-1.5">Shown in the IP Lookup modal:</p>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                    {FIELD_SETS[k.provider].map(f => (
                      <label key={f.key} className="flex items-center gap-2 text-xs text-white cursor-pointer">
                        <input
                          type="checkbox"
                          checked={k.enabled_fields ? k.enabled_fields.includes(f.key) : true}
                          onChange={e => handleToggleField(k.provider, f.key, e.target.checked)}
                          className="accent-blue-600"
                        />
                        {f.label}
                      </label>
                    ))}
                  </div>
                  {fieldsError && <p className="text-xs text-red-400 mt-1">{fieldsError}</p>}
                </div>
              )}
            </div>
          )})}
        </div>
      )}

      <div className="pt-2 border-t border-gray-800 max-w-lg">
        <p className="text-xs font-semibold text-white uppercase tracking-wider mt-4 mb-1">Lucidchart</p>
        <label className="block text-xs text-white mb-1">API token</label>
        <div className="flex items-center gap-2">
          <input
            type="password"
            value={lucidToken}
            onChange={e => onLucidChange(e.target.value)}
            placeholder="eyJ…"
            className={inp}
          />
          <button
            onClick={lucidSave.save}
            disabled={lucidSave.saving}
            className="shrink-0 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg px-4 py-2 transition-colors"
          >
            {lucidSave.saving ? 'Saving…' : 'Save'}
          </button>
        </div>
        {lucidSave.saved && <p className="text-xs text-green-400 mt-1">Saved</p>}
        {lucidSave.error && <p className="text-xs text-red-400 mt-1">{lucidSave.error}</p>}
        <p className="text-xs text-gray-500 mt-1">Personal Access Token from lucid.co → Account → API Tokens. Required for topology export to Lucidchart.</p>
      </div>
    </div>
  )
}

function UsersTab() {
  const { user: me } = useAuth()
  const timezone = useTimezone()
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

  const makeDefaultAdmin = async (u: User) => {
    try {
      await api.setDefaultAdmin(u.id)
      load()
    } catch (e: any) { setError(e.message) }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <p className="text-sm font-semibold text-white">Users</p>
        <HelpButton title="Users — How It Works">
          <p>Three roles: <span className="text-gray-300 font-medium">admin</span> (full access, including this Users tab and all Settings), <span className="text-gray-300 font-medium">analyst</span> (read access plus export), and <span className="text-gray-300 font-medium">viewer</span> (read-only, no export).</p>
          <p>This tab only manages <span className="text-gray-300 font-medium">local accounts</span> — SAML/Okta SSO users are auto-provisioned on first login and managed in Okta itself, not here.</p>
          <p><span className="text-gray-300 font-medium">Deactivate</span> blocks login immediately without deleting the account or its history — prefer it over Delete for someone leaving temporarily, since Delete is permanent.</p>
          <p>The <span className="text-yellow-400">★</span> marks the <span className="text-gray-300 font-medium">default admin</span> — when every auth method in the Auth tab is disabled, the app skips the login page entirely and signs everyone in as this account. Click the star on any active admin to reassign it.</p>
        </HelpButton>
      </div>
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
                        <div className="flex items-center gap-1.5">
                          <p className="text-white font-medium">{u.username}</p>
                          <button
                            onClick={() => !u.is_default_admin && u.role === 'admin' && u.is_active && makeDefaultAdmin(u)}
                            disabled={u.is_default_admin || u.role !== 'admin' || !u.is_active}
                            title={u.is_default_admin
                              ? 'Default admin — auto-logged-in when all auth methods are disabled'
                              : (u.role === 'admin' && u.is_active ? 'Make default admin' : 'Only active admins can be the default admin')}
                            className={`text-sm leading-none ${u.is_default_admin ? 'text-yellow-400' : 'text-gray-500 hover:text-gray-300 disabled:hover:text-gray-500'}`}
                          >
                            {u.is_default_admin ? '★' : '☆'}
                          </button>
                        </div>
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
                    {u.last_login
                      // last_login is naive UTC (SQLite datetime('now'), no 'Z') —
                      // normalize before parsing so it isn't misread as local time.
                      ? new Date(u.last_login.includes('T') || u.last_login.endsWith('Z') ? u.last_login : u.last_login.replace(' ', 'T') + 'Z')
                          .toLocaleString([], { timeZone: timezone })
                      : 'Never'}
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
