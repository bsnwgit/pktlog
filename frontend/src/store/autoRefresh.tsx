import { createContext, useContext, useState, useEffect, useRef } from 'react'

interface AutoRefreshCtx {
  enabled: boolean
  intervalSec: number
  tick: number
  setEnabled: (v: boolean) => void
  setIntervalSec: (v: number) => void
}

const AutoRefreshContext = createContext<AutoRefreshCtx>({
  enabled: false, intervalSec: 30, tick: 0,
  setEnabled: () => {}, setIntervalSec: () => {},
})

export function AutoRefreshProvider({ children }: { children: React.ReactNode }) {
  const [enabled, setEnabled] = useState(false)
  const [intervalSec, setIntervalSec] = useState(30)
  const [tick, setTick] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (enabled) {
      timerRef.current = setInterval(() => setTick(t => t + 1), intervalSec * 1000)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [enabled, intervalSec])

  return (
    <AutoRefreshContext.Provider value={{ enabled, intervalSec, tick, setEnabled, setIntervalSec }}>
      {children}
    </AutoRefreshContext.Provider>
  )
}

export function useAutoRefresh() {
  return useContext(AutoRefreshContext)
}
