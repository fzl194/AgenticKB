import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@vue-flow/core', () => ({
  VueFlow: {
    name: 'VueFlow',
    props: ['nodes', 'edges', 'deleteKeyCode'],
    emits: ['node-click'],
    template: '<div data-testid="vue-flow-mock"><button data-testid="select-flow-node" @click="$emit(\'node-click\', { node: { id: nodes[1].id } })">select</button><slot /></div>',
  },
  Handle: { template: '<span />' },
  Position: { Left: 'left', Right: 'right' },
}))
vi.mock('@vue-flow/background', () => ({ Background: { template: '<span />' } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { template: '<span />' } }))

import { VueFlow } from '@vue-flow/core'
import DocumentStructureGraph from '@/components/kb/DocumentStructureGraph.vue'

const props = {
  documentTitle: '产业知识方案.docx',
  result: {
    snapshot: {
      id: 'snapshot-1', title: '产业知识方案', mime_type: 'application/docx',
      quality_status: 'PASS', lifecycle_status: 'READY', parser_fingerprint: 'p@1',
      compiler_fingerprint: 'c@1', snapshot_fingerprint: 'f', created_by_run_id: 'r1',
      created_at: '2026-09-04T00:00:00Z', source_storage_object_id: 'o1',
      source_content_revision: 1,
    },
    outline: [
      { element_id: 'h1', level: 1, title: '总体方案' },
      { element_id: 'h2', level: 2, title: '接入方式' },
    ],
    elements: { count: 12, items: [] },
    tables: [{ table_id: 't1', rows: 4, columns: 3, header: [], preview: [] }],
    segments: { count: 8, items: [] },
    diagnostics: { warnings: [], containers: 3, relations: 2 },
  },
}

describe('DocumentStructureGraph', () => {
  it('展示生产结构统计，并明确不是实体本体推断图', () => {
    const wrapper = mount(DocumentStructureGraph, { props })

    expect(wrapper.text()).toContain('文档结构图')
    expect(wrapper.text()).toContain('章节 2')
    expect(wrapper.text()).toContain('表格 1')
    expect(wrapper.text()).toContain('切片 8')
    expect(wrapper.text()).toContain('不包含实体、本体或业务关系推断')
  })

  it('向只读画布传入文档、章节和表格节点及确定性边', () => {
    const wrapper = mount(DocumentStructureGraph, { props })
    const flow = wrapper.getComponent(VueFlow)
    const nodes = flow.props('nodes') as Array<{ id: string; data: { kind: string } }>
    const edges = flow.props('edges') as Array<{ source: string; target: string }>

    expect(nodes.map(node => node.id)).toEqual([
      'document:root', 'section:h1', 'section:h2', 'table:t1',
    ])
    expect(nodes.map(node => node.data.kind)).toEqual([
      'document', 'section', 'section', 'table',
    ])
    expect(edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: 'document:root', target: 'section:h1' }),
      expect.objectContaining({ source: 'section:h1', target: 'section:h2' }),
      expect.objectContaining({ source: 'document:root', target: 'table:t1' }),
    ]))
    expect(flow.props('deleteKeyCode')).toBeNull()
  })

  it('选择节点后展示节点详情', async () => {
    const wrapper = mount(DocumentStructureGraph, { props })

    await wrapper.get('[data-testid="select-flow-node"]').trigger('click')

    expect(wrapper.text()).toContain('章节总体方案1 级标题')
  })

  it('latest_revision 不会被标为当前可搜索版本', () => {
    const wrapper = mount(DocumentStructureGraph, {
      props: {
        ...props,
        result: {
          ...props.result,
          view: 'latest_revision' as const,
          versioning: {
            view: 'latest_revision' as const,
            serving: { document_snapshot_id: 'snapshot-1', build_id: 'b1', source_content_revision: 1 },
            latest: { document_snapshot_id: 'snapshot-2', source_content_revision: 2 },
            in_sync: false,
            latest_state: 'not_in_search' as const,
          },
        },
      },
    })

    expect(wrapper.text()).toContain('最新解析 · 尚未进入搜索')
    expect(wrapper.text()).not.toContain('线上数据')
  })
})
