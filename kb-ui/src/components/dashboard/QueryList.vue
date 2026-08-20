<template>
  <ul class="qlist">
    <li v-for="item in items" :key="item.text" class="qlist__row">
      <span class="qlist__text" :title="item.text">{{ item.text }}</span>
      <span class="qlist__count">{{ item.count }} 次</span>
      <span
        v-if="item.note"
        class="qlist__note"
        :class="{ 'qlist__note--warn': item.noteTone === 'warn' }"
      >
        {{ item.note }}
      </span>
    </li>
  </ul>
</template>

<script setup lang="ts">
/**
 * 查询原文清单。概览页的「答不上来的问题」与设置页的三处清单共用同一份标记与样式。
 *
 * 抽成组件而不是把 CSS 提到 global：重复的不只是样式，还有那段 li 结构（文本省略 +
 * 次数 + 可选注记）。只提 CSS 的话，三处的 DOM 仍会各写各的，迟早长歪。
 *
 * `text` 直接来自用户输入（后端已截断到 200 字），因此调用方必须是 admin-only 的场景。
 */
export interface QueryListItem {
  /** 用户输入原文，同时作为 :key —— 后端已按原文聚合，同一列表内不会重复。 */
  text: string
  count: number
  /** 可选注记：如「N 次无结果」或最近一次被问到的日期。 */
  note?: string
  /** warn 会把注记染成警告色；不传则用弱化色。 */
  noteTone?: 'muted' | 'warn'
}

defineProps<{ items: QueryListItem[] }>()
</script>

<style scoped>
.qlist {
  list-style: none;
  margin: 0;
  padding: 0;
}

.qlist__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 4px;
  font-size: 13px;
  border-bottom: 1px solid var(--kb-border-light);
}

.qlist__row:last-child {
  border-bottom: none;
}

.qlist__text {
  flex: 1;
  color: var(--kb-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qlist__count {
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.qlist__note {
  color: var(--kb-text-tertiary);
  font-size: 12px;
  flex-shrink: 0;
}

.qlist__note--warn {
  color: var(--kb-warning);
}
</style>
