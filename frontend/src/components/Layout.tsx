import { ReactNode, useState, useEffect } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../store/auth'
import { api } from '../api/client'
import { AutoRefreshProvider, useAutoRefresh } from '../store/autoRefresh'
import clsx from 'clsx'
import { BrandLockup } from './Brand'
import ResonanceMount from '../resonance/ResonanceMount'
import { getToken } from '../api/client'

// ─── Change Password Modal ────────────────────────────────────────────────────
function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (newPw.length < 6) { setError('New password must be at least 6 characters'); return }
    if (newPw !== confirmPw) { setError('Passwords do not match'); return }
    setSaving(true)
    setError('')
    try {
      await api.changeMyPassword(currentPw, newPw)
      setSuccess(true)
      setTimeout(onClose, 1200)
    } catch (e: any) {
      setError(e.message ?? 'Failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-sm p-6" onClick={e => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-white mb-5">Change Password</h2>
        {success ? (
          <p className="text-green-400 text-sm text-center py-4">Password updated successfully!</p>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="text-xs text-white block mb-1">Current Password</label>
              <input type="password" value={currentPw} onChange={e => setCurrentPw(e.target.value)} required autoFocus
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="text-xs text-white block mb-1">New Password</label>
              <input type="password" value={newPw} onChange={e => setNewPw(e.target.value)} required
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
            </div>
            <div>
              <label className="text-xs text-white block mb-1">Confirm New Password</label>
              <input type="password" value={confirmPw} onChange={e => setConfirmPw(e.target.value)} required
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" />
            </div>
            {error && <p className="text-red-400 text-xs">{error}</p>}
            <div className="flex justify-end gap-3 pt-2">
              <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-white hover:text-white transition-colors">Cancel</button>
              <button type="submit" disabled={saving}
                className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors disabled:opacity-50">
                {saving ? 'Saving…' : 'Update Password'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

const NAV = [
  { to: '/',         label: 'Dashboard', icon: '◑', adminOnly: false },
  { to: '/explorer', label: 'Explorer',  icon: '⊞', adminOnly: false },
  { to: '/alerts',   label: 'Alerts',    icon: '△', adminOnly: false, dividerBefore: true },
  { to: '/logs',     label: 'Logs',      icon: '≡', adminOnly: false },
  { to: '/approval', label: 'Approval',  icon: '✓', adminOnly: true,  dividerBefore: true },
  { to: '/settings', label: 'Settings',  icon: '⚙', adminOnly: false, dividerBefore: true },
]

const INTERVALS = [
  { label: '15s', value: 15 },
  { label: '30s', value: 30 },
  { label: '1m',  value: 60 },
  { label: '5m',  value: 300 },
]

function AutoRefreshControl() {
  const { enabled, intervalSec, setEnabled, setIntervalSec } = useAutoRefresh()
  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => setEnabled(!enabled)}
        title={enabled ? 'Disable auto-refresh' : 'Enable auto-refresh'}
        className={clsx(
          'flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg border transition-colors',
          enabled
            ? 'bg-blue-600/20 border-blue-500/50 text-blue-300'
            : 'bg-gray-800 border-gray-700 text-white hover:text-white hover:border-gray-500',
        )}
      >
        <svg className={clsx('w-3 h-3', enabled && 'animate-spin')} style={enabled ? {animationDuration:'3s'} : {}} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
        </svg>
        {enabled ? 'Auto' : 'Refresh'}
      </button>
      {enabled && (
        <select
          value={intervalSec}
          onChange={e => setIntervalSec(Number(e.target.value))}
          className="text-xs bg-gray-800 border border-gray-700 text-white rounded-lg px-2 py-1 focus:outline-none focus:border-blue-500"
        >
          {INTERVALS.map(i => (
            <option key={i.value} value={i.value}>{i.label}</option>
          ))}
        </select>
      )}
    </div>
  )
}

export default function Layout({ children, chromeless = false }: { children: ReactNode; chromeless?: boolean }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const visibleNav = NAV.filter(n => !n.adminOnly || user?.role === 'admin')
  const [unacked, setUnacked] = useState<number>(0)
  const [showChangePw, setShowChangePw] = useState(false)
  const [managedMode, setManagedMode] = useState(false)

  // Skipped entirely when chromeless (embedded, badge/banner never shown)
  // via an early return *inside* each effect callback, not by conditionally
  // calling the hooks themselves — keeps hook call order stable every render.
  useEffect(() => {
    if (chromeless) return
    const tick = async () => {
      try {
        const events = await api.getAlertEvents(true)
        setUnacked(events.filter(e => !e.resolved_at).length)
      } catch {}
    }
    tick()
    const id = setInterval(tick, 10_000)
    return () => clearInterval(id)
  }, [])

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  useEffect(() => {
    if (chromeless) return
    fetch('/api/suite/mode', { credentials: 'include' })
      .then(r => r.json())
      .then(d => setManagedMode(Boolean(d.direct_ui_locked)))
      .catch(() => {})
  }, [])

  // Chromeless: embedded via pkthub's remote-settings iframe — no sidebar,
  // no header, just the page content. Still wrapped in AutoRefreshProvider
  // since Settings.tsx (and others) call useAutoRefresh().
  if (chromeless) {
    return (
      <AutoRefreshProvider>
        <div className="relative z-10 text-white min-h-screen p-6">
          {children}
        </div>
      </AutoRefreshProvider>
    )
  }

  return (
    <AutoRefreshProvider>
    <div className="relative z-10 flex h-screen text-white overflow-hidden">
      <aside className="w-[210px] flex-shrink-0 border-r border-gray-800 flex flex-col" style={{ background: 'linear-gradient(180deg, rgba(216,180,110,.025), transparent 40%)' }}>
        <div className="flex items-center px-4 py-4 border-b border-gray-800">
          <BrandLockup markSize={30} />
        </div>

        <nav className="flex-1 px-2 py-4 space-y-0.5">
          {visibleNav.map(({ to, label, icon, dividerBefore }, i) => (
            <div key={to}>
              {/* dividerBefore marks the start of a group, so consecutive ones
                  collapse to a single rule. Approval and Settings both claim it
                  because Approval is admin-only — without that, hiding Approval
                  would take the divider above Settings away with it. */}
              {dividerBefore && !visibleNav[i - 1]?.dividerBefore && (
                <div className="h-px bg-blue-500/25 mx-3 my-3" />
              )}
              <NavLink
                to={to}
                end={to === '/'}
                className={({ isActive }) => clsx(
                  'flex items-center gap-3 pl-3 pr-3 py-2.5 text-[11.5px] uppercase tracking-[0.13em] transition-colors',
                  isActive
                    ? 'bg-gradient-to-r from-blue-500/[0.12] to-transparent text-blue-300 border-l-2 border-blue-500'
                    : 'text-gray-400 hover:text-white hover:bg-blue-500/[0.04] border-l-2 border-transparent',
                )}
              >
                <span className="text-xs w-3.5 text-center leading-none">{icon}</span>
                <span>{label}</span>
                {label === 'Alerts' && unacked > 0 && (
                  <span className="ml-auto font-mono text-[9.5px] text-red-500 border border-red-500/50 px-1.5 leading-relaxed">
                    {unacked}
                  </span>
                )}
              </NavLink>
            </div>
          ))}
        </nav>

        <div className="px-2 pt-2">
          <NavLink
            to="/documentation"
            className={({ isActive }) => clsx(
              'flex items-center gap-3 pl-3 pr-3 py-2.5 text-[11.5px] uppercase tracking-[0.13em] transition-colors',
              isActive
                ? 'bg-gradient-to-r from-blue-500/[0.12] to-transparent text-blue-300 border-l-2 border-blue-500'
                : 'text-gray-400 hover:text-white hover:bg-blue-500/[0.04] border-l-2 border-transparent',
            )}
          >
            <span className="text-xs w-3.5 text-center leading-none">❐</span>
            <span>Documentation</span>
          </NavLink>
        </div>

        <div className="px-3 py-3 border-t border-gray-800">
          <div className="flex items-center gap-2 px-2 py-1.5">
            <div className="w-7 h-7 rounded-full border border-blue-500/50 flex items-center justify-center text-[11px] font-mono text-blue-300">
              {user?.username?.[0]?.toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-white truncate">{user?.username}</p>
              <p className="text-xs text-white capitalize">{user?.role}</p>
            </div>
            {user?.authProvider === 'local' && (
              <button onClick={() => setShowChangePw(true)} title="Change password" className="text-white hover:text-white">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z"/>
                </svg>
              </button>
            )}
            <button onClick={handleLogout} title="Sign out" className="text-white hover:text-white">
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>
        </div>
      </aside>

      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="h-12 flex-shrink-0 border-b border-gray-800 flex items-center px-6 gap-5">
          <div className="flex items-center gap-1.5 text-sm">
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse"></span>
            <span className="text-white">Live</span>
          </div>
          <div className="ml-auto">
            <AutoRefreshControl />
          </div>
        </header>

        <main className="flex-1 overflow-auto p-5">
          {managedMode && (
        <div className="flex-shrink-0 bg-orange-950/30 border-b border-orange-800/30 px-5 py-1.5 flex items-center gap-2">
          <span style={{fontSize:'13px'}}>&#128274;</span>
          <span className="text-xs text-orange-300 font-semibold">Managed Mode</span>
          <span className="text-xs text-orange-400/70">&#8212; Direct URL access is restricted. Traffic routes through pktHub.</span>
        </div>
      )}
      {children}
        </main>
      </div>
      {showChangePw && <ChangePasswordModal onClose={() => setShowChangePw(false)} />}
      {/* Same slot the removed AI Assistant occupied: one mount for the whole
          authenticated app, so route changes do not cost a new session. */}
      <ResonanceMount getToken={getToken} />
    </div>
    </AutoRefreshProvider>
  )
}