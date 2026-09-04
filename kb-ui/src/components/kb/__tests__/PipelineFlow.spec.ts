/**
 * A0-6（34 号 §P0）：Pipeline 阶段不再回退固定实体/本体流程。
 *
 * 要钉的行为：
 * - 阶段条目由真实 stageEvents 推导——新链事件不渲染「实体抽取/实体归一/
 *   关系抽取/落图」等当前产品不存在的阶段；
 * - 历史 legacy Run 真实记录过实体阶段 → 照常渲染（只显示真实记录到的阶段）；
 * - 无事件 → 不渲染任何阶段骨架（不是一排「等待中」的假流程）。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PipelineFlow from '@/components/kb/PipelineFlow.vue'

function ev(stage: string, status = 'completed') {
  return {
    id: `${stage}-1`, stage, status, created_at: '2026-09-03T00:00:00Z',
    duration_ms: 10,
  }
}

function labels(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('.pipeline-stage__name').map(n => n.text())
}

describe('A0-6 PipelineFlow 动态阶段推导', () => {
  it('新链事件（解析→分段→检索单元→向量化→写入→构建）不渲染实体/本体/落图阶段', () => {
    const wrapper = mount(PipelineFlow, {
      props: { stageEvents: [
        ev('parse'), ev('segment'), ev('retrieval_units'),
        ev('embedding'), ev('db_write'), ev('assemble_build'),
      ] },
    })
    const text = labels(wrapper)
    expect(text).toContain('解析')
    expect(text).toContain('检索单元')
    expect(text).not.toContain('实体抽取')
    expect(text).not.toContain('实体归一')
    expect(text).not.toContain('关系抽取')
    expect(text).not.toContain('落图')
  })

  it('历史 legacy Run 记录过实体阶段 → 按真实事件渲染（含落图）', () => {
    const wrapper = mount(PipelineFlow, {
      props: { stageEvents: [
        ev('parse'), ev('segment'), ev('entity_extract'),
        ev('resolve'), ev('graph_write'),
      ] },
    })
    const text = labels(wrapper)
    expect(text).toContain('解析')
    expect(text).toContain('实体抽取')
    expect(text).toContain('落图')
    // 只显示真实出现过的阶段：没有 retrieval_units 事件就不渲染检索单元
    expect(text).not.toContain('检索单元')
  })

  it('无事件 → 不渲染任何阶段骨架', () => {
    const wrapper = mount(PipelineFlow, { props: { stageEvents: [] } })
    expect(wrapper.findAll('.pipeline-stage')).toHaveLength(0)
  })

  it('阶段顺序保持流水线目录顺序（与事件到达顺序无关）', () => {
    const wrapper = mount(PipelineFlow, {
      props: { stageEvents: [
        ev('db_write'), ev('parse'), ev('segment'),
      ] },
    })
    const text = labels(wrapper)
    expect(text.indexOf('解析')).toBeLessThan(text.indexOf('分段'))
    expect(text.indexOf('分段')).toBeLessThan(text.indexOf('数据写入'))
  })

  it('运行中阶段状态照常判定（started 未 completed → 运行中）', () => {
    const wrapper = mount(PipelineFlow, {
      props: { stageEvents: [ev('parse'), ev('segment', 'started')] },
    })
    const running = wrapper.findAll('.pipeline-stage')
      .filter(s => s.classes().includes('pipeline-stage--running'))
    expect(running).toHaveLength(1)
    expect(running[0].text()).toContain('分段')
  })
})
