<template>
  <div ref="el" class="wiki-graph-canvas" data-test="wiki-graph-canvas" />
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type { EChartsOption } from 'echarts'

echarts.use([GraphChart, TooltipComponent, CanvasRenderer])

/**
 * Wiki 图谱画布——echarts 力导向薄封装（范 式=charts/PieChart：tree-shaken
 * echarts.use + 实例生命周期收口）。只负责渲染 option 与转译节点事件，
 * 不含任何业务语义（图模型在 utils/wikiGraph，交互决策在 WikiReaderPanel 右栏）。
 *
 * 节点事件载荷=key（echarts data 项上挂的原字段），双击走原生 dblclick。
 */
const props = defineProps<{
  option: EChartsOption
}>()

const emit = defineEmits<{
  'node-click': [key: string]
  'node-dblclick': [key: string]
}>()

const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value)
  chart.setOption(props.option)
  chart.on('click', params => {
    if ((params as { dataType?: string }).dataType === 'node') emit('node-click', String((params.data as { key: string }).key))
  })
  chart.on('dblclick', params => {
    if ((params as { dataType?: string }).dataType === 'node') emit('node-dblclick', String((params.data as { key: string }).key))
  })
  observer = new ResizeObserver(() => { chart?.resize() })
  observer.observe(el.value)
})

/** 模型变化 → 全量替换 option（force 布局按新初值重启弛豫；
 * notMerge 防残留旧节点/边）。 */
watch(() => props.option, option => {
  chart?.setOption(option, { notMerge: true })
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.wiki-graph-canvas { width: 100%; height: 100%; min-height: 420px; }
</style>
