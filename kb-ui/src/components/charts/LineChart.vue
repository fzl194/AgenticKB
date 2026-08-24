<template>
  <div ref="chartRef" :style="{ width: '100%', height }" />
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart as EchartsLine } from 'echarts/charts'
import { TooltipComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([EchartsLine, TooltipComponent, GridComponent, CanvasRenderer])

const props = withDefaults(defineProps<{
  labels: string[]
  series: { name: string; data: number[]; color?: string }[]
  height?: string
}>(), {
  height: '260px',
})

const chartRef = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

function render() {
  if (!chart) return
  chart.setOption({
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e2e8f0',
      borderWidth: 1,
      textStyle: { color: '#0f172a', fontSize: 13 },
    },
    grid: { left: 16, right: 16, top: 16, bottom: 24, containLabel: true },
    xAxis: {
      type: 'category',
      data: props.labels,
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    series: props.series.map(s => ({
      name: s.name,
      type: 'line' as const,
      data: s.data,
      smooth: true,
      symbol: 'circle',
      symbolSize: 4,
      lineStyle: { width: 2, color: s.color || '#0891b2' },
      itemStyle: { color: s.color || '#0891b2' },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: (s.color || '#0891b2') + '30' },
          { offset: 1, color: (s.color || '#0891b2') + '05' },
        ]),
      },
    })),
  })
}

/** 具名处理函数，好让 onUnmounted 能摘掉它（原来是内联箭头，永不回收）。 */
function handleResize() {
  chart?.resize()
}

onMounted(() => {
  if (chartRef.value) {
    chart = echarts.init(chartRef.value)
    render()
  }
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

watch(() => [props.labels, props.series], render, { deep: true })
</script>
