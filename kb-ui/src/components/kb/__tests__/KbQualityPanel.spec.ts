import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
const getKbQuality = vi.hoisted(() => vi.fn())
vi.mock('@/api/kb', () => ({ useKbApi: () => ({ getKbQuality }) }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => '读取失败' }))
import KbQualityPanel from '../KbQualityPanel.vue'
const report = (count: number) => ({
  structure: { sections_total: count, sections_with_ordinal: count, sections_bridged: count, units_total: count, units_with_section: count },
  tables: { tables_total: 0, tables_query_ready: 0, cells_total: 0, cells_typed: 0, unqueryable: [] },
  locator: { resolved: 0, denominator: 0, degraded: 0 },
})
describe('quality report identity and refresh', () => {
  beforeEach(() => vi.clearAllMocks())
  it('切库清空旧报告并忽略旧请求，重新进入和手动刷新更新数据', async () => {
    let resolveOld!: (value: unknown) => void
    getKbQuality.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValue(report(22))
    const wrapper = mount(KbQualityPanel, { props: { kbId: 'a', active: true } })
    await wrapper.setProps({ kbId: 'b' })
    await flushPromises()
    expect(getKbQuality).toHaveBeenLastCalledWith('b')
    expect(wrapper.text()).toContain('22')
    resolveOld(report(99))
    await flushPromises()
    expect(wrapper.text()).not.toContain('99')
    await wrapper.setProps({ active: false })
    getKbQuality.mockResolvedValue(report(33))
    await wrapper.setProps({ active: true })
    await flushPromises()
    expect(wrapper.text()).toContain('33')
    getKbQuality.mockResolvedValue(report(44))
    await wrapper.get('[data-testid="quality-refresh"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('44')
    wrapper.unmount()
  })
})
