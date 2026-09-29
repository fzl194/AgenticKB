/**
 * useRetrievalNames 的直接单测——钉住模块级缓存与竞态语义：
 * 这里是单例状态，回归最不容易被组件测试发现。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const kbApi = vi.hoisted(() => ({ listKbs: vi.fn() }))
const operatorApi = vi.hoisted(() => ({ listParadigms: vi.fn() }))

vi.mock('@/api/kb', () => ({ useKbApi: () => kbApi }))
vi.mock('@/api/operator', () => ({ useOperatorApi: () => operatorApi }))

import { useRetrievalNames } from '@/components/retrieval/useRetrievalNames'

/** 手动放行的 deferred：竞态测试需要精确控制 resolve 顺序。 */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('useRetrievalNames', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useRetrievalNames().reset()
  })

  it('解析当前域的知识库与范式名称，查不到回落原始 ID', async () => {
    kbApi.listKbs.mockResolvedValue([{ id: 'kb-1', name: '云核心网手册' }])
    operatorApi.listParadigms.mockResolvedValue([{ id: 'p-1', name: '告警范式' }])

    const { load, kbName, paradigmName } = useRetrievalNames()
    await load('domain-a')

    expect(kbName('kb-1')).toBe('云核心网手册')
    expect(paradigmName('p-1')).toBe('告警范式')
    // 列表里没有的（已删除库/范式，或列表拉失败）——回落 ID，总能读
    expect(kbName('kb-gone')).toBe('kb-gone')
    expect(paradigmName('p-gone')).toBe('p-gone')
  })

  it('同域重复 load 走缓存；范式整个会话只拉一次', async () => {
    kbApi.listKbs.mockResolvedValue([{ id: 'kb-1', name: 'A 库' }])
    operatorApi.listParadigms.mockResolvedValue([{ id: 'p-1', name: 'P1' }])

    const { load } = useRetrievalNames()
    await load('domain-a')
    await load('domain-a')
    await load('domain-b')

    expect(kbApi.listKbs).toHaveBeenCalledTimes(2) // 每域一次
    expect(operatorApi.listParadigms).toHaveBeenCalledTimes(1) // 会话一次
  })

  it('切域竞态：慢的旧域响应不得覆盖新域的映射', async () => {
    const slowOld = deferred<Array<{ id: string; name: string }>>()
    const fastNew = deferred<Array<{ id: string; name: string }>>()
    // 第一次调用挂起（domain-a），随后切到 domain-b
    kbApi.listKbs.mockImplementationOnce(() => slowOld.promise)
      .mockImplementationOnce(() => fastNew.promise)
    operatorApi.listParadigms.mockResolvedValue([])

    const { load, kbName } = useRetrievalNames()
    const oldLoad = load('domain-a')
    const newLoad = load('domain-b')

    fastNew.resolve([{ id: 'kb-new', name: '新域库' }])
    await newLoad
    expect(kbName('kb-new')).toBe('新域库')

    slowOld.resolve([{ id: 'kb-old', name: '旧域库' }])
    await oldLoad
    // 旧域结果迟到：activeDomain 已是 domain-b，state 不被写回
    expect(kbName('kb-old')).toBe('kb-old')
    expect(kbName('kb-new')).toBe('新域库')
  })

  it('列表拉失败不抛错且清缓存，下次 load 重试', async () => {
    kbApi.listKbs.mockRejectedValueOnce(new Error('down'))
      .mockResolvedValueOnce([{ id: 'kb-1', name: 'A 库' }])
    operatorApi.listParadigms.mockResolvedValue([])

    const { load, kbName } = useRetrievalNames()
    await expect(load('domain-a')).resolves.toBeUndefined()
    expect(kbName('kb-1')).toBe('kb-1')

    await load('domain-a')
    expect(kbApi.listKbs).toHaveBeenCalledTimes(2) // 失败没落缓存，重试了
    expect(kbName('kb-1')).toBe('A 库')
  })

  it('reset 清空缓存与映射，并拦掉在途写入', async () => {
    const pending = deferred<Array<{ id: string; name: string }>>()
    kbApi.listKbs.mockImplementationOnce(() => pending.promise)
    operatorApi.listParadigms.mockResolvedValue([])

    const { load, reset, kbName } = useRetrievalNames()
    const inFlight = load('domain-a')
    reset()

    pending.resolve([{ id: 'kb-1', name: 'A 库' }])
    await inFlight
    expect(kbName('kb-1')).toBe('kb-1') // reset 后在途结果被丢弃
  })
})
