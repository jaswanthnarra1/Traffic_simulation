import { useMemo } from 'react'
import { useNetwork } from '../components/TrafficMap'
import { buildAdjacency, type Adjacency } from '../utils/network'

export function useAdjacency(): Adjacency | null {
  const net = useNetwork()
  return useMemo(() => (net.data ? buildAdjacency(net.data.geojson.features) : null), [net.data])
}
