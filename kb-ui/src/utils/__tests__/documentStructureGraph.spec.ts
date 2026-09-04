import { describe, expect, it } from 'vitest'

import { buildDocumentStructureGraph } from '@/utils/documentStructureGraph'

const outline = [
  { element_id: 'h1', level: 1, title: '总体方案' },
  { element_id: 'h2', level: 2, title: '接入方式' },
  { element_id: 'h3', level: 2, title: '消费方式' },
  { element_id: 'h4', level: 1, title: '安全要求' },
]

describe('buildDocumentStructureGraph', () => {
  it('把线上解析结果编排为文档、章节、表格的确定性关系图', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '产业知识方案.docx',
      outline,
      tables: [
        { table_id: 't1', rows: 6, columns: 3, header: ['字段', '含义', '要求'], preview: [] },
      ],
      segmentCount: 18,
      elementCount: 42,
      relationCount: 7,
    })

    expect(graph.nodes.map(node => [node.id, node.kind, node.label])).toEqual([
      ['document:root', 'document', '产业知识方案.docx'],
      ['section:h1', 'section', '总体方案'],
      ['section:h2', 'section', '接入方式'],
      ['section:h3', 'section', '消费方式'],
      ['section:h4', 'section', '安全要求'],
      ['table:t1', 'table', '表格 1'],
    ])
    expect(graph.edges.map(edge => [edge.source, edge.target])).toEqual([
      ['document:root', 'section:h1'],
      ['section:h1', 'section:h2'],
      ['section:h1', 'section:h3'],
      ['document:root', 'section:h4'],
      ['document:root', 'table:t1'],
    ])
    expect(graph.summary).toEqual({
      sections: 4,
      tables: 1,
      segments: 18,
      elements: 42,
      relations: 7,
    })
  })

  it('标题重名时仍使用 element_id 保持节点身份，不按名称错误合并', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '重复标题',
      outline: [
        { element_id: 'a', level: 1, title: '配置' },
        { element_id: 'b', level: 1, title: '配置' },
      ],
      tables: [],
      segmentCount: 0,
      elementCount: 2,
      relationCount: 0,
    })

    expect(graph.nodes.filter(node => node.kind === 'section').map(node => node.id))
      .toEqual(['section:a', 'section:b'])
  })

  it('层级跳跃时始终连接到最近的较低级标题', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '跳级标题',
      outline: [
        { element_id: 'h3-first', level: 3, title: '首个三级标题' },
        { element_id: 'h1', level: 1, title: '一级标题' },
        { element_id: 'h3', level: 3, title: '三级标题' },
        { element_id: 'h2', level: 2, title: '二级标题' },
      ],
      tables: [], segmentCount: 0, elementCount: 0, relationCount: 0,
    })

    expect(graph.edges.map(item => [item.source, item.target])).toEqual([
      ['document:root', 'section:h3-first'],
      ['document:root', 'section:h1'],
      ['section:h1', 'section:h3'],
      ['section:h1', 'section:h2'],
    ])
  })

  it('重复 element_id 不冲突并产生可见诊断', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '异常身份',
      outline: [
        { element_id: 'same', level: 1, title: '章节一' },
        { element_id: 'same', level: Number.NaN, title: '章节二' },
      ],
      tables: [], segmentCount: 0, elementCount: 0, relationCount: 0,
    })

    expect(graph.nodes.map(node => node.id)).toEqual([
      'document:root', 'section:same', 'section:same:1',
    ])
    expect(graph.diagnostics).toContain('发现重复结构元素编号 same，画布已保留为独立节点')
  })

  it('表格只连接文档根节点，不虚构表格所属章节', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '表格文档',
      outline: [{ element_id: 'h1', level: 1, title: '参数' }],
      tables: [
        { table_id: 't1', rows: 2, columns: 2, header: [], preview: [] },
        { table_id: 't2', rows: 3, columns: 4, header: [], preview: [] },
      ],
      segmentCount: 0,
      elementCount: 0,
      relationCount: 0,
    })

    expect(graph.edges.filter(edge => edge.target.startsWith('table:')))
      .toEqual([
        { id: 'contains:document:root:table:t1', source: 'document:root', target: 'table:t1' },
        { id: 'contains:document:root:table:t2', source: 'document:root', target: 'table:t2' },
      ])
  })

  it('不修改接口返回的原始数组', () => {
    const frozenOutline = Object.freeze(outline.map(node => Object.freeze({ ...node })))
    const frozenTables = Object.freeze([
      Object.freeze({ table_id: 't1', rows: 1, columns: 1, header: Object.freeze([]), preview: Object.freeze([]) }),
    ])

    const before = JSON.stringify({ outline: frozenOutline, tables: frozenTables })
    expect(() => buildDocumentStructureGraph({
      documentTitle: '不可变输入',
      outline: frozenOutline,
      tables: frozenTables,
      segmentCount: 1,
      elementCount: 1,
      relationCount: 1,
    })).not.toThrow()
    expect(JSON.stringify({ outline: frozenOutline, tables: frozenTables })).toBe(before)
  })

  it('大文档按展示上限截断，并保留真实总量用于提示', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '超长文档',
      outline: Array.from({ length: 5 }, (_, index) => ({
        element_id: `h${index}`,
        level: 1,
        title: `章节 ${index}`,
      })),
      tables: [],
      segmentCount: 100,
      elementCount: 200,
      relationCount: 4,
      maxSections: 3,
    })

    expect(graph.nodes.filter(node => node.kind === 'section')).toHaveLength(3)
    expect(graph.summary.sections).toBe(5)
    expect(graph.omitted).toEqual({ sections: 2, tables: 0 })
  })

  it('允许显式关闭章节和表格节点展示', () => {
    const graph = buildDocumentStructureGraph({
      documentTitle: '只看文档',
      outline,
      tables: [{ table_id: 't1', rows: 1, columns: 1, header: [], preview: [] }],
      segmentCount: 0, elementCount: 0, relationCount: 0,
      maxSections: 0,
      maxTables: 0,
    })

    expect(graph.nodes.map(node => node.id)).toEqual(['document:root'])
    expect(graph.omitted).toEqual({ sections: 4, tables: 1 })
  })
})
