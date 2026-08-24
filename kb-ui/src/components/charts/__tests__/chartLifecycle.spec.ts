/**
 * 图表组件的挂载/卸载生命周期。
 *
 * 钉住的是一个真实泄漏：三个组件原先都在 setup 顶层挂内联箭头
 * `window.addEventListener('resize', () => chart?.resize())`——既没留引用也从不移除，
 * 每挂载一次就多一个永不回收的监听器。概览页一屏 6 个图表实例，来回切页面按 6 累积。
 *
 * 用 addEventListener/removeEventListener 的调用配对来断言，而不是数 window 上的监听器
 * （jsdom 不暴露）。echarts 整体 mock 掉：这里测的是生命周期，不是渲染。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

const chartInstance = vi.hoisted(() => ({
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
}))

vi.mock('echarts/core', () => ({
  init: vi.fn(() => chartInstance),
  use: vi.fn(),
  graphic: {
    // LineChart 的 areaStyle 用到它
    LinearGradient: class { constructor(..._args: unknown[]) {} },
  },
}))
vi.mock('echarts/charts', () => ({
  PieChart: {}, BarChart: {}, LineChart: {},
}))
vi.mock('echarts/components', () => ({
  TooltipComponent: {}, LegendComponent: {}, GridComponent: {},
}))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

import PieChartCmp from '@/components/charts/PieChart.vue'
import BarChartCmp from '@/components/charts/BarChart.vue'
import LineChartCmp from '@/components/charts/LineChart.vue'

const CASES = [
  { name: 'PieChart', component: PieChartCmp, props: { data: [{ name: 'a', value: 1 }] } },
  { name: 'BarChart', component: BarChartCmp, props: { data: [{ name: 'a', value: 1 }] } },
  {
    name: 'LineChart',
    component: LineChartCmp,
    props: { labels: ['01-01'], series: [{ name: 's', data: [1] }] },
  },
]

let addSpy: ReturnType<typeof vi.spyOn>
let removeSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  vi.clearAllMocks()
  addSpy = vi.spyOn(window, 'addEventListener')
  removeSpy = vi.spyOn(window, 'removeEventListener')
})

afterEach(() => {
  addSpy.mockRestore()
  removeSpy.mockRestore()
})

function resizeHandlers(spy: ReturnType<typeof vi.spyOn>) {
  const calls = spy.mock.calls as unknown as Array<[string, unknown]>
  return calls.filter(([event]) => event === 'resize').map(([, fn]) => fn)
}

describe.each(CASES)('$name 生命周期', ({ component, props }) => {
  it('卸载时摘掉 resize 监听——摘不掉就是泄漏', () => {
    const wrapper = mount(component, { props: props as never })

    const added = resizeHandlers(addSpy)
    expect(added).toHaveLength(1)

    wrapper.unmount()

    const removed = resizeHandlers(removeSpy)
    // 必须是**同一个函数引用**：内联箭头即使调了 removeEventListener 也摘不掉
    expect(removed).toContain(added[0])
  })

  it('卸载时销毁 echarts 实例', () => {
    const wrapper = mount(component, { props: props as never })
    wrapper.unmount()

    expect(chartInstance.dispose).toHaveBeenCalledTimes(1)
  })

  it('反复挂载卸载不累积监听器', () => {
    for (let i = 0; i < 3; i++) {
      mount(component, { props: props as never }).unmount()
    }

    // 加了几个就得摘掉几个
    expect(resizeHandlers(addSpy)).toHaveLength(3)
    expect(resizeHandlers(removeSpy)).toHaveLength(3)
  })
})
