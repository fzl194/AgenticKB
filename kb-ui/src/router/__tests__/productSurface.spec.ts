import { describe, expect, it } from 'vitest'
import router from '@/router'

describe('formal product routes', () => {
  it('does not expose research entity, ontology, or graph pages', () => {
    const names = new Set(router.getRoutes().map(route => String(route.name ?? '')))
    for (const retired of [
      'graph', 'entities', 'ontology', 'ontology-graph',
      'ontology-review', 'mentions-review',
    ]) {
      expect(names.has(retired)).toBe(false)
    }
  })
})
