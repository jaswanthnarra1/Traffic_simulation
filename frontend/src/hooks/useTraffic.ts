import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { api } from '../services/api'
import type { SegmentRow } from '../types'
import { useSim } from './useSim'

export function useTraffic() {
  const { t } = useSim()
  const q = useQuery({ queryKey: ['traffic', t], queryFn: () => api.traffic(t), enabled: Boolean(t) })
  const byId = useMemo(() => new Map<string, SegmentRow>((q.data?.segments ?? []).map(s => [s.segment_id, s])), [q.data])
  return { ...q, byId }
}

/** The segment a page should focus on: the URL selection, else the strongest current anomaly. */
export function useFocusSegment(): string | null {
  const { seg } = useSim()
  const { data } = useTraffic()
  if (seg) return seg
  if (!data) return null
  const top = [...data.segments].sort((a, b) => b.anomaly_score - a.anomaly_score)[0]
  return top && top.anomaly_score > 0 ? top.segment_id : null
}
