/**
 * 检索记录的 ID → 名称解析。
 *
 * 检索记录接口（retrieval-records）的响应只带 kb_ids / paradigm_id 原始 ID，
 * 名称在另外两个现成接口里：知识库列表（随域）与检索范式列表（跨域通用）。
 * 这里统一拉一次建映射，供记录明细、使用分析、筛选下拉共用。
 *
 * 定位是"锦上添花"：任何一个列表拉失败都不抛错，回落显示原始 ID，
 * 绝不阻塞记录本身的加载。
 */
import { reactive } from 'vue'
import { useKbApi } from '@/api/kb'
import { useOperatorApi } from '@/api/operator'

export interface NameOption {
  id: string
  name: string
}

/** 知识域 → 该域可见知识库列表的请求缓存（成功才落缓存）。 */
const kbCache = new Map<string, Promise<NameOption[]>>()
/** 范式跨域通用，整个会话只拉一次。 */
let paradigmPromise: Promise<NameOption[]> | null = null
/** 切域竞态守卫：慢的旧域响应不得覆盖新域的映射。 */
let activeDomain = ''

const state = reactive({
  kbOptions: [] as NameOption[],
  paradigmOptions: [] as NameOption[],
})

async function fetchKbOptions(domain: string): Promise<NameOption[]> {
  const kbs = await useKbApi().listKbs(domain)
  return kbs.map(kb => ({ id: kb.id, name: kb.name }))
}

async function fetchParadigmOptions(): Promise<NameOption[]> {
  const paradigms = await useOperatorApi().listParadigms()
  return paradigms.map(p => ({ id: p.id, name: p.name }))
}

export function useRetrievalNames() {
  /** 拉齐当前域的两份名称映射。重复调用走缓存，失败清缓存允许下次重试。 */
  async function load(domain: string): Promise<void> {
    if (!domain) return
    activeDomain = domain
    let kbEntry = kbCache.get(domain)
    if (!kbEntry) {
      kbEntry = fetchKbOptions(domain).catch(reason => {
        kbCache.delete(domain)
        throw reason
      })
      kbCache.set(domain, kbEntry)
    }
    if (!paradigmPromise) {
      paradigmPromise = fetchParadigmOptions().catch(reason => {
        paradigmPromise = null
        throw reason
      })
    }
    const [kbs, paradigms] = await Promise.all([
      kbEntry.catch(() => [] as NameOption[]),
      paradigmPromise.catch(() => [] as NameOption[]),
    ])
    if (activeDomain !== domain) return
    state.kbOptions = kbs
    state.paradigmOptions = paradigms
  }

  /** 查不到（库/范式已删除，或列表还没到）回落原始 ID——总能读。 */
  function kbName(id: string): string {
    return state.kbOptions.find(option => option.id === id)?.name ?? id
  }

  function paradigmName(id: string): string {
    return state.paradigmOptions.find(option => option.id === id)?.name ?? id
  }

  /** 测试专用：清空模块级缓存，避免用例间串味。 */
  function reset(): void {
    kbCache.clear()
    paradigmPromise = null
    activeDomain = ''
    state.kbOptions = []
    state.paradigmOptions = []
  }

  return { state, load, kbName, paradigmName, reset }
}
