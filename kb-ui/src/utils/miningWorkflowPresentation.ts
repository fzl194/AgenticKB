import type { MiningExecutionZone, MiningOperatorDef } from '@/types/miningWorkflow'
import { t } from '@/locales/t'

export interface MiningOperatorFamily {
  key: string
  labelKey: string
  types: readonly string[]
}

// 批次8 M6：正式目录 9 算子（实体/本体研究线不在 catalog，不分组展示）
export const MINING_OPERATOR_FAMILIES: readonly MiningOperatorFamily[] = [
  { key: 'input_parse', labelKey: 'common.miningFamily.input_parse', types: ['input_ingest', 'document_parse', 'segment_compile'] },
  {
    key: 'retrieval_assets',
    labelKey: 'common.miningFamily.retrieval_assets',
    types: ['retrieval_unit_project', 'query_expansion_generate', 'hierarchical_summary_generate', 'embedding'],
  },
  { key: 'asset_publish', labelKey: 'common.miningFamily.asset_publish', types: ['asset_persist', 'mining_finalize'] },
]

const MINING_OPERATOR_LABEL_KEYS: Readonly<Record<string, string>> = {
  input_ingest: 'common.miningOperator.input_ingest',
  document_parse: 'common.miningOperator.document_parse',
  segment_compile: 'common.miningOperator.segment_compile',
  retrieval_unit_project: 'common.miningOperator.retrieval_unit_project',
  query_expansion_generate: 'common.miningOperator.query_expansion_generate',
  hierarchical_summary_generate: 'common.miningOperator.hierarchical_summary_generate',
  embedding: 'common.miningOperator.embedding',
  asset_persist: 'common.miningOperator.asset_persist',
  mining_finalize: 'common.miningOperator.mining_finalize',
}

export function miningOperatorLabel(operatorType: string): string {
  const key = MINING_OPERATOR_LABEL_KEYS[operatorType]
  return key ? t(key) : operatorType
}

const MINING_ZONE_LABEL_KEYS: Record<MiningExecutionZone, string> = {
  input: 'common.miningZone.input',
  document: 'common.miningZone.document',
  global: 'common.miningZone.global',
}

export function miningZoneLabel(zone: MiningExecutionZone): string {
  return t(MINING_ZONE_LABEL_KEYS[zone])
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
    label: t(family.labelKey),
    items: family.types.flatMap(type => {
      const operator = byType.get(type)
      return operator ? [operator] : []
    }),
  })).filter(group => group.items.length)
  const unknown = operators.filter(operator => !knownTypes.has(operator.type))
  if (unknown.length) groups.push({ key: 'other', label: t('common.miningFamily.other'), items: unknown })
  return groups
}
