import type { ParadigmGraph } from '@/types/operator'

export interface ParadigmTemplate {
  key: string
  label: string
  description: string
  /** null = blank canvas */
  graph: ParadigmGraph | null
}

// 预置范式模板（批次8 R8，25 号 §9）：算子目录收敛为 7+1
// （scope_resolve/query_embed/fts/dense_vector/rrf/model_rerank/evidence_hydrate/assemble），
// 终点 assemble 产 EvidenceResponse；评测图可直接把召回算子的 candidates 声明为输出
// （§6.18：不再需要 collect 直通节点）。nodeIds 用短字面量（无尾号），避免与后拖入的
// `${type}_${n}` 冲突。
export const PARADIGM_TEMPLATES: ParadigmTemplate[] = [
  {
    key: 'blank',
    label: '空白画布',
    description: '从零开始，自己拖拽编排。',
    graph: null,
  },
  {
    key: 'lexical-evidence',
    label: '① 关键词证据检索',
    description: 'scope_resolve → fts → evidence_hydrate → assemble。不要求 embedding 服务；'
      + '对应官方预置 system-lexical-retrieval（§9.1）。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 120 } },
        { nodeId: 'fts', operatorType: 'fts', params: { topK: 20 }, ui: { x: 300, y: 120 } },
        { nodeId: 'hyd', operatorType: 'evidence_hydrate', params: { mode: 'auto', topN: 50 }, ui: { x: 570, y: 120 } },
        { nodeId: 'asm', operatorType: 'assemble', params: { maxEvidence: 10, maxOutputTokens: 3000 }, ui: { x: 850, y: 120 } },
      ],
      edges: [
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'fts', toSlot: 'scope' },
        { fromNode: 'fts', fromSlot: 'candidates', toNode: 'hyd', toSlot: 'candidates' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'hyd', toSlot: 'scope' },
        { fromNode: 'hyd', fromSlot: 'hydratedEvidence', toNode: 'asm', toSlot: 'hydratedEvidence' },
      ],
      output: { nodeId: 'asm', slot: 'evidenceResponse' },
    },
  },
  {
    key: 'hybrid-evidence',
    label: '② 标准混合证据检索（官方默认）',
    description: 'scope_resolve + query_embed → fts‖dense_vector → rrf → model_rerank → '
      + 'evidence_hydrate → assemble。对应官方默认 system-hybrid-retrieval（§9.2）；'
      + 'rerank 失败自动保序降级。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 40 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 240 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { topK: 20 }, ui: { x: 320, y: 40 } },
        { nodeId: 'fts', operatorType: 'fts', params: { topK: 20 }, ui: { x: 320, y: 200 } },
        { nodeId: 'fuse', operatorType: 'rrf', params: { k: 60 }, ui: { x: 590, y: 120 } },
        { nodeId: 'rr', operatorType: 'model_rerank', params: { topN: 50, topK: 10 }, ui: { x: 840, y: 120 } },
        { nodeId: 'hyd', operatorType: 'evidence_hydrate', params: { mode: 'auto', topN: 50 }, ui: { x: 1090, y: 120 } },
        { nodeId: 'asm', operatorType: 'assemble', params: { maxEvidence: 10, maxOutputTokens: 3000 }, ui: { x: 1350, y: 120 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'fts', toSlot: 'scope' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'fuse', toSlot: 'candidates' },
        { fromNode: 'fts', fromSlot: 'candidates', toNode: 'fuse', toSlot: 'candidates' },
        { fromNode: 'fuse', fromSlot: 'candidates', toNode: 'rr', toSlot: 'candidates' },
        { fromNode: 'rr', fromSlot: 'candidates', toNode: 'hyd', toSlot: 'candidates' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'hyd', toSlot: 'scope' },
        { fromNode: 'hyd', fromSlot: 'hydratedEvidence', toNode: 'asm', toSlot: 'hydratedEvidence' },
      ],
      output: { nodeId: 'asm', slot: 'evidenceResponse' },
    },
  },
  {
    key: 'dense-eval',
    label: '③ 纯向量评测（基线）',
    description: 'query_embed → dense_vector，直接把召回算子的 candidates 声明为输出'
      + '（§9.3：纯向量只作评测图，不建独立正式预置）。测纯向量召回上限。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 60 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 220 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { topK: 20 }, ui: { x: 330, y: 140 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
      ],
      output: { nodeId: 'dv', slot: 'candidates' },
    },
  },
  {
    key: 'fts-eval',
    label: '④ 纯全文评测（基线）',
    description: 'fts 单路，直接输出 candidates。对比混合链里词法通道的单独贡献。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 120 } },
        { nodeId: 'fts', operatorType: 'fts', params: { topK: 20 }, ui: { x: 330, y: 120 } },
      ],
      edges: [
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'fts', toSlot: 'scope' },
      ],
      output: { nodeId: 'fts', slot: 'candidates' },
    },
  },
]
