import { describe, expect, it } from 'vitest'

import { buildDocumentStructureGraph } from '@/utils/documentStructureGraph'

const outline = [
  { element_id: 'h1', level: 1, title: '总体方案', order_index: 0 },
  { element_id: 'h2', level: 2, title: '接入方式', order_index: 4 },
  { element_id: 'h3', level: 2, title: '消费方式', order_index: 9 },
]

const segments = [
  {
    segment_index: 0, block_type: 'prose', heading_chain: [{ level: 1, title: '总体方案' }],
    text: '总体方案正文', element_ids: ['p1'], section_element_id: 'h1',
    source_order_start: 1, source_order_end: 1, table_ref: null, table_caption: null,
  },
  {
    segment_index: 1, block_type: 'list', heading_chain: [{ level: 1, title: '总体方案' }],
    text: '总体方案清单', element_ids: ['l1'], section_element_id: 'h1',
    source_order_start: 2, source_order_end: 2, table_ref: null, table_caption: null,
  },
  {
    segment_index: 2, block_type: 'code', heading_chain: [{ level: 2, title: '接入方式' }],
    text: 'curl /connect', element_ids: ['c1'], section_element_id: 'h2',
    source_order_start: 5, source_order_end: 5, table_ref: null, table_caption: null,
  },
  {
    segment_index: 3, block_type: 'formula', heading_chain: [{ level: 2, title: '接入方式' }],
    text: 'x = y + 1', element_ids: ['f1'], section_element_id: 'h2',
    source_order_start: 6, source_order_end: 6, table_ref: null, table_caption: null,
  },
  {
    segment_index: 4, block_type: 'figure_caption', heading_chain: [], text: '图 1 接入拓扑',
    element_ids: ['fig1'], section_element_id: 'missing', source_order_start: 12,
    source_order_end: 12, table_ref: null, table_caption: null,
  },
  {
    segment_index: 5, block_type: 'figure', heading_chain: [{ level: 2, title: '接入方式' }],
    text: '图 2 接入时序', element_ids: ['fig2'], section_element_id: 'h2',
    source_order_start: 7, source_order_end: 7, table_ref: null, table_caption: null,
  },
]

const tables = [{
  table_id: 't1', rows: 2, columns: 2, header: ['字段', '含义'],
  preview: [['mode', '接入模式']], source_element_id: 'table-e1',
  parent_section_element_id: 'h1', caption: '接入参数', preview_truncated: true,
}]

describe('buildDocumentStructureGraph', () => {
  it('按 section_element_id 聚合章节内容组，并保留真实 segments', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '产业知识方案.docx', outline, tables, segments,
      segmentCount: 8, elementCount: 42, relationCount: 7,
    })

    const h1 = graph.nodes.find(node => node.id === 'section:h1')
    const prose = graph.nodes.find(node => node.id === 'content:section:h1:prose')
    const list = graph.nodes.find(node => node.id === 'content:section:h1:list')
    const code = graph.nodes.find(node => node.id === 'content:section:h2:code')

    expect(h1?.segments.map(item => item.text)).toEqual(['总体方案正文', '总体方案清单'])
    expect(prose?.segments.map(item => item.segment_index)).toEqual([0])
    expect(list?.segments.map(item => item.segment_index)).toEqual([1])
    expect(code?.segments.map(item => item.segment_index)).toEqual([2])
    expect(graph.edges).toContainEqual(expect.objectContaining({
      source: 'section:h1', target: 'content:section:h1:prose',
    }))
  })

  it('聚合 prose/list/code/formula/figure_caption，无法归属的内容进入 unassigned 组', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '聚合类型', outline, tables: [], segments,
      segmentCount: segments.length, elementCount: 0, relationCount: 0,
    })

    const contentKinds = graph.nodes
      .filter(node => node.kind === 'content_group' && node.contentType !== 'unassigned')
      .map(node => node.contentType)
    expect(contentKinds).toEqual(expect.arrayContaining([
      'prose', 'list', 'code', 'formula', 'figure_caption',
    ]))
    expect(graph.nodes.find(node => node.id === 'content:unassigned')?.segments)
      .toEqual([expect.objectContaining({ text: '图 1 接入拓扑' })])
  })

  it('表格按 parent_section_element_id 连接章节并保留 caption/header/preview', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '表格文档', outline, tables, segments: [],
      segmentCount: 0, elementCount: 0, relationCount: 0,
    })
    const table = graph.nodes.find(node => node.id === 'table:t1')

    expect(table).toMatchObject({
      kind: 'table', label: '接入参数', parentId: 'section:h1',
      table: { header: ['字段', '含义'], preview: [['mode', '接入模式']], preview_truncated: true },
    })
    expect(graph.edges).toContainEqual(expect.objectContaining({
      source: 'section:h1', target: 'table:t1',
    }))
  })

  it('表格切片不重复冒充章节正文，表格内容只由表格节点承载', () => {
    const tableSegment = {
      ...segments[0], segment_index: 9, block_type: 'table_row',
      text: '字段=mode；含义=接入模式', table_ref: 't1',
    }
    const graph = buildDocumentStructureGraph({
      documentTitle: '表格文档', outline, tables,
      segments: [...segments, tableSegment], segmentCount: 6,
      elementCount: 0, relationCount: 0,
    })

    expect(graph.nodes.find(node => node.id === 'section:h1')?.segments)
      .not.toContainEqual(expect.objectContaining({ segment_index: 9 }))
    expect(graph.nodes.some(node => node.id.includes('table_row'))).toBe(false)
  })

  it('未知章节的表格与 segments 进入明确的未归属组，不伪挂文档根', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '未知归属', outline,
      tables: [{ ...tables[0], table_id: 't2', parent_section_element_id: 'missing' }],
      segments, segmentCount: segments.length, elementCount: 0, relationCount: 0,
    })

    expect(graph.nodes.find(node => node.id === 'content:unassigned')).toMatchObject({
      kind: 'content_group', label: '未归属内容', parentId: 'document:root',
    })
    expect(graph.nodes.find(node => node.id === 'table:t2')?.parentId).toBe('content:unassigned')
  })

  it('标题重名仍使用 element_id；重复 element_id 产生可见诊断', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '重复标题',
      outline: [
        { element_id: 'same', level: 1, title: '配置', order_index: 0 },
        { element_id: 'same', level: 1, title: '配置', order_index: 8 },
      ],
      tables: [], segments: [], segmentCount: 0, elementCount: 2, relationCount: 0,
    })

    expect(graph.nodes.map(node => node.id)).toContain('section:same:1')
    expect(graph.diagnostics).toContain('发现重复结构元素编号 same，已保留为独立章节')
  })

  it('按 order_index 排序，并优先使用后端返回的父章节身份', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '乱序大纲',
      outline: [
        { element_id: 'child', level: 2, title: '子节', order_index: 20, parent_section_element_id: 'root' },
        { element_id: 'root', level: 1, title: '根节', order_index: 10, parent_section_element_id: null },
      ],
      tables: [], segments: [], segmentCount: 0, elementCount: 2, relationCount: 0,
    })

    expect(graph.nodes.filter(node => node.kind === 'section').map(node => node.id))
      .toEqual(['section:root', 'section:child'])
    expect(graph.edges).toContainEqual(expect.objectContaining({
      source: 'section:root', target: 'section:child',
    }))
  })

  it('返回数少于总数时标记 partial，并报告未返回内容而非静默完整', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '超长文档', outline, tables, segments,
      segmentCount: 200, elementCount: 900, returnedElementCount: 500, relationCount: 4,
    })

    expect(graph.partial).toBe(true)
    expect(graph.completeness).toEqual({
      returnedSegments: 6, totalSegments: 200,
      returnedElements: 500, totalElements: 900,
    })
  })

  it('在模型构建前限制超大章节和表格集合', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '超大文档',
      outline: Array.from({ length: 550 }, (_, index) => ({
        element_id: `h-${index}`, level: 1, title: `章节 ${index}`, order_index: index,
        parent_section_element_id: null,
      })),
      tables: Array.from({ length: 120 }, (_, index) => ({
        table_id: `t-${index}`, rows: 1, columns: 1, header: ['列'], preview: [['值']],
        source_element_id: `e-${index}`, parent_section_element_id: null,
        caption: `表格 ${index}`, preview_truncated: false,
      })),
      segments: [], segmentCount: 0, elementCount: 0, relationCount: 0,
      sectionCount: 550, tableCount: 120,
    })

    expect(graph.nodes.filter(node => node.kind === 'section')).toHaveLength(500)
    expect(graph.nodes.filter(node => node.kind === 'table')).toHaveLength(100)
    expect(graph.partial).toBe(true)
    expect(graph.diagnostics.join(' ')).toContain('前 500 个章节')
    expect(graph.diagnostics.join(' ')).toContain('前 100 张表格')
  })

  it('不修改接口返回的只读数组', () => {
    const frozenOutline = Object.freeze(outline.map(node => Object.freeze({ ...node })))
    const frozenSegments = Object.freeze(segments.map(segment => Object.freeze({ ...segment })))
    const frozenTables = Object.freeze(tables.map(table => Object.freeze({
      ...table,
      header: Object.freeze([...table.header]),
      preview: Object.freeze(table.preview.map(row => Object.freeze([...row]))),
    })))
    const before = JSON.stringify({ frozenOutline, frozenSegments, frozenTables })

    expect(() => buildDocumentStructureGraph({
      documentTitle: '不可变输入', outline: frozenOutline,
      tables: frozenTables, segments: frozenSegments,
      segmentCount: 5, elementCount: 1, relationCount: 1,
    })).not.toThrow()
    expect(JSON.stringify({ frozenOutline, frozenSegments, frozenTables })).toBe(before)
  })
})
