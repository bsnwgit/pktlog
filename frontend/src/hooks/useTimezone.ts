/**
 * Shared "display timezone" setting — configured in Settings → General,
 * applied everywhere a timestamp is rendered.
 */
import { useEffect, useState } from 'react'
import { api } from '../api/client'

export function useTimezone(): string {
  const [timezone, setTimezone] = useState('UTC')

  useEffect(() => {
    api.getSettings()
      .then(s => setTimezone((s['timezone'] as string) || 'UTC'))
      .catch(() => {})
  }, [])

  return timezone
}
