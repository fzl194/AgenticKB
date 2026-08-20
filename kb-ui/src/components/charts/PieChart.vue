<template>
  <div ref="chartRef" :style="{ width: '100%', height }" />
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts/core'
import { PieChart as EchartsPie } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([EchartsPie, TooltipComponent, LegendComponent, CanvasRenderer])

const props = withDefaults(defineProps<{
  data: { name: string; value: number; color?: string }[]
  height?: string
  showLegend?: boolean
}>(), {
  height: '260px',
  showLegend: true,
})

const chartRef = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

function render() {
  if (!chart) return
  chart.setOption({
    tooltip: {
      trigger: 'item',
      backgroundColor: '#fff',
      borderColor: '#e2e8f0',
      borderWidth: 1,
      textStyle: { color: '#0f172a', fontSize: 13 },
    },
    legend: props.showLegend
      ? {
          orient: 'horizontal',
          bottom: 0,
          itemWidth: 10,
          itemHeight: 10,
          itemGap: 14,
          textStyle: { color: '#64748b', fontSize: 12 },
        }
      : undefined,
    series: [{
      type: 'pie',
      radius: ['45%', '72%'],
      center: ['50%', '45%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      emphasis: {
        label: { show: true, fontSize: 14, fontWeight: 600 },
        itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.1)' },
      },
      data: props.data.map(d => ({
        name: d.name,
        value: d.value,
        itemStyle: d.color ? { color: d.color } : undefined,
      })),
    }],
    color: ['#0891b2', '#22d3ee', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6'],
  })
}

/**
 * 具名处理函数，好让 onUnmounted 能摘掉它。
 *
 * 原来是在 setup 顶层挂一个内联箭头（`window.addEventListener('resize', () => ...)`），
 * 既没保留引用也从不移除 —— 每挂载一次就多一个永不回收的监听器。概览页一屏就有
 * 好几个图表实例，来回切页面按实例数累积。
 */
function handleResize() {
  chart?.resize()
}

onMounted(() => {
  if (chartRef.value) {
    chart = echarts.init(chartRef.value)
    render()
  }
  // 放进 onMounted 而不是 setup 顶层：服务端渲染时不会执行，也就不必再判 window 是否存在
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

watch(() => props.data, render, { deep: true })
</script>
