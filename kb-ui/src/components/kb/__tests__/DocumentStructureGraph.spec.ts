import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@vue-flow/core', () => ({
  VueFlow: {
    name: 'VueFlow',
    props: ['nodes', 'edges', 'deleteKeyCode'],
    emits: ['node-click'],
    template: `<div data-testid="vue-flow-mock">
      <button v-for="node in nodes" :key="node.id" :data-testid="'flow-' + node.id"
        @click="$emit('node-click', { node })">{{ node.data.label }}</button>
      <slot />
    </div>`,
  },
  Handle: { template: '<span />' },
  Position: { Left: 'left', Right: 'right' },
}));
vi.mock('@vue-flow/background', () => ({ Background: { template: '<span />' } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { template: '<span />' } }))

import DocumentStructureGraph from '@/components/kb/DocumentStructureGraph.vue'

function result(snapshotId = 'snapshot-1') {
  return {
    view: 'current_serving' as const,
    snapshot: {
      id: snapshotId, title: '产业知识方案', mime_type: 'application/docx',
      quality_status: 'PASS', lifecycle_status: 'READY', parser_fingerprint: 'p@1',
      compiler_fingerprint: 'c@1', snapshot_fingerprint: 'f', created_by_run_id: 'r1',
      created_at: '2026-09-04T00:00:00Z', source_storage_object_id: 'o1',
      source_content_revision: 1,
    },
    outline: [
      { element_id: 'h1', level: 1, title: '总体方案', order_index: 0 },
      { element_id: 'h2', level: 2, title: '接入方式', order_index: 4 },
    ],
    elements: { count: 12, items: [] },
    tables: [{
      table_id: 't1', rows: 2, columns: 2, header: ['字段', '含义'],
      preview: [['mode', '<script>alert(1)</script>']], source_element_id: 'te1',
      parent_section_element_id: 'h1', caption: '接入参数', preview_truncated: true,
    }],
    segments: {
      count: 8,
      items: [{
        segment_index: 0, block_type: 'prose', heading_chain: [{ level: 1, title: '总体方案' }],
        text: '<img src=x onerror=alert(1)>真实正文', element_ids: ['p1'],
        section_element_id: 'h1', source_order_start: 1, source_order_end: 1,
        table_ref: null, table_caption: null,
      }],
    },
    diagnostics: { warnings: [], containers: 3, relations: 2 },
  }
}

describe('DocumentStructureGraph V2', () => {
  it('呈现可搜索大纲、局部只读图和内容 Inspector 三个工作区', () => {
    const wrapper = mount(DocumentStructureGraph, {
      props: { documentTitle: '产业知识方案.docx', result: result() },
    })

    expect(wrapper.find('[data-testid="structure-outline-pane"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="structure-local-graph"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="structure-inspector"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('确定性结构层')
  })

  it('点击章节后 Inspector 直接显示该节当前返回的真实 segments，且只做文本插值', async () => {
    const wrapper = mount(DocumentStructureGraph, {
      props: { documentTitle: '产业知识方案.docx', result: result() },
    })

    await wrapper.get('[data-testid="outline-section:h1"]').trigger('click')

    const inspector = wrapper.get('[data-testid="structure-inspector"]')
    expect(inspector.text()).toContain('总体方案')
    expect(inspector.text()).toContain('<img src=x onerror=alert(1)>真实正文')
    expect(inspector.find('img').exists()).toBe(false)
  })

  it('点击表格后显示 caption、header、真实 preview 和截断提示，不渲染单元格HTML', async () => {
    const wrapper = mount(DocumentStructureGraph, {
      props: { documentTitle: '产业知识方案.docx', result: result() },
    })

    await wrapper.get('[data-testid="outline-table:t1"]').trigger('click')

    const inspector = wrapper.get('[data-testid="structure-inspector"]')
    expect(inspector.text()).toContain('接入参数')
    expect(inspector.text()).toContain('字段')
    expect(inspector.text()).toContain('mode')
    expect(inspector.text()).toContain('<script>alert(1)</script>')
    expect(inspector.text()).toContain('当前仅显示预览数据')
    expect(inspector.find('script').exists()).toBe(false)
  })

  it('表格携带未知章节编号时仍明确显示所属章节不可用', async () => {
    const unknownParent = result()
    unknownParent.tables[0].parent_section_element_id = 'missing-section'
    const wrapper = mount(DocumentStructureGraph, {
      props: { documentTitle: '产业知识方案.docx', result: unknownParent },
    })

    await wrapper.get('[data-testid="outline-table:t1"]').trigger('click')

    expect(wrapper.get('[data-testid="structure-inspector"]').text())
      .toContain('所属章节暂不可用')
  })

  it('大文档返回被截断时显示 partial 提示', () => {
    const partial = result()
    partial.segments.count = 200
    partial.elements.count = 900
    const wrapper = mount(DocumentStructureGraph, {
      props: { documentTitle: '产业知识方案.docx', result: partial },
    })

    expect(wrapper.get('[data-testid="structure-partial-notice"]').text())
      .toContain('当前工作台仅展示接口已返回的内容')
  })

  it('搜索过滤章节和表格入口，并保留文档根', async () => {
    const wrapper = mount(DocumentStructureGraph, {
      props: { documentTitle: '产业知识方案.docx', result: result() },
    })

    await wrapper.get('[data-testid="structure-search"]').setValue('接入参数')

    expect(wrapper.find('[data-testid="outline-section:h1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="outline-table:t1"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('产业知识方案.docx')
  })

  it('切换文档版本时选择状态回到文档根，不保留旧版本节点', async () => {
    const wrapper = mount(DocumentStructureGraph, {
      props: { documentTitle: '产业知识方案.docx', result: result('snapshot-1') },
    })
    await wrapper.get('[data-testid="outline-section:h1"]').trigger('click')
    expect(wrapper.get('[data-testid="structure-inspector"]').text()).toContain('总体方案')

    const next = result('snapshot-2')
    next.outline = [{ element_id: 'new', level: 1, title: '新版本章节', order_index: 0 }]
    next.segments.items = []
    await wrapper.setProps({ result: next })

    expect(wrapper.get('[data-testid="structure-inspector"]').text()).toContain('产业知识方案.docx')
    expect(wrapper.get('[data-testid="structure-inspector"]').text()).not.toContain('总体方案')
  })

  it('局部图直接下级超过上限时显式提示截断数量，不静默丢失', () => {
    const wide = result()
    wide.outline = Array.from({ length: 26 }, (_, index) => ({
      element_id: `w${index}`, level: 1, title: `宽章节 ${index}`, order_index: index * 2,
    }))
    wide.segments.items = []
    wide.tables = []
    const wrapper = mount(DocumentStructureGraph, {
      props: { documentTitle: '产业知识方案.docx', result: wide },
    })

    const notice = wrapper.get('[data-testid="structure-local-truncated"]')
    expect(notice.text()).toContain('共 26 个直接下级')
    expect(notice.text()).toContain('前 24 个')
  })

  it('导航入口被截断时，搜索空态说明只在已展示范围内匹配，不误报无匹配', async () => {
    const huge = result()
    huge.outline = Array.from({ length: 241 }, (_, index) => ({
      element_id: `s${index}`, level: 1, title: `章节 ${index}`, order_index: index * 2,
    }))
    huge.segments.items = []
    huge.tables = []
    const wrapper = mount(DocumentStructureGraph, {
      props: { documentTitle: '产业知识方案.docx', result: huge },
    })

    await wrapper.get('[data-testid="structure-search"]').setValue('不存在的关键词')

    expect(wrapper.get('.structure-outline__empty').text())
      .toContain('未在已展示的前 240 个入口中匹配到')
  })
})
