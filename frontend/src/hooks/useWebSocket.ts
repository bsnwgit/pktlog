/**
 * useWebSocket — persistent, auto-reconnecting WebSocket hook.
 * Connects to /api/ws/dashboard with the current JWT as ?token=.
 * Passes typed messages to the onMessage callback.
 *
 * Reconnects with exponential backoff: 1s → 2s → 4s → … → 30s max.
 */
import { useEffect, useRef, useCallback, useState } from 'react'
import { getToken } from '../api/client'
import type { DeviceSummary, FlowRecord } from '../api/client'

// ── Message types ──────────────────────────────────────────────────────────────

export interface IngestStats {
  buffered: number
  total_received: number
  total_flushed: number
  last_flush: string
}

export interface AlertFiredPayload {
  event_id: number
  rule_name: string
  severity: string
  message: string
  details: Record<string, unknown>
  fired_at: string
}

export type WsMessage =
  | { type: 'device_update'; data: DeviceSummary[] }
  | { type: 'ingest_stats';  data: IngestStats }
  | { type: 'flow_update';   data: FlowRecord[]; total: number }
  | { type: 'alert_fired';   data: AlertFiredPayload }
  | { type: 'ping' }

// ── Hook ───────────────────────────────────────────────────────────────────────

export function useWebSocket(
  onMessage: (msg: WsMessage) => void,
  enabled = true,
): { connected: boolean } {
  const [connected, setConnected] = useState(false)
  const wsRef        = useRef<WebSocket | null>(null)
  const retryRef     = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryCount   = useRef(0)
  const onMsgRef     = useRef(onMessage)
  onMsgRef.current   = onMessage  // always up to date without re-triggering effect

  const connect = useCallback(() => {
    if (!enabled) return
    const token = getToken()
    if (!token) return

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url   = `${proto}://${window.location.host}/api/ws/dashboard?token=${encodeURIComponent(token)}`
    const ws    = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      retryCount.current = 0
      setConnected(true)
    }

    ws.onmessage = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(ev.data as string) as WsMessage
        if (msg.type === 'ping') {
          // Reply to server keepalive — prevents load-balancer idle disconnects
          if (ws.readyState === WebSocket.OPEN) ws.send('ping')
          return
        }
        onMsgRef.current(msg)
      } catch { /* ignore parse errors */ }
    }

    ws.onclose = () => {
      wsRef.current = null
      setConnected(false)
      if (!enabled) return
      // Exponential backoff capped at 30s
      const delay = Math.min(1000 * 2 ** retryCount.current, 30_000)
      retryCount.current++
      retryRef.current = setTimeout(connect, delay)
    }

    ws.onerror = () => ws.close()
  }, [enabled])

  useEffect(() => {
    if (!enabled) return
    connect()
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current)
      wsRef.current?.close()
    }
  }, [connect, enabled])

  return { connected }
}
