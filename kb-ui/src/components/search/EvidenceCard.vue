<template>
  <div class="evidence-card" :class="[`evidence-card--${item.evidenceRole || 'seed'}`, { 'evidence-card--expanded': expanded }]">
    <div class="evidence-card__header" @click="expanded = !expanded">
      <div class="evidence-card__left">
        <span class="evidence-card__idx">#{{ idx + 1 }}</span>
        <span class="evidence-card__role" :class="`evidence-card__role--${item.evidenceRole || 'seed'}`">
          {{ roleLabel }}
        </span>
      </div>
      <div class="evidence-card__right">
        <span class="evidence-card__title">{{ item.title || '-' }}</span>
        <span class="evidence-card__score">{{ item.score.toFixed(3) }}</span>
      </div>
    </div>
    <div class="evidence-card__body" v-show="expanded">
      <p class="evidence-card__text">{{ item.text }}</p>
      <div class="evidence-card__meta" v-if="item.scoreChain">
        <template v-for="(val, key) in item.scoreChain" :key="key">
          <span class="evidence-card__meta-item" v-if="typeof val === 'number'">
            {{ key }}: {{ val.toFixed(3) }}
          </span>
        </template>
        <span class="evidence-card__meta-item" v-if="item.routeSources?.length">
          routes: {{ item.routeSources.join(' + ') }}
        </span>
      </div>
      <div class="evidence-card__tags">
        <span class="evidence-card__tag">{{ item.kind }}</span>
        <span class="evidence-card__tag" v-if="item.semanticRole">{{ item.semanticRole }}</span>
        <span class="evidence-card__tag" v-if="item.blockType">{{ item.blockType }}</span>
        <!--
          上面这段 text 是后端按上下文预算压缩过的（命中项硬截断、其余抽取式摘要），
          所以「查看原文」不是可有可无的装饰，而是拿到完整内容的唯一入口。
          truncated 只用来加强提示，不作为显示条件——摘要式压缩不会留下 ... 痕迹。
        -->
        <el-button
          link
          type="primary"
          size="small"
          class="evidence-card__fulltext"
          @click.stop="emit('viewFullText', item)"
        >
          {{ truncated ? '查看完整原文' : '查看原文' }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SearchContextItem } from '@/types'

const props = defineProps<{
  item: SearchContextItem
  idx: number
}>()

const emit = defineEmits<{ viewFullText: [SearchContextItem] }>()

const expanded = ref(props.idx < 3)

/** 硬截断留下的省略号。抽取式摘要不留痕迹，所以这只是加强提示，不是判据。 */
const truncated = computed(() => props.item.text?.trimEnd().endsWith('...') ?? false)

const roleLabel = computed(() => {
  const map: Record<string, string> = {
    direct_answer: '直接回答', support: '支撑', contrast: '对比',
    background: '背景', missing: '缺失', seed: '种子',
  }
  return map[props.item.evidenceRole] || props.item.role || '未知'
})
</script>

<style scoped>
.evidence-card {
  background: var(--kb-bg-card);
  border-radius: var(--kb-radius-sm);
  border: 1px solid var(--kb-border-light);
  transition: all var(--kb-duration) var(--kb-ease);
  overflow: hidden;
}

.evidence-card:hover {
  border-color: var(--kb-border);
}

.evidence-card--expanded {
  border-color: var(--kb-accent-medium);
}

.evidence-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  gap: 12px;
}

.evidence-card__left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.evidence-card__idx {
  font-size: 12px;
  font-weight: 700;
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.evidence-card__role {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
}

.evidence-card__role--direct_answer { background: var(--kb-success-soft); color: var(--kb-success); }
.evidence-card__role--support { background: var(--kb-accent-soft); color: var(--kb-accent); }
.evidence-card__role--contrast { background: var(--kb-warning-soft); color: var(--kb-warning); }
.evidence-card__role--background { background: var(--kb-border-light); color: var(--kb-text-secondary); }
.evidence-card__role--missing { background: var(--kb-danger-soft); color: var(--kb-danger); }
.evidence-card__role--seed { background: var(--kb-accent-soft); color: var(--kb-accent); }

.evidence-card__right {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.evidence-card__title {
  font-size: 13px;
  color: var(--kb-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

.evidence-card__score {
  font-size: 13px;
  font-weight: 700;
  color: var(--kb-accent);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.evidence-card__body {
  padding: 0 16px 14px;
  border-top: 1px solid var(--kb-border-light);
  padding-top: 12px;
}

.evidence-card__text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--kb-text-primary);
  margin: 0 0 10px;
  white-space: pre-wrap;
}

.evidence-card__meta {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.evidence-card__meta-item {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.evidence-card__tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.evidence-card__tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--kb-border-light);
  color: var(--kb-text-secondary);
  font-weight: 500;
}

.evidence-card__fulltext {
  margin-left: auto;
  font-size: 11px;
  height: auto;
  padding: 0;
}
</style>
