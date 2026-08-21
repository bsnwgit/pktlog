import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './store/auth'
import Layout from './components/Layout'
import Login from './pages/Login'

import { lazy, Suspense } from 'react'
const Dashboard      = lazy(() => import('./pages/Dashboard'))
const SyslogExplorer = lazy(() => import('./pages/SyslogExplorer'))
const Alerts         = lazy(() => import('./pages/Alerts'))
const Approval       = lazy(() => import('./pages/Approval'))
const Settings       = lazy(() => import('./pages/Settings'))
const Logs           = lazy(() => import('./pages/Logs'))
const Documentation  = lazy(() => import('./pages/Documentation'))

function PageFallback() {
  return <div className="flex items-center justify-center h-48 text-white">Loading…</div>
}

// Embedded via pkthub's remote-settings iframe (?chromeless=1) — hide the
// sidebar/header, just render the page content.
const isChromeless = new URLSearchParams(window.location.search).get('chromeless') === '1'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth()
  if (isLoading) return <PageFallback />
  if (!user) return <Navigate to="/login" replace />
  return <Layout chromeless={isChromeless}>{children}</Layout>
}

// Hiding the nav entry isn't access control — a non-admin can still type the
// URL. The API is admin-gated too; this just avoids rendering a page whose
// every call would 403.
function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth()
  if (isLoading) return <PageFallback />
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'admin') return <Navigate to="/" replace />
  return <Layout chromeless={isChromeless}>{children}</Layout>
}


// Detect pktHub proxy basename so React Router routes work when served
// via /proxy/{appId}/. In direct mode pathname starts with / so basename stays '/'.
function getBasename(): string {
  const m = window.location.pathname.match(/^(\/proxy\/[^/]+)/)
  return m ? m[1] : '/'
}
export default function App() {
  return (
    <BrowserRouter basename={getBasename()}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={
            <ProtectedRoute>
              <Suspense fallback={<PageFallback />}><Dashboard /></Suspense>
            </ProtectedRoute>
          } />
          <Route path="/explorer" element={
            <ProtectedRoute>
              <Suspense fallback={<PageFallback />}><SyslogExplorer /></Suspense>
            </ProtectedRoute>
          } />
          <Route path="/alerts" element={
            <ProtectedRoute>
              <Suspense fallback={<PageFallback />}><Alerts /></Suspense>
            </ProtectedRoute>
          } />
          <Route path="/approval" element={
            <AdminRoute>
              <Suspense fallback={<PageFallback />}><Approval /></Suspense>
            </AdminRoute>
          } />
          <Route path="/settings" element={
            <ProtectedRoute>
              <Suspense fallback={<PageFallback />}><Settings /></Suspense>
            </ProtectedRoute>
          } />
          <Route path="/logs" element={
            <ProtectedRoute>
              <Suspense fallback={<PageFallback />}><Logs /></Suspense>
            </ProtectedRoute>
          } />
          <Route path="/documentation" element={
            <ProtectedRoute>
              <Suspense fallback={<PageFallback />}><Documentation /></Suspense>
            </ProtectedRoute>
          } />
          <Route path="/users" element={<Navigate to="/settings" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}