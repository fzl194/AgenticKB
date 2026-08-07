import type { ParadigmGraph } from '@/types/operator'

export interface ParadigmTemplate {
  key: string
  label: string
  description: string
  /** null = blank canvas */
  graph: ParadigmGraph | null
}

// Pre-wired example paradigms (PRD §0 core scenarios). nodeIds use short literals (no trailing
// digits) so they never collide with operators dropped later (those get `${type}_${n}`).
export const PARADIGM_TEMPLATES: ParadigmTemplate[] = [
  {
    key: 'blank',
    label: '空白画布',
    description: '从零开始，自己拖拽编排。',
    graph: null,
  },
  {
    key: 'dense-only',
    label: '① 纯向量检索（基线）',
    description: 'query_embed → dense_vector → collect。测纯向量召回上限；改 dense_vector 的"检索范围"可对比 raw_text/question/both。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 50 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 190 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'raw_text', topK: 20 }, ui: { x: 320, y: 110 } },
        { nodeId: 'out', operatorType: 'collect', params: { maxItems: 20 }, ui: { x: 600, y: 110 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'out', toSlot: 'candidates' },
      ],
      output: { nodeId: 'out', slot: 'candidates' },
    },
  },
  {
    key: 'dense-rerank',
    label: '② 向量 + 模型重排',
    description: 'query_embed → dense_vector → model_rerank → collect。测重排的增益。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 50 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 190 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'both', topK: 20 }, ui: { x: 300, y: 110 } },
        { nodeId: 'rr', operatorType: 'model_rerank', params: { topK: 10 }, ui: { x: 560, y: 110 } },
        { nodeId: 'out', operatorType: 'collect', params: { maxItems: 10 }, ui: { x: 820, y: 110 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'rr', toSlot: 'candidates' },
        { fromNode: 'rr', fromSlot: 'candidates', toNode: 'out', toSlot: 'candidates' },
      ],
      output: { nodeId: 'out', slot: 'candidates' },
    },
  },
  {
    key: 'hybrid-fusion',
    label: '③ 多路混合 + 加权融合',
    description: 'dense_vector ‖ fts → weighted_rrf → collect。复刻并调参现有多路融合。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 40 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 220 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'both', topK: 20 }, ui: { x: 320, y: 40 } },
        { nodeId: 'fts', operatorType: 'fts', params: { topK: 20 }, ui: { x: 320, y: 200 } },
        { nodeId: 'fuse', operatorType: 'weighted_rrf', params: { k: 60, weights: { dense_vector: 1.2, lexical_bm25: 1.0 } }, ui: { x: 600, y: 120 } },
        { nodeId: 'out', operatorType: 'collect', params: { maxItems: 20 }, ui: { x: 860, y: 120 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'fts', toSlot: 'scope' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'fuse', toSlot: 'candidates' },
        { fromNode: 'fts', fromSlot: 'candidates', toNode: 'fuse', toSlot: 'candidates' },
        { fromNode: 'fuse', fromSlot: 'candidates', toNode: 'out', toSlot: 'candidates' },
      ],
      output: { nodeId: 'out', slot: 'candidates' },
    },
  },
  {
    key: 'production-assemble',
    label: '④ 生产链（产 ContextPack）',
    description: '理解 + 向量 + 实体 → 加权融合 → 模型重排 → assemble。终点产出 ContextPack 喂 LLM。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qu', operatorType: 'query_understanding', ui: { x: 40, y: 40 } },
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 180 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 320 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'both', topK: 20 }, ui: { x: 320, y: 120 } },
        { nodeId: 'ee', operatorType: 'entity_exact', params: { topK: 20 }, ui: { x: 320, y: 300 } },
        { nodeId: 'fuse', operatorType: 'weighted_rrf', params: { k: 60 }, ui: { x: 580, y: 200 } },
        { nodeId: 'rr', operatorType: 'model_rerank', params: { topK: 10 }, ui: { x: 800, y: 200 } },
        { nodeId: 'asm', operatorType: 'assemble', params: { maxItems: 10, relationExpansion: true }, ui: { x: 1020, y: 200 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'ee', toSlot: 'scope' },
        { fromNode: 'qu', fromSlot: 'understanding', toNode: 'ee', toSlot: 'understanding' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'fuse', toSlot: 'candidates' },
        { fromNode: 'ee', fromSlot: 'candidates', toNode: 'fuse', toSlot: 'candidates' },
        { fromNode: 'fuse', fromSlot: 'candidates', toNode: 'rr', toSlot: 'candidates' },
        { fromNode: 'rr', fromSlot: 'candidates', toNode: 'asm', toSlot: 'candidates' },
        { fromNode: 'qu', fromSlot: 'understanding', toNode: 'asm', toSlot: 'understanding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'asm', toSlot: 'scope' },
      ],
      output: { nodeId: 'asm', slot: 'contextPack' },
    },
  },
  {
    key: 'hyde',
    label: '⑤ HyDE 向量检索',
    description: 'hyde → dense_vector → collect。用假设性文档嵌入替代查询嵌入，对比 HyDE 增益。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'hyde', operatorType: 'hyde', ui: { x: 40, y: 50 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 190 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'both', topK: 20 }, ui: { x: 320, y: 110 } },
        { nodeId: 'out', operatorType: 'collect', params: { maxItems: 20 }, ui: { x: 600, y: 110 } },
      ],
      edges: [
        { fromNode: 'hyde', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'out', toSlot: 'candidates' },
      ],
      output: { nodeId: 'out', slot: 'candidates' },
    },
  },
  {
    key: 'fts-only',
    label: '⑥ 纯全文检索',
    description: 'fts → collect。只用词法检索（tsvector→trigram→LIKE 三级降级），不需要向量。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 110 } },
        { nodeId: 'fts', operatorType: 'fts', params: { topK: 20 }, ui: { x: 320, y: 110 } },
        { nodeId: 'out', operatorType: 'collect', params: { maxItems: 20 }, ui: { x: 600, y: 110 } },
      ],
      edges: [
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'fts', toSlot: 'scope' },
        { fromNode: 'fts', fromSlot: 'candidates', toNode: 'out', toSlot: 'candidates' },
      ],
      output: { nodeId: 'out', slot: 'candidates' },
    },
  },
  {
    key: 'entity-only',
    label: '⑦ 纯实体精确',
    description: 'query_understanding → entity_exact → collect。按实体名精确匹配，适合命令/网元名查找。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qu', operatorType: 'query_understanding', ui: { x: 40, y: 50 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 190 } },
        { nodeId: 'ee', operatorType: 'entity_exact', params: { topK: 20 }, ui: { x: 320, y: 110 } },
        { nodeId: 'out', operatorType: 'collect', params: { maxItems: 20 }, ui: { x: 600, y: 110 } },
      ],
      edges: [
        { fromNode: 'qu', fromSlot: 'understanding', toNode: 'ee', toSlot: 'understanding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'ee', toSlot: 'scope' },
        { fromNode: 'ee', fromSlot: 'candidates', toNode: 'out', toSlot: 'candidates' },
      ],
      output: { nodeId: 'out', slot: 'candidates' },
    },
  },
  {
    key: 'graph-expand',
    label: '⑧ 向量 + 图扩展',
    description: 'dense_vector → graph_expand → collect。先向量召回，再沿段落关系图 BFS 扩展邻居。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 50 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 230 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'raw_text', topK: 10 }, ui: { x: 300, y: 60 } },
        { nodeId: 'ge', operatorType: 'graph_expand', params: { maxDepth: 2, maxResults: 10 }, ui: { x: 300, y: 220 } },
        { nodeId: 'out', operatorType: 'collect', params: { maxItems: 20 }, ui: { x: 580, y: 140 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'ge', toSlot: 'seeds' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'ge', toSlot: 'scope' },
        { fromNode: 'ge', fromSlot: 'candidates', toNode: 'out', toSlot: 'candidates' },
      ],
      output: { nodeId: 'out', slot: 'candidates' },
    },
  },
  {
    key: 'dense-llm-rerank',
    label: '⑨ 向量 + LLM 重排',
    description: 'query_embed → dense_vector → llm_rerank → collect。用 LLM 列表式重排替代模型重排。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 50 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 190 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'both', topK: 20 }, ui: { x: 300, y: 110 } },
        { nodeId: 'rr', operatorType: 'llm_rerank', params: { topK: 10 }, ui: { x: 560, y: 110 } },
        { nodeId: 'out', operatorType: 'collect', params: { maxItems: 10 }, ui: { x: 820, y: 110 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'rr', toSlot: 'candidates' },
        { fromNode: 'rr', fromSlot: 'candidates', toNode: 'out', toSlot: 'candidates' },
      ],
      output: { nodeId: 'out', slot: 'candidates' },
    },
  },
  {
    key: 'explicit-input',
    label: '⑩ 显式入口节点',
    description: 'request_input → query_embed → dense_vector → collect。用显式 Input 节点把查询接进起点算子。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'in', operatorType: 'request_input', ui: { x: 40, y: 50 } },
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 270, y: 50 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 200 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'raw_text', topK: 20 }, ui: { x: 500, y: 120 } },
        { nodeId: 'out', operatorType: 'collect', params: { maxItems: 20 }, ui: { x: 760, y: 120 } },
      ],
      edges: [
        { fromNode: 'in', fromSlot: 'query', toNode: 'qe', toSlot: 'query' },
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'out', toSlot: 'candidates' },
      ],
      output: { nodeId: 'out', slot: 'candidates' },
    },
  },
  {
    key: 'triple-fusion-assemble',
    label: '⑪ 三路全融合 + 生产链',
    description: 'dense ‖ fts ‖ entity_exact → rrf → model_rerank → assemble。三路召回 + 融合 + 重排 + 产 ContextPack。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qu', operatorType: 'query_understanding', ui: { x: 40, y: 40 } },
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 170 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 300 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'both', topK: 20 }, ui: { x: 300, y: 80 } },
        { nodeId: 'fts', operatorType: 'fts', params: { topK: 20 }, ui: { x: 300, y: 220 } },
        { nodeId: 'ee', operatorType: 'entity_exact', params: { topK: 20 }, ui: { x: 300, y: 360 } },
        { nodeId: 'fuse', operatorType: 'rrf', params: { k: 60 }, ui: { x: 560, y: 200 } },
        { nodeId: 'rr', operatorType: 'model_rerank', params: { topK: 10 }, ui: { x: 780, y: 200 } },
        { nodeId: 'asm', operatorType: 'assemble', params: { maxItems: 10, relationExpansion: true }, ui: { x: 1000, y: 200 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'fts', toSlot: 'scope' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'ee', toSlot: 'scope' },
        { fromNode: 'qu', fromSlot: 'understanding', toNode: 'ee', toSlot: 'understanding' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'fuse', toSlot: 'candidates' },
        { fromNode: 'fts', fromSlot: 'candidates', toNode: 'fuse', toSlot: 'candidates' },
        { fromNode: 'ee', fromSlot: 'candidates', toNode: 'fuse', toSlot: 'candidates' },
        { fromNode: 'fuse', fromSlot: 'candidates', toNode: 'rr', toSlot: 'candidates' },
        { fromNode: 'rr', fromSlot: 'candidates', toNode: 'asm', toSlot: 'candidates' },
        { fromNode: 'qu', fromSlot: 'understanding', toNode: 'asm', toSlot: 'understanding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'asm', toSlot: 'scope' },
      ],
      output: { nodeId: 'asm', slot: 'contextPack' },
    },
  },
  {
    key: 'lite-assemble',
    label: '⑫ 极简生产链（无查询理解）',
    description: 'query_embed → dense_vector → assemble。终点是 assemble 所以照样能绑定到域给 MCP 用，但不接 query_understanding，省掉一次 LLM 往返；同时关掉图扩展。代价：ContextPack 的 query 块没有意图/实体/关键词，条目本身不受影响。',
    graph: {
      schemaVersion: '1.0',
      nodes: [
        { nodeId: 'qe', operatorType: 'query_embed', ui: { x: 40, y: 50 } },
        { nodeId: 'scope', operatorType: 'scope_resolve', ui: { x: 40, y: 190 } },
        { nodeId: 'dv', operatorType: 'dense_vector', params: { textKind: 'both', topK: 20 }, ui: { x: 320, y: 110 } },
        { nodeId: 'asm', operatorType: 'assemble', params: { maxItems: 10, maxExpanded: 0, relationExpansion: false }, ui: { x: 600, y: 110 } },
      ],
      edges: [
        { fromNode: 'qe', fromSlot: 'queryEmbedding', toNode: 'dv', toSlot: 'queryEmbedding' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'dv', toSlot: 'scope' },
        { fromNode: 'dv', fromSlot: 'candidates', toNode: 'asm', toSlot: 'candidates' },
        { fromNode: 'scope', fromSlot: 'scope', toNode: 'asm', toSlot: 'scope' },
      ],
      output: { nodeId: 'asm', slot: 'contextPack' },
    },
  },
]
