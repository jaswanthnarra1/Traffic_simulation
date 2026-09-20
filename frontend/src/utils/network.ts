import type { NetworkResponse } from '../types'

type F = NetworkResponse['geojson']['features'][number]

/** Segment adjacency from the network GeoJSON (no extra request). Mirrors the backend: the reverse twin is not a neighbor. */
export interface Adjacency { up: Map<string, string[]>; down: Map<string, string[]>; twin: Map<string, string> }

export function buildAdjacency(features: F[]): Adjacency {
  const bySource = new Map<string, string[]>(), byTarget = new Map<string, string[]>()
  const ends = new Map<string, [string, string]>()
  const push = (m: Map<string, string[]>, k: string, v: string) => m.set(k, [...(m.get(k) ?? []), v])
  for (const f of features) {
    const { segment_id: id, source_node: s, target_node: t } = f.properties
    ends.set(id, [s, t]); push(bySource, s, id); push(byTarget, t, id)
  }
  const twin = new Map<string, string>()
  for (const [id, [s, t]] of ends) {
    const other = (bySource.get(t) ?? []).find(o => ends.get(o)![1] === s)
    if (other) twin.set(id, other)
  }
  const up = new Map<string, string[]>(), down = new Map<string, string[]>()
  for (const [id, [s, t]] of ends) {
    up.set(id, (byTarget.get(s) ?? []).filter(o => o !== twin.get(id)))
    down.set(id, (bySource.get(t) ?? []).filter(o => o !== twin.get(id)))
  }
  return { up, down, twin }
}

/** The segment plus its immediate upstream/downstream neighbors — the corridor the camera frames. */
export const corridor = (id: string, adj: Adjacency): string[] => [id, ...(adj.up.get(id) ?? []), ...(adj.down.get(id) ?? [])]
