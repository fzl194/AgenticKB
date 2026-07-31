import { describe, expect, it } from 'vitest'
import { load as yamlLoad } from 'js-yaml'
import { normalizeSite, parseUiYaml, buildUiYaml } from '../brandYaml'

describe('normalizeSite', () => {
  it('maps snake_case site keys to BrandConfig (camelCase)', () => {
    expect(
      normalizeSite({ title: 'T', name: 'N', badge: 'B', logo_text: 'L', icon: 'I' }),
    ).toEqual({ title: 'T', name: 'N', badge: 'B', logoText: 'L', icon: 'I' })
  })

  it('ignores non-string and unknown keys', () => {
    expect(normalizeSite({ title: 5, logo_text: null, extra: 'x' })).toEqual({})
  })

  it('handles nullish / non-object input', () => {
    expect(normalizeSite(null)).toEqual({})
    expect(normalizeSite(undefined)).toEqual({})
    expect(normalizeSite('site')).toEqual({})
  })
})

describe('parseUiYaml', () => {
  it('separates site from rest and normalizes', () => {
    const text =
      'site:\n  title: A\n  logo_text: L\nmining_api_base: http://x\nfoo: bar\n'
    const { site, rest } = parseUiYaml(text)
    expect(site).toEqual({ title: 'A', logoText: 'L' })
    expect(rest).toEqual({ mining_api_base: 'http://x', foo: 'bar' })
  })

  it('returns empty parts for blank / null yaml', () => {
    expect(parseUiYaml('')).toEqual({ site: {}, rest: {} })
    expect(parseUiYaml('null')).toEqual({ site: {}, rest: {} })
  })
})

describe('buildUiYaml', () => {
  it('writes snake_case site keys and preserves rest', () => {
    const text = buildUiYaml({
      rest: { mining_api_base: 'http://x' },
      site: { title: 'T', logoText: 'L', icon: 'data:x' },
    })
    const doc = yamlLoad(text) as Record<string, unknown>
    const site = doc.site as Record<string, string>
    expect(site.title).toBe('T')
    expect(site.logo_text).toBe('L')
    expect(site.icon).toBe('data:x')
    expect(doc.mining_api_base).toBe('http://x')
  })

  it('round-trips parse → build without losing rest keys or site values', () => {
    const original =
      'site:\n  title: A\n  name: N\n  badge: B\n  logo_text: L\n  icon: ""\n' +
      'mining_api_base: http://x\nllm_api_base: http://y\n'
    const rebuilt = buildUiYaml(parseUiYaml(original))
    const doc = yamlLoad(rebuilt) as Record<string, unknown>
    const site = doc.site as Record<string, string>
    expect(site).toEqual({ title: 'A', name: 'N', badge: 'B', logo_text: 'L', icon: '' })
    expect(doc.mining_api_base).toBe('http://x')
    expect(doc.llm_api_base).toBe('http://y')
  })
})
