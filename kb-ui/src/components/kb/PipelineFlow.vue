<template>
  <div class="pipeline-flow">
    <div
      v-for="(stage, idx) in stages"
      :key="stage.key"
      class="pipeline-stage"
      :class="[`pipeline-stage--${getStageStatus(stage.key)}`]"
    >
      <div class="pipeline-stage__node">
        <span class="pipeline-stage__icon">{{ stageIcons[stage.key] || '⚙' }}</span>
      </div>
      <div class="pipeline-stage__info">
        <span v-if="stage.line" class="pipeline-stage__line" :class="`pipeline-stage__line--${stage.line}`">
          {{ stage.line === 'ontology' ? '本体线' : '篇章线' }}
        </span>
        <span class="pipeline-stage__name">{{ stage.label }}</span>
        <span class="pipeline-stage__meta">
          <template v-if="getStageStatus(stage.key) === 'completed'">
            {{ formatMs(getStageDuration(stage.key)) }}
          </template>
          <template v-else-if="getStageStatus(stage.key) === 'running'">
            运行中...
          </template>
          <template v-else-if="getStageStatus(stage.key) === 'failed'">
            失败
          </template>
          <template v-else>
            等待中
          </template>
        </span>
      </div>
      <div v-if="idx < stages.length - 1" class="pipeline-stage__connector" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { MiningRunStage } from '@/types'

const props = withDefaults(defineProps<{
  stageEvents: MiningRunStage[]
  allDocsSettled?: boolean  // 全部文档是否已尘埃落定（用于解除逐文档阶段的孤儿 started 误判）
}>(), {
  allDocsSettled: false,
})

interface PipelineStage {
  key: string
  label: string
  backendKeys: string[]
  line?: 'discourse' | 'ontology'  // 篇章线 / 本体线（历史 legacy 阶段）
  scope?: 'document' | 'global'    // 逐文档阶段 / 全局尾段（落图、建库发布）。默认逐文档
}

// 阶段目录（标签映射，含历史 legacy 阶段——旧 Run 的真实事件要能显示名字）。
// A0-6：渲染条目按 stageEvents 动态推导——不再预渲染固定 12 阶段骨架，
// 新链 Run 不出现「实体抽取/落图」等当前产品不存在的阶段。
const STAGE_CATALOG: PipelineStage[] = [
  { key: 'parse', label: '解析', backendKeys: ['parse'] },
  { key: 'segment', label: '分段', backendKeys: ['segment'] },
  { key: 'enrich', label: '段落理解', backendKeys: ['enrich'], line: 'discourse' },
  { key: 'entity_extract', label: '实体抽取', backendKeys: ['entity_extract'], line: 'ontology' },
  { key: 'resolve', label: '实体归一', backendKeys: ['resolve'], line: 'ontology' },
  { key: 'entity_relations', label: '关系抽取', backendKeys: ['entity_relations'], line: 'ontology' },
  { key: 'discourse', label: '语篇分析', backendKeys: ['discourse'], line: 'discourse' },
  { key: 'retrieval_units', label: '检索单元', backendKeys: ['retrieval_units'], line: 'discourse' },
  { key: 'embedding', label: '向量化', backendKeys: ['embedding'] },
  { key: 'db_write', label: '数据写入', backendKeys: ['db_write'] },
  { key: 'graph_write', label: '落图', backendKeys: ['graph_write'], line: 'ontology', scope: 'global' },
  { key: 'build', label: '构建&发布', backendKeys: ['assemble_build', 'validate_build'], scope: 'global' },
]

// 只有真实记录到事件的阶段才渲染（目录顺序，与事件到达顺序无关）
const stages = computed<PipelineStage[]>(() => {
  const seen = new Set(props.stageEvents.map(e => e.stage))
  return STAGE_CATALOG.filter(s => s.backendKeys.some(k => seen.has(k)))
})

const stageIcons: Record<string, string> = {
  parse: '🔍', segment: '✂️', enrich: '🧠', entity_extract: '🏷️',
  resolve: '🔗', entity_relations: '🕸️', discourse: '💬',
  retrieval_units: '🔎', embedding: '🔢', db_write: '💾', graph_write: '📊', build: '📦',
}

function findStageEvents(stage: PipelineStage): MiningRunStage[] {
  return props.stageEvents.filter(s => stage.backendKeys.includes(s.stage))
}

function getStageStatus(key: string): string {
  const stage = stages.value.find(s => s.key === key)
  if (!stage) return 'pending'
  const events = findStageEvents(stage)
  if (events.length === 0) return 'pending'
  if (events.some(e => e.status === 'failed')) return 'failed'
  const started = events.filter(e => e.status === 'started')
  const completed = events.filter(e => e.status === 'completed')
  // 所有 backendKey 都收到 completed → 阶段完成（多 key 阶段如 build 需全齐）
  if (stage.backendKeys.every(bk => events.some(e => e.stage === bk && e.status === 'completed'))) return 'completed'
  // 逐文档阶段在并行流式下会出现"孤儿 started"（某篇文档发了 started、却因失败/丢事件
  // 没发 completed），导致 started>completed 永久判为运行中。一旦全部文档已尘埃落定，
  // 这些孤儿不该再把阶段钉在运行中——只要本阶段至少有一条 completed 就视为完成。
  // 全局尾段（落图/建库）本就在文档落定之后才跑，不套用此豁免，按真实 started/completed 判。
  if (stage.scope !== 'global' && props.allDocsSettled) {
    return completed.length > 0 ? 'completed' : 'running'
  }
  if (started.length > completed.length) return 'running'
  if (completed.length > 0) return 'running'
  return 'pending'
}

function getStageDuration(key: string): number {
  const stage = stages.value.find(s => s.key === key)
  if (!stage) return 0
  return findStageEvents(stage)
    .filter(e => e.status === 'completed' && e.duration_ms != null)
    .reduce((sum, e) => sum + (e.duration_ms ?? 0), 0)
}

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
</script>

<style scoped>
.pipeline-flow {
  display: flex;
  align-items: flex-start;
  overflow-x: auto;
  padding: 8px 0;
  gap: 0;
}

.pipeline-stage {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.pipeline-stage__node {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px solid var(--kb-border);
  background: var(--kb-bg-card);
  transition: all var(--kb-duration) var(--kb-ease);
}

.pipeline-stage--completed .pipeline-stage__node {
  border-color: var(--kb-success);
  background: var(--kb-success-soft);
}

.pipeline-stage--running .pipeline-stage__node {
  border-color: var(--kb-warning);
  background: var(--kb-warning-soft);
  animation: pulse-border 1.5s ease-in-out infinite;
}

.pipeline-stage--failed .pipeline-stage__node {
  border-color: var(--kb-danger);
  background: var(--kb-danger-soft);
}

.pipeline-stage__icon {
  font-size: 16px;
}

.pipeline-stage__info {
  display: flex;
  flex-direction: column;
  margin-left: 8px;
  min-width: 70px;
}

.pipeline-stage__line {
  font-size: 10px;
  line-height: 1.4;
  padding: 0 5px;
  border-radius: 6px;
  width: fit-content;
  margin-bottom: 2px;
}
.pipeline-stage__line--ontology { background: var(--kb-accent-soft); color: var(--kb-accent); }
.pipeline-stage__line--discourse { background: var(--kb-border-light); color: var(--kb-text-secondary); }

.pipeline-stage__name {
  font-size: 12px;
  font-weight: 600;
  color: var(--kb-text-primary);
}

.pipeline-stage__meta {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.pipeline-stage--completed .pipeline-stage__meta { color: var(--kb-success); }
.pipeline-stage--running .pipeline-stage__meta { color: var(--kb-warning); }
.pipeline-stage--failed .pipeline-stage__meta { color: var(--kb-danger); }

.pipeline-stage__connector {
  width: 24px;
  height: 2px;
  background: var(--kb-border);
  margin: 0 4px;
  align-self: center;
  flex-shrink: 0;
}

.pipeline-stage--completed + .pipeline-stage .pipeline-stage__connector,
.pipeline-stage--completed ~ .pipeline-stage__connector {
  background: var(--kb-success);
}

@keyframes pulse-border {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.3); }
  50% { box-shadow: 0 0 0 6px rgba(245, 158, 11, 0); }
}
</style>
