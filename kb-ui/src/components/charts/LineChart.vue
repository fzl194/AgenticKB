<template>
  <div ref="chartRef" :style="{ width: '100%', height }" />
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart as EchartsLine } from 'echarts/charts'
import { TooltipComponent, GridComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([EchartsLine, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer])

/** 默认色板（按系列序号取）——调用方不传 color 时多系列不再挤成同色。 */
const PALETTE = ['#0891b2', '#f59e0b', '#ef4444', '#8b5cf6', '#10b981', '#6366f1']

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
  const colors = props.series.map((s, i) => s.color || PALETTE[i % PALETTE.length])
  // 多系列才配图例：单系列标题已说明这条线是什么（Dashboard 用法），省出高度
  const multi = props.series.length > 1
  chart.setOption({
    legend: multi ? {
      top: 0,
      left: 0,
      itemWidth: 14,
      itemHeight: 8,
      icon: 'roundRect',
      textStyle: { color: '#64748b', fontSize: 12 },
    } : undefined,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e2e8f0',
      borderWidth: 1,
      textStyle: { color: '#0f172a', fontSize: 13 },
    },
    grid: { left: 16, right: 16, top: multi ? 34 : 16, bottom: 24, containLabel: true },
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
    series: props.series.map((s, i) => ({
      name: s.name,
      type: 'line' as const,
      data: s.data,
      smooth: true,
      symbol: 'circle',
      symbolSize: 4,
      lineStyle: { width: 2, color: colors[i] },
      itemStyle: { color: colors[i] },
      // 面积填充只给首系列——多系列各自带渐变面会互相叠脏
      areaStyle: i === 0 ? {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: colors[i] + '30' },
          { offset: 1, color: colors[i] + '05' },
        ]),
      } : undefined,
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
