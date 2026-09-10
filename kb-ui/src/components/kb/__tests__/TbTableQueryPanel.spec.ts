/**
 * A3 表格精确查询面板（39 号 §3.3）：
 * - 挂载即首查询（拿列 schema + 首屏行），schema 驱动类型标注；
 * - typed error（unknown_field 等）原样呈现为可修正提示，不吞错；
 * - 行点击回传 _row（预览高亮锚）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const queryStructure = vi.fn()

vi.mock('@/api/serving', () => ({
  useServingApi: () => ({ queryStructure }),
}))
vi.mock('element-plus', () => ({ ElMessage: { info: vi.fn(), error: vi.fn() } }))

import TbTableQueryPanel from '@/components/kb/TbTableQueryPanel.vue'

const COLUMNS = [
  { name: '告警码', value_type: 'text', sortable: true, can_aggregate: false,
    operations: ['eq', 'ne', 'in', 'contains', 'is_null', 'count'] },
  { name: '功耗', value_type: 'number', sortable: true, can_aggregate: true,
    operations: ['eq', 'lt', 'gt', 'sum', 'avg'] },
  { name: '投产日期', value_type: 'date', sortable: true, can_aggregate: true,
    operations: ['eq', 'gte', 'min', 'max'] },
]

function okResult(overrides: Record<string, unknown> = {}) {
  return {
    asset_ref: 'a#table:tbl:1', table_name: 'tbl:1',
    columns: COLUMNS,
    rows: [
      { _row: 1, 告警码: 'A1-101', 功耗: '1,234.5', 投产日期: '2026-01-05' },
      { _row: 2, 告警码: 'A1-102', 功耗: '2,000', 投产日期: '2026-09-07' },
    ],
    has_more: false,
    ...overrides,
  }
}

async function mountPanel() {
  const wrapper = mount(TbTableQueryPanel, {
    props: {
      assetRef: 'alarm.xlsx#table:tbl:1', kbId: 'kb-1', domain: 'default',
      defaultColumns: ['告警码', '功耗'],
    },
    global: {
      stubs: {
        ElSelect: { props: ['modelValue'], emits: ['update:modelValue', 'change'], template: '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value); $emit(\'change\', $event.target.value)"><slot /></select>' },
        ElOption: { props: ['value', 'label'], template: '<option :value="value">{{ label }}</option>' },
        ElInput: { props: ['modelValue'], emits: ['update:modelValue'], template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />' },
        ElAlert: { props: ['title', 'description'], template: '<div class="el-alert">{{ title }} {{ description }}<slot /></div>' },
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('A3 表格精确查询面板', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    queryStructure.mockResolvedValue(okResult())
  })

  it('挂载即首查询：行渲染 + schema 列类型可见', async () => {
    const wrapper = await mountPanel()
    expect(queryStructure).toHaveBeenCalledWith(
      'alarm.xlsx#table:tbl:1',
      expect.objectContaining({ where: [] }),
      expect.objectContaining({ domain: 'default', kbId: 'kb-1' }),
    )
    const rows = wrapper.findAll('[data-testid="table-query-row"]')
    expect(rows).toHaveLength(2)
    // 选列来自 defaultColumns ∩ schema
    expect(rows[0].text()).toContain('A1-101')
    expect(wrapper.text()).toContain('数值')
    expect(wrapper.text()).toContain('日期')
  })

  it('行点击回传 _row（预览高亮锚）', async () => {
    const wrapper = await mountPanel()
    await wrapper.findAll('[data-testid="table-query-row"]')[1].trigger('click')
    expect(wrapper.emitted('row-click')?.[0]).toEqual([2])
  })

  it('typed error 原样呈现（可修正），不吞错不退化', async () => {
    queryStructure.mockRejectedValue({
      response: { data: { error: 'type_mismatch', message: '字段 投产日期 需要 日期（YYYY-MM-DD）' } },
    })
    const wrapper = await mountPanel()
    const alert = wrapper.find('[data-testid="table-query-error"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('YYYY-MM-DD')
    expect(wrapper.text()).toContain('类型不匹配')
  })

  it('聚合结果展示（含行计数）', async () => {
    queryStructure.mockResolvedValue(okResult({
      rows: [],
      aggregate: { op: 'sum', field: '功耗', value: 3234.5, row_count: 2 },
    }))
    const wrapper = await mountPanel()
    const agg = wrapper.find('[data-testid="table-query-aggregate"]')
    expect(agg.text()).toContain('3234.5')
    expect(agg.text()).toContain('2')
  })

  it('翻页保留已执行的排序和筛选，修改条件后从第一页重查', async () => {
    queryStructure.mockResolvedValue(okResult({ has_more: true, cursor: 'page-2' }))
    const wrapper = await mountPanel()
    await wrapper.get('[data-testid="table-query-order-field"]').setValue('功耗')
    await wrapper.get('[data-testid="table-query-order-dir"]').setValue('desc')
    await wrapper.findAll('button').find(b => b.text() === '+ 条件')!.trigger('click')
    await wrapper.get('[data-testid="table-query-filter-value"]').setValue('A1')
    await wrapper.get('[data-testid="table-query-run"]').trigger('click')
    await flushPromises()
    const first = queryStructure.mock.calls.at(-1)![1]
    expect(first.where).toHaveLength(1)
    await wrapper.get('[data-testid="table-query-next"]').trigger('click')
    await flushPromises()
    expect(queryStructure.mock.calls.at(-1)![1]).toEqual({ ...first, cursor: 'page-2' })
    await wrapper.get('[data-testid="table-query-filter-value"]').setValue('A2')
    await wrapper.get('[data-testid="table-query-next"]').trigger('click')
    await flushPromises()
    expect(queryStructure.mock.calls.at(-1)![1].cursor).toBeUndefined()
    expect(queryStructure.mock.calls.at(-1)![1].where[0].value).toBe('A2')
    expect(wrapper.findAll('[data-testid="table-query-row"]')).toHaveLength(2)
  })

  it('筛选操作不包含聚合且选列变化后重新查询第一页', async () => {
    queryStructure.mockResolvedValue(okResult({ has_more: true, cursor: 'page-2' }))
    const wrapper = await mountPanel()
    await wrapper.findAll('button').find(b => b.text() === '+ 条件')!.trigger('click')
    const ops = wrapper.get('[data-testid="table-query-filter-op"]').findAll('option').map(o => o.attributes('value'))
    expect(ops).toContain('eq')
    expect(ops).not.toContain('count')
    await wrapper.get('[data-testid="table-query-run"]').trigger('click')
    await flushPromises()
    const check = wrapper.findAllComponents({ name: 'ElCheckbox' })
    // 未全局注册的 el-checkbox 仍可触发 change（与实际复选框事件一致）。
    if (check.length) check[0]!.vm.$emit('change', false)
    else await wrapper.findAll('[data-testid="table-query-col-check"]')[0]!.trigger('change')
    await flushPromises()
    await wrapper.get('[data-testid="table-query-next"]').trigger('click')
    await flushPromises()
    expect(queryStructure.mock.calls.at(-1)![1].cursor).toBeUndefined()
    expect(queryStructure.mock.calls.at(-1)![1].select).toEqual(['功耗'])
  })
})
