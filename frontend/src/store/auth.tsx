import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { api, setToken, clearToken } from '../api/client'

interface AuthState {
  user: { username: string; role: string; hasPassword: boolean; authProvider: string } | null
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  isLoading: boolean
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthState['user']>(null)
  const [isLoading, setIsLoading] = useState(true)

  // Try silent refresh on mount (restores session after page reload)
  // Also handles the SSO redirect — backend sets short-lived sso_access_token + sso_role cookies
  useEffect(() => {
    const runNormalAuthFlow = () => {
      const getCookie = (name: string) => {
        const match = document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith(name + '='))
        return match ? decodeURIComponent(match.slice(name.length + 1)) : null
      }
      const clearCookie = (name: string) => {
        document.cookie = `${name}=; max-age=0; path=/`
      }

      const ssoToken = getCookie('sso_access_token')
      const ssoRole  = getCookie('sso_role')

      if (ssoToken && ssoRole) {
        // SSO callback — consume the one-time cookies and establish session
        clearCookie('sso_access_token')
        clearCookie('sso_role')
        setToken(ssoToken, ssoRole)
        api.getMe()
          .then(me => { if (me) setUser({ username: me.username, role: me.role, hasPassword: me.has_password, authProvider: me.auth_provider }) })
          .catch(() => {})
          .finally(() => setIsLoading(false))
        return
      }

      fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
        .then(r => r.ok ? r.json() : null)
        .then(async data => {
          if (!data) {
            // No session yet — if every auth method is disabled, log straight
            // in as the default admin instead of showing the login form.
            const config = await fetch('/api/auth/config').then(r => r.ok ? r.json() : null).catch(() => null)
            if (config && !config.local_enabled && !config.saml_enabled) {
              data = await api.autoLogin().catch(() => null)
            }
          }
          if (data) {
            setToken(data.access_token, data.role)
            return api.getMe()
          }
        })
        .then(me => { if (me) setUser({ username: me.username, role: me.role, hasPassword: me.has_password, authProvider: me.auth_provider }) })
        .catch(() => {})
        .finally(() => setIsLoading(false))
    }

    // Accessed through pkthub's proxy (e.g. the remote-settings embed) — the
    // proxy attaches a suite token to every forwarded request server-side, so
    // this app's own login flow never gets a chance to run. Detect that via
    // /api/suite/whoami (which recognizes the suite token) and synthesize a
    // session instead of falling through to the login page. A direct visit
    // with no suite token just gets a 401 here and falls through normally.
    fetch('/api/suite/whoami')
      .then(r => r.ok ? r.json() : null)
      .then(whoami => {
        if (whoami?.authenticated && whoami?.via_suite_token) {
          setUser({ username: 'pktHub', role: whoami.role, hasPassword: false, authProvider: 'suite' })
          setIsLoading(false)
          return true
        }
        return false
      })
      .catch(() => false)
      .then(handled => { if (!handled) runNormalAuthFlow() })
  }, [])

  const login = async (username: string, password: string) => {
    const data = await api.login(username, password)
    setToken(data.access_token, data.role)
    const me = await api.getMe()
    setUser({ username: me.username, role: me.role, hasPassword: me.has_password, authProvider: me.auth_provider })
  }

  const logout = async () => {
    await api.logout().catch(() => {})
    clearToken()
    setUser(null)
  }

  // Proactive session polling — if both tokens expire while user is idle, redirect to login.
  useEffect(() => {
    if (!user) return
    const interval = setInterval(async () => {
      const res = await fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
      if (res.ok) {
        const data = await res.json()
        setToken(data.access_token, data.role)
      } else {
        clearToken()
        window.location.replace('/login')
      }
    }, 60_000)
    return () => clearInterval(interval)
  }, [user])

  return (
    <AuthContext.Provider value={{ user, login, logout, isLoading }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
