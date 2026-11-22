<template>
  <div class="kb-card" @click="$emit('open', kb.id)">
    <div class="kb-card__top">
      <span class="kb-card__name" :title="kb.name">{{ kb.name }}</span>
      <span class="kb-card__badge" :class="`kb-card__badge--${status}`" :title="badgeTitle">
        {{ badgeIcon }}
      </span>
    </div>

    <div class="kb-card__meta">
      <span>{{ t('dashboard.kbCard.docCount', { n: kb.status_counts.total }) }}</span>
      <span class="kb-card__sep">·</span>
      <span>{{ roleLabel }}</span>
    </div>

    <!-- 副行给具体数字，而不是只有一个角标：「2 篇待处理」比一个 ⚠ 有用得多 -->
    <div class="kb-card__sub" :class="{ 'kb-card__sub--alert': status === 'failed' }">
      {{ subline }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { kbCardStatus, type KbCardStatus } from '@/utils/dashboard'
import { t } from '@/locales/t'
import type { KbOverviewItem } from '@/types/kb'
import { roleLabel as kbRoleLabel } from '@/views/kb/kbMeta'

const props = defineProps<{ kb: KbOverviewItem }>()
defineEmits<{ open: [string] }>()

const status = computed(() => kbCardStatus(props.kb))

const badgeIcon = computed(() => (
  { failed: '⚠', mining: '⏳', ready: '✓' }[status.value]
))

/** 角标提示的文案 key——表里只存 key，渲染期经 computed 翻译（切语言即重算，R8）。 */
const BADGE_TITLE_KEYS: Record<KbCardStatus, string> = {
  failed: 'dashboard.kbCard.badgeFailed',
  mining: 'dashboard.kbCard.badgeMining',
  ready: 'dashboard.kbCard.badgeReady',
}

const badgeTitle = computed(() => t(BADGE_TITLE_KEYS[status.value]))

const roleLabel = computed(() => kbRoleLabel(props.kb.my_role) ?? props.kb.my_role)

const subline = computed(() => {
  const { failed, mining, total } = props.kb.status_counts
  if (failed > 0) return t('dashboard.kbCard.failedPending', { n: failed })
  if (mining > 0) return t('dashboard.kbCard.miningProgress', { done: mining, total })
  if (props.kb.last_mined_at) return t('dashboard.kbCard.lastMined', { time: formatTime(props.kb.last_mined_at) })
  return t('dashboard.kbCard.notMined')
})

function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}
</script>

<style scoped>
.kb-card {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  padding: 14px 16px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.kb-card:hover {
  border-color: var(--kb-accent);
  box-shadow: var(--kb-shadow-card);
}

.kb-card__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.kb-card__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--kb-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-card__badge {
  flex-shrink: 0;
  font-size: 13px;
  line-height: 1;
}
.kb-card__badge--failed { color: var(--kb-danger); }
.kb-card__badge--mining { color: var(--kb-warning); }
.kb-card__badge--ready { color: var(--kb-success); }

.kb-card__meta {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--kb-text-secondary);
}

.kb-card__sep {
  color: var(--kb-text-tertiary);
}

.kb-card__sub {
  margin-top: 6px;
  font-size: 12px;
  color: var(--kb-text-tertiary);
}

.kb-card__sub--alert {
  color: var(--kb-danger);
  font-weight: 500;
}
</style>
