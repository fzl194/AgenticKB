import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { apiErrorDetail } from '@/api/proxyClient'
import { useLocaleStore } from '@/stores/locale'
import EmptyState from '@/components/common/EmptyState.vue'
import { runStatusLabel } from '@/utils/runStatus'
import {
  pendingTasks, docStatusChartLabel, documentStatusSlices, unitTypeBars,
} from '@/utils/dashboard'
// 注意：kbMeta 的 docStatusLabel 与 utils/dashboard 的 docStatusChartLabel 是两个函数（同名陷阱见任务头）
import { visibilityLabel, roleLabel, docStatusLabel } from '@/views/kb/kbMeta'
import {
  miningOperatorLabel, miningZoneLabel, groupMiningOperators,
} from '@/utils/miningWorkflowPresentation'
import { managementErrorCodeMessage, managementErrorMessage } from '@/utils/managementError'
import { validateCustomRange } from '@/components/retrieval/timeRange'
import {
  buildDocumentStructureGraph, DOCUMENT_ROOT_ID, UNASSIGNED_GROUP_ID,
} from '@/utils/documentStructureGraph'
import type { KbOverviewItem, KbStats } from '@/types/kb'
import type { MiningOperatorDef } from '@/types/miningWorkflow'

// 顺序铁律：全局键必须先于 pinia 创建写入——store 在创建时读它
beforeEach(() => {
  localStorage.setItem('kb-ui-lang', 'en')
  setActivePinia(createPinia())
})
afterEach(() => localStorage.removeItem('kb-ui-lang'))

describe('mapping utils follow the language switch', () => {
  it('runStatusLabel', () => {
    expect(runStatusLabel('queued')).toBe('Queued')
    expect(runStatusLabel('awaiting_review')).toBe('Awaiting Review')
    expect(runStatusLabel('not_a_status')).toBe('not_a_status')
  })

  it('pendingTasks / docStatusChartLabel / documentStatusSlices / unitTypeBars', () => {
    const kb = {
      id: 'kb1', name: '库一', can_write: true,
      awaiting_review_run_id: 'run1',
      status_counts: { failed: 2, mining: 0, total: 5 },
    } as unknown as KbOverviewItem

    const [review, failed] = pendingTasks([kb])
    expect(review.text).toBe('Mining is paused, awaiting manual review')
    expect(review.actionLabel).toBe('Resolve')
    expect(failed.text).toBe('2 documents failed to parse')
    expect(failed.actionLabel).toBe('View')

    expect(docStatusChartLabel('mined')).toBe('Indexed')
    expect(documentStatusSlices({ has_active_release: false, document_status: { mined: 3 } } as unknown as KbStats)
      .map(slice => slice.name)).toEqual([
      'Indexed', 'Processing', 'Pending Mining', 'Failed', 'Update Failed',
    ])
    expect(unitTypeBars({ table_row: 4 }).map(bar => bar.name)).toEqual(['Table Row'])
  })

  it('mining operator / zone / family labels', () => {
    expect(miningOperatorLabel('embedding')).toBe('Embedding')
    expect(miningOperatorLabel('mystery')).toBe('mystery')
    expect(miningZoneLabel('global')).toBe('Whole Batch')
    const groups = groupMiningOperators([
      { type: 'mystery', zone: 'input' } as unknown as MiningOperatorDef,
    ])
    expect(groups.map(group => group.label)).toEqual(['Other'])
  })

  it('managementError messages', () => {
    expect(managementErrorCodeMessage('user_deleted')).toBe('User is deleted; restore the account first')
    // 中文键是后端 detail 文本的匹配数据——匹配逻辑不变，输出随语言
    expect(managementErrorCodeMessage('不能删除自己的账号')).toBe('You cannot delete your own account')
  })

  it('custom time range validation', () => {
    expect(validateCustomRange({ days: 'custom', startDate: '', endDate: '' }))
      .toBe('Select both a start date and an end date for the custom range')
    expect(validateCustomRange({ days: 'custom', startDate: '2026-02-01', endDate: '2026-01-01' }))
      .toBe('The start date cannot be later than the end date')
    expect(validateCustomRange({ days: 'custom', startDate: '2020-01-01', endDate: '2026-01-01' }))
      .toBe('The custom range cannot exceed 365 days')
  })

  it('kbMeta visibility / role / document status labels', () => {
    expect(visibilityLabel('public')).toBe('Public')
    expect(roleLabel('owner')).toBe('Owner')
    expect(roleLabel('domain_admin')).toBe('Domain Admin')
    expect(docStatusLabel('uploaded')).toBe('Not mined')
    expect(docStatusLabel('mined', true)).toBe('Indexed (unchanged)')
  })

  it('document structure graph labels', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '',
      outline: [{ element_id: 'h1', level: 1, title: '', order_index: 0 }],
      tables: [{
        table_id: 't1', rows: 2, columns: 3, header: [], preview: [],
        source_element_id: 'e1', parent_section_element_id: 'h1',
        caption: '', preview_truncated: false,
      }],
      segments: [{
        segment_index: 0, block_type: 'prose', heading_chain: [], text: 'x', element_ids: [],
        section_element_id: 'missing', source_order_start: 1, source_order_end: 1,
        table_ref: null, table_caption: null,
      }],
      segmentCount: 1, elementCount: 1, relationCount: 0,
    })
    const node = (id: string) => graph.nodes.find(n => n.id === id)
    expect(node(DOCUMENT_ROOT_ID)).toMatchObject({ label: 'Untitled document', subtitle: 'Current document' })
    expect(node('section:h1')).toMatchObject({ label: 'Untitled section', subtitle: 'Heading level 1' })
    expect(node('table:t1')).toMatchObject({ label: 'Table 1', subtitle: '2 rows × 3 columns' })
    expect(node(UNASSIGNED_GROUP_ID)).toMatchObject({ label: 'Unassigned content', subtitle: '1 segments' })
  })

  it('api fallback error text', async () => {
    expect(await apiErrorDetail(new Error(''))).toBe('Request failed')
  })

  it('managementErrorMessage default fallback follows the language', async () => {
    // 改成 t() 后的默认 fallback 与 detail !== t('common.requestFailed') 比较都只有这条 EN 覆盖
    expect(await managementErrorMessage({})).toBe('Request failed')
  })

  it('EmptyState follows the language (imported t helper in a template)', async () => {
    const wrapper = mount(EmptyState)
    expect(wrapper.text()).toContain('No data')
    useLocaleStore().setLang('zh')
    await nextTick()
    expect(wrapper.text()).toContain('暂无数据')
  })
})
