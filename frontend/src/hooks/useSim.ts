import { useQuery } from '@tanstack/react-query'
import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../services/api'
import { clampTime } from '../utils/format'

/** Shared simulation state lives in the URL (?t=…&seg=…) so every page, link and reload agree. */
export function useSim() {
  const [params, setParams] = useSearchParams()
  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta, staleTime: Infinity })
  const t = params.get('t') ?? meta.data?.demo_time ?? ''
  const seg = params.get('seg')

  const update = useCallback((patch: Record<string, string | null>) => {
    setParams(prev => {
      const next = new URLSearchParams(prev)
      Object.entries(patch).forEach(([k, v]) => (v === null ? next.delete(k) : next.set(k, v)))
      return next
    }, { replace: false })
  }, [setParams])

  const setTime = useCallback((nt: string) => {
    const lo = meta.data?.data_range[0] ?? nt
    const hi = meta.data?.data_range[1] ?? nt
    update({ t: clampTime(nt, lo, hi) })
  }, [meta.data, update])

  return {
    t, seg, meta: meta.data, ready: Boolean(t),
    setTime,
    setSeg: (s: string | null) => update({ seg: s }),
    update,
  }
}
