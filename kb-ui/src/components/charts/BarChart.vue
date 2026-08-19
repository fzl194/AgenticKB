<template>
  <div ref="chartRef" :style="{ width: '100%', height }" />
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart as EchartsBar } from 'echarts/charts'
import { TooltipComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([EchartsBar, TooltipComponent, GridComponent, CanvasRenderer])

const props = withDefaults(defineProps<{
  data: { name: string; value: number; color?: string }[]
  height?: string
  horizontal?: boolean
}>(), {
  height: '260px',
  horizontal: false,
})

const chartRef = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

function render() {
  if (!chart) return
  const names = props.data.map(d => d.name)
  const values = props.data.map(d => d.value)
  const colors = props.data.map(d => d.color || '#0891b2')

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff',
      borderColor: '#e2e8f0',
      borderWidth: 1,
      textStyle: { color: '#0f172a', fontSize: 13 },
    },
    grid: { left: 16, right: 16, top: 16, bottom: 24, containLabel: true },
    xAxis: props.horizontal
      ? { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 11 }, splitLine: { lineStyle: { color: '#f1f5f9' } } }
      : { type: 'category', data: names, axisLabel: { color: '#94a3b8', fontSize: 11 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e2e8f0' } } },
    yAxis: props.horizontal
      ? { type: 'category', data: names, axisLabel: { color: '#94a3b8', fontSize: 11 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e2e8f0' } } }
      : { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 11 }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
    series: [{
      type: 'bar',
      // 圆角只加在「数据端」，贴基线的那头保持直角——两头都圆会让短柱看起来像胶囊，
      // 读不出它是从 0 长出来的。横向条的数据端在右，纵向条在上。
      data: values.map((v, i) => ({
        value: v,
        itemStyle: {
          color: colors[i],
          borderRadius: props.horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
        },
      })),
      barWidth: '50%',
      barMaxWidth: 40,
    }],
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

watch(() => props.data, render, { deep: true })
</script>
