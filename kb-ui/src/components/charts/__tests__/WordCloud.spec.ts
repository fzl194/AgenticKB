/**
 * WordCloud——手写词云组件。
 *
 * 布局是纯计算（词宽用字形宽度估算，不读 DOM），jsdom 下完全可断言：
 * 词频最高→字号最大、全部带"N 次" tooltip、maxTerms 截断长尾、items 更新重排。
 */
import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WordCloud from '../WordCloud.vue'

const ITEMS = [
  { name: '计费', value: 30 },
  { name: '告警', value: 10 },
  { name: '会话', value: 3 },
]

describe('WordCloud', () => {
  it('按词频渲染：首词字号最大，逐级递减，每词带次数 tooltip', async () => {
    const wrapper = mount(WordCloud, { props: { items: ITEMS, height: '200px' } })
    // 布局在 onMounted 里做，DOM 更新要等一个 tick
    await flushPromises()
    const spans = wrapper.findAll('.wordcloud__word')
    expect(spans).toHaveLength(3)
    expect(spans[0].attributes('title')).toBe('计费：30 次')

    // findAll 回 VueNode 联合类型，取 style 需要按 HTMLElement 收窄
    const sizeOf = (i: number) => Number((spans[i].element as HTMLElement).style.fontSize.replace('px', ''))
    expect(sizeOf(0)).toBeGreaterThan(sizeOf(1))
    expect(sizeOf(1)).toBeGreaterThan(sizeOf(2))
  })

  it('maxTerms 截断长尾：渲染词数不超过上限', async () => {
    const many = Array.from({ length: 80 }, (_, i) => ({ name: `词${i}`, value: 100 - i }))
    const wrapper = mount(WordCloud, { props: { items: many, maxTerms: 10, height: '200px' } })
    await flushPromises()
    // 回退容器 600×200 放不下 10 个也允许——上限是硬顶，不保证全放
    expect(wrapper.findAll('.wordcloud__word').length).toBeLessThanOrEqual(10)
  })

  it('空数据不渲染任何词', async () => {
    const wrapper = mount(WordCloud, { props: { items: [], height: '200px' } })
    await flushPromises()
    expect(wrapper.findAll('.wordcloud__word')).toHaveLength(0)
  })

  it('items 变化后重排（summary 刷新驱动）', async () => {
    const wrapper = mount(WordCloud, { props: { items: [{ name: 'a', value: 5 }], height: '200px' } })
    await flushPromises()
    expect(wrapper.findAll('.wordcloud__word')).toHaveLength(1)

    await wrapper.setProps({ items: [{ name: 'b', value: 2 }, { name: 'c', value: 1 }] })
    const names = wrapper.findAll('.wordcloud__word').map(s => s.text())
    expect(names).toEqual(['b', 'c'])
  })
})
