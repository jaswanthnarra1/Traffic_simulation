import { useCallback, useEffect, useRef, useState } from 'react'

export type GeoStatus = 'idle' | 'locating' | 'ready' | 'denied' | 'unavailable'
export interface Here { lat: number; lon: number; accuracy: number }

/**
 * One-shot browser geolocation, only on request. It never prompts on page load, never tracks, never stores the
 * position (React state only, gone on reload). If the browser already granted permission earlier, it quietly locates once.
 */
export function useGeolocation() {
  const [status, setStatus] = useState<GeoStatus>('idle')
  const [here, setHere] = useState<Here | null>(null)
  const asked = useRef(false)

  const locate = useCallback((): Promise<Here | null> => new Promise(resolve => {
    if (!('geolocation' in navigator)) { setStatus('unavailable'); resolve(null); return }
    setStatus('locating')
    navigator.geolocation.getCurrentPosition(
      p => {
        const h = { lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy }
        setHere(h); setStatus('ready'); resolve(h)
      },
      err => { setStatus(err.code === 1 ? 'denied' : 'unavailable'); resolve(null) },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 },
    )
  }), [])

  useEffect(() => {
    if (asked.current || !navigator.permissions?.query) return
    asked.current = true
    navigator.permissions.query({ name: 'geolocation' as PermissionName }).then(s => { if (s.state === 'granted') void locate() }).catch(() => undefined)
  }, [locate])

  return { status, here, locate }
}
