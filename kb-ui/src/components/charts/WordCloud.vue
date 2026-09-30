<template>
  <div ref="hostRef" class="wordcloud" :style="{ height }">
    <span
      v-for="w in placed"
      :key="w.name"
      class="wordcloud__word"
      :style="{ left: `${w.x}px`, top: `${w.y}px`, fontSize: `${w.size}px`, color: w.color }"
      :title="`${w.name}：${w.value} 次`"
    >{{ w.name }}</span>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'

/**
 * 手写词云（零新依赖——echarts 词云是独立插件 echarts-wordcloud，不在依赖冻结清单内）。
 * 布局：按词频定字号（sqrt 缩放抑制头部垄断），阿基米德螺旋从容器中心向外找
 * 第一个不与已放词碰撞的空位；词宽用字形宽度估算（CJK≈1em、拉丁≈0.58em），
 * 不读 DOM 尺寸——布局是纯计算，jsdom 里可测且换容器尺寸即可重排。
 */
const props = withDefaults(defineProps<{
  items: Array<{ name: string; value: number }>
  height?: string
  /** 最多渲染词数——超过只留高频词，防止长尾把图铺成芝麻。 */
  maxTerms?: number
}>(), {
  height: '240px',
  maxTerms: 60,
})

const COLORS = ['#0891b2', '#f59e0b', '#ef4444', '#8b5cf6', '#10b981', '#6366f1', '#ec4899', '#14b8a6']
const MIN_FONT = 14
const MAX_FONT = 34
/** 首词字号再放大一档，视觉锚点。 */
const TOP_FONT = 40

interface Word { name: string; value: number; size: number; x: number; y: number; color: string }
interface Box { left: number; top: number; right: number; bottom: number }

const hostRef = ref<HTMLDivElement>()
const placed = ref<Word[]>([])
let observer: ResizeObserver | null = null

function estimateWidth(text: string, size: number): number {
  let units = 0
  for (const ch of text) units += ch.charCodeAt(0) > 0x2e7f ? 1 : 0.58
  return units * size
}

function fontSizeFor(value: number, min: number, max: number, index: number): number {
  if (max === min) return index === 0 ? TOP_FONT : 22
  const ratio = (Math.sqrt(value) - Math.sqrt(min)) / (Math.sqrt(max) - Math.sqrt(min))
  const base = MIN_FONT + (MAX_FONT - MIN_FONT) * ratio
  return Math.round(index === 0 ? Math.max(base, TOP_FONT) : base)
}

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom
}

function layout(): void {
  const host = hostRef.value
  const items = [...props.items]
    .filter(item => item.value > 0 && item.name.trim())
    .sort((a, b) => b.value - a.value)
    .slice(0, props.maxTerms)
  if (!host || !items.length) {
    placed.value = []
    return
  }
  // jsdom 无布局，clientWidth=0：给确定性回退尺寸，浏览器里则是真实尺寸
  const width = host.clientWidth || 600
  const height = host.clientHeight || 240
  const cx = width / 2
  const cy = height / 2
  const values = items.map(item => item.value)
  const maxValue = Math.max(...values)
  const minValue = Math.min(...values)

  const boxes: Box[] = []
  const words: Word[] = []
  for (const [index, item] of items.entries()) {
    const size = fontSizeFor(item.value, minValue, maxValue, index)
    const w = estimateWidth(item.name, size)
    const h = size * 1.2
    // 螺旋步进与角度：最多 6 圈（12π），够小容器用完所有空位
    let theta = 0
    let placedWord: Word | null = null
    while (theta < Math.PI * 12) {
      const radius = 2 + (Math.min(width, height) / 2) * (theta / (Math.PI * 12))
      const x = cx + radius * Math.cos(theta)
      const y = cy + radius * Math.sin(theta)
      const box: Box = { left: x - w / 2, top: y - h / 2, right: x + w / 2, bottom: y + h / 2 }
      const inside = box.left >= 0 && box.top >= 0 && box.right <= width && box.bottom <= height
      if (inside && boxes.every(box2 => !overlaps(box, box2))) {
        placedWord = {
          name: item.name, value: item.value, size,
          x: Math.round(x), y: Math.round(y),
          color: COLORS[index % COLORS.length],
        }
        boxes.push(box)
        break
      }
      theta += 0.35
    }
    // 圈内找不到空位的词放弃渲染——图放不下比压在一起可读性好
    if (placedWord) words.push(placedWord)
  }
  placed.value = words
}

onMounted(() => {
  layout()
  if (typeof ResizeObserver !== 'undefined' && hostRef.value) {
    observer = new ResizeObserver(() => layout())
    observer.observe(hostRef.value)
  }
})

onUnmounted(() => {
  observer?.disconnect()
  observer = null
})

watch(() => props.items, layout)

defineExpose({ placed })
</script>

<style scoped>
.wordcloud { position: relative; overflow: hidden; width: 100%; }
.wordcloud__word {
  position: absolute;
  transform: translate(-50%, -50%);
  white-space: nowrap;
  font-weight: 600;
  line-height: 1;
  cursor: default;
  user-select: none;
}
</style>
