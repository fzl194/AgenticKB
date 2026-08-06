import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const state = vi.hoisted(() => ({
  domain: { currentDomain: 'odn', enabledDomains: [{ domain_id: 'odn' }], fetchDomains: vi.fn() },
  router: { push: vi.fn() },
  api: { listParadigms: vi.fn(), getMcpCatalog: vi.fn() },
  ui: { confirm: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/operator', () => ({ useOperatorApi: () => state.api }))
vi.mock('@/stores/domain', () => ({ useDomainStore: () => state.domain }))
vi.mock('vue-router', () => ({ useRouter: () => state.router }))
vi.mock('element-plus', () => ({
  ElMessageBox: { confirm: state.ui.confirm },
  ElMessage: { success: state.ui.success, error: state.ui.error, warning: state.ui.warning },
}))

import ParadigmListView from '../ParadigmListView.vue'

function paradigm(over: Record<string, unknown> = {}) {
  return {
    id: 'pd-1', name: 'ODN 拓扑排障', description: null, status: 'active',
    currentVersion: 3, draftGraph: null, createdAt: null, updatedAt: null,
    boundDomain: 'odn', isDefault: true, boundAt: null,
    ...over,
  }
}

/**
 * The column answers the one question the binding dialog cannot: "I published it — can an agent
 * actually use it?" Everything asserted here is about that answer being both correct and
 * survivable when the catalog is not.
 */
describe('ParadigmListView — Agent visibility column', () => {
  beforeEach(() => {
    state.api.listParadigms.mockReset().mockResolvedValue([paradigm()])
    state.api.getMcpCatalog.mockReset().mockResolvedValue({ paradigms: [], hidden: [] })
    state.ui.error.mockReset()
  })

  async function mountWith(paradigms: unknown[], catalog: unknown) {
    state.api.listParadigms.mockResolvedValue(paradigms)
    if (catalog instanceof Error) state.api.getMcpCatalog.mockRejectedValue(catalog)
    else state.api.getMcpCatalog.mockResolvedValue(catalog)
    const wrapper = shallowMount(ParadigmListView)
    await flushPromises()
    return wrapper
  }

  function display(wrapper: ReturnType<typeof shallowMount>, id = 'pd-1') {
    return (wrapper.vm as unknown as {
      visibilityDisplay: Record<string, { state: string; reason?: string; tagType: string }>
    }).visibilityDisplay[id]
  }

  it('marks a catalogued paradigm visible', async () => {
    const wrapper = await mountWith([paradigm()], {
      paradigms: [{ id: 'pd-1', name: 'ODN 拓扑排障', description: '', domain: 'odn', version: 3, isDomainDefault: true }],
      hidden: [],
    })

    expect(display(wrapper).state).toBe('visible')
  })

  it('marks a hidden paradigm with its reason', async () => {
    const wrapper = await mountWith([paradigm()], {
      paradigms: [],
      hidden: [{ id: 'pd-1', name: 'x', reason: 'kb_not_anonymously_readable', details: ['kb-3f2a'], undisclosedCount: 0 }],
    })

    expect(display(wrapper).state).toBe('hidden')
    expect(display(wrapper).reason).toBe('kb_not_anonymously_readable')
  })

  it('greys out a collect terminus but flags misconfiguration in amber', async () => {
    // A collect paradigm is meant to be unservable — evaluation harness, not a mistake to fix.
    const wrapper = await mountWith(
      [paradigm({ id: 'pd-a' }), paradigm({ id: 'pd-b' })],
      {
        paradigms: [],
        hidden: [
          { id: 'pd-a', name: 'eval', reason: 'not_servable', details: [], undisclosedCount: 0 },
          { id: 'pd-b', name: 'oops', reason: 'unbound_kb_scope', details: [], undisclosedCount: 0 },
        ],
      },
    )

    expect(display(wrapper, 'pd-a').tagType).toBe('info')
    expect(display(wrapper, 'pd-b').tagType).toBe('warning')
  })

  it('shows "unknown" for an unpublished paradigm without calling it hidden', async () => {
    // Draft paradigms are never in the catalog. Reporting that as a problem would train operators
    // to ignore the column.
    const wrapper = await mountWith(
      [paradigm({ status: 'draft', currentVersion: 0 })],
      { paradigms: [], hidden: [] },
    )

    expect(display(wrapper).state).toBe('unknown')
  })

  it('degrades to "unknown" — not an error toast — when the catalog is unreachable', async () => {
    const wrapper = await mountWith([paradigm()], new Error('503'))

    expect(display(wrapper).state).toBe('unknown')
    expect(state.ui.error).not.toHaveBeenCalled()
    // The list itself must still be there: the column is an add-on, not a dependency.
    expect((wrapper.vm as unknown as { paradigms: unknown[] }).paradigms).toHaveLength(1)
  })

  it('appends a count for knowledge bases this user may not be told about', async () => {
    const wrapper = await mountWith([paradigm()], {
      paradigms: [],
      hidden: [{
        id: 'pd-1', name: 'x', reason: 'kb_not_anonymously_readable',
        details: ['kb-visible'], undisclosedCount: 2,
      }],
    })

    const shown = (wrapper.vm as unknown as {
      visibilityDisplay: Record<string, { details?: string[] }>
    }).visibilityDisplay['pd-1'].details
    expect(shown).toEqual(['kb-visible', '另有 2 个不可见的知识库'])
  })
})

/**
 * The binding dialog and this column report the same underlying conditions. Wording them
 * differently would read as two unrelated problems, so both go through one function.
 */
describe('ParadigmListView — reasonText', () => {
  beforeEach(() => {
    state.api.listParadigms.mockReset().mockResolvedValue([])
    state.api.getMcpCatalog.mockReset().mockResolvedValue({ paradigms: [], hidden: [] })
  })

  async function reasonText() {
    const wrapper = shallowMount(ParadigmListView)
    await flushPromises()
    return (wrapper.vm as unknown as {
      reasonText: (code?: string, details?: string[]) => string
    }).reasonText
  }

  it('gives the binding code and the catalog code the same words', async () => {
    const t = await reasonText()

    expect(t('paradigm_not_servable')).toBe(t('not_servable'))
    expect(t('paradigm_requires_identity', ['kb-1'])).toBe(t('kb_not_anonymously_readable', ['kb-1']))
  })

  it('names the offending knowledge bases when it is allowed to', async () => {
    const t = await reasonText()

    expect(t('kb_not_anonymously_readable', ['kb-1', 'kb-2'])).toContain('kb-1、kb-2')
  })

  it('returns empty for an unrecognised code so the caller can fall back', async () => {
    const t = await reasonText()

    expect(t('something_new')).toBe('')
    expect(t(undefined)).toBe('')
  })
})
