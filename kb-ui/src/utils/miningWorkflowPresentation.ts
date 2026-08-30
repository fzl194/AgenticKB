import type { MiningExecutionZone, MiningOperatorDef } from '@/types/miningWorkflow'

export interface MiningOperatorFamily {
  key: string
  label: string
  types: readonly string[]
}

// 批次8 M6：正式目录 9 算子（实体/本体研究线不在 catalog，不分组展示）
export const MINING_OPERATOR_FAMILIES: readonly MiningOperatorFamily[] = [
  { key: 'input_parse', label: '输入与解析', types: ['input_ingest', 'document_parse', 'segment_compile'] },
  {
    key: 'retrieval_assets',
    label: '搜索表示与增强',
    types: ['retrieval_unit_project', 'query_expansion_generate', 'hierarchical_summary_generate', 'embedding'],
  },
  { key: 'asset_publish', label: '资产与发布', types: ['asset_persist', 'mining_finalize'] },
]

export const MINING_ZONE_LABELS: Record<MiningExecutionZone, string> = {
  input: '输入阶段',
  document: '逐文档',
  global: '整批次',
}

export interface MiningOperatorPresentationGroup {
  key: string
  label: string
  items: MiningOperatorDef[]
}

export function groupMiningOperators(operators: MiningOperatorDef[]): MiningOperatorPresentationGroup[] {
  const byType = new Map(operators.map(operator => [operator.type, operator]))
  const knownTypes = new Set(MINING_OPERATOR_FAMILIES.flatMap(family => [...family.types]))
  const groups = MINING_OPERATOR_FAMILIES.map(family => ({
    key: family.key,
    label: family.label,
    items: family.types.flatMap(type => {
      const operator = byType.get(type)
      return operator ? [operator] : []
    }),
  })).filter(group => group.items.length)
  const unknown = operators.filter(operator => !knownTypes.has(operator.type))
  if (unknown.length) groups.push({ key: 'other', label: '其他', items: unknown })
  return groups
}
