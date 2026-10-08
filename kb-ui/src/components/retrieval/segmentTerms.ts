/**
 * 查询文本分词与词频聚合——「查询热词」词云的原料。
 *
 * 分词用浏览器内置 Intl.Segmenter（zh 词级，Chrome 87+/Edge 87+），零依赖；
 * 环境不支持时退化为 CJK 二元组 + 英文单词切分，词频信号仍可用。
 * 原句级 GROUP BY 的"热门查询"几乎全是 count=1 的噪音，分词后才能看出热点主题。
 */

export interface QueryFrequency {
  /** 去重后的原始问句。 */
  text: string
  /** 该问句出现次数（按次加权）。 */
  count: number
  /** 其中零结果的次数（词云外的分析备用，当前仅透传）。 */
  noResult?: number
}

export interface TermFrequency {
  term: string
  count: number
  noResult: number
}

/** 功能词/口语助词/检索动词——出现再多也不构成"热点主题"。 */
const STOPWORDS: ReadonlySet<string> = new Set([
  '的', '了', '吗', '呢', '吧', '啊', '哦', '嗯',
  '是', '在', '有', '和', '与', '或', '及', '等', '个', '些',
  '我', '你', '他', '她', '它', '我们', '你们', '他们', '咱', '大家',
  '什么', '怎么', '怎样', '怎么样', '如何', '为什么', '哪些', '哪个', '哪里',
  '这', '那', '这个', '那个', '这些', '那些',
  '请', '帮', '帮忙', '一下', '现在', '目前', '今天', '最近',
  '应该', '可以', '可能', '能', '会', '要', '想', '需要',
  '找', '查', '查看', '查询', '搜索', '检索', '看看', '列出', '显示',
  '所有', '全部', '关于', '对于', '以及', '还有', '没有', '不是', '不能',
  '之后', '之前', '以后', '以前', '因为', '所以', '但是', '而且', '然后',
  '如果', '通过', '使用', '的话', '是什么', '多少',
  'the', 'a', 'an', 'of', 'to', 'in', 'on', 'for', 'and', 'or', 'is',
  'are', 'how', 'what', 'which', 'who', 'when', 'where', 'why', 'can',
  'could', 'should', 'would', 'do', 'does', 'did', 'with', 'by', 'from',
  'about', 'please', 'help', 'me', 'my', 'our', 'your',
])

const MIN_TERM_LENGTH = 2
const MAX_TERM_LENGTH = 20
/** 纯数字/日期/时间/版本号片段。 */
const NUMERIC_ONLY = /^[0-9.:/_+-]+$/

function isDroppableTerm(term: string): boolean {
  if (term.length < MIN_TERM_LENGTH || term.length > MAX_TERM_LENGTH) return true
  const lower = term.toLowerCase()
  if (STOPWORDS.has(term) || STOPWORDS.has(lower)) return true
  return NUMERIC_ONLY.test(term)
}

/** 模块级缓存：Segmenter 构造有成本，词级配置全局复用。 */
let segmenterInstance: Intl.Segmenter | null = null

function segment(text: string): string[] {
  if (typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function') {
    segmenterInstance ??= new Intl.Segmenter('zh', { granularity: 'word' })
    const tokens: string[] = []
    for (const part of segmenterInstance.segment(text)) {
      if (part.isWordLike) tokens.push(part.segment)
    }
    return mergeAdjacentSingles(tokens)
  }
  return naiveSegment(text)
}

const isSingleCjk = (token: string): boolean =>
  token.length === 1 && token.charCodeAt(0) > 0x2e7f

/**
 * ICU 词典偏小，领域复合词常被漏切成相邻单字（"计费"→计|费），长度过滤会把
 * 它们整词丢掉。把相邻的单字词贪心两两拼回：计|费→计费，规|则→规则。
 * 误合并（跨词边界的两个孤立单字）远少于漏切损失，可接受。
 */
function mergeAdjacentSingles(tokens: string[]): string[] {
  const merged: string[] = []
  let pending: string | null = null
  for (const token of tokens) {
    if (isSingleCjk(token)) {
      if (pending !== null) {
        merged.push(pending + token)
        pending = null
      } else {
        pending = token
      }
    } else {
      pending = null   // 孤立单字后面跟多字词：单字放弃（长度过滤反正会丢）
      merged.push(token)
    }
  }
  return merged
}

/** 兜底切分：CJK 连续段切二元组，拉丁字母段整词。 */
function naiveSegment(text: string): string[] {
  const tokens: string[] = []
  for (const run of text.split(/[^一-鿿]+/)) {
    for (let i = 0; i + 1 < run.length; i++) tokens.push(run.slice(i, i + 2))
  }
  tokens.push(...(text.match(/[A-Za-z][A-Za-z0-9-]+/g) ?? []))
  return tokens
}

/**
 * 聚合词频：同一问句内重复出现的词只计一次（按问句次数加权），
 * 输出按次数降序（同次数按词序稳定排列）。
 */
export function aggregateTerms(queries: QueryFrequency[]): TermFrequency[] {
  const totals = new Map<string, TermFrequency>()
  for (const { text, count, noResult = 0 } of queries) {
    const trimmed = text.trim()
    if (!trimmed || count <= 0) continue
    const seenInText = new Set<string>()
    for (const term of segment(trimmed)) {
      if (isDroppableTerm(term)) continue
      const key = term.toLowerCase()
      if (seenInText.has(key)) continue
      seenInText.add(key)
      const entry = totals.get(key) ?? { term: key, count: 0, noResult: 0 }
      entry.count += count
      entry.noResult += noResult
      totals.set(key, entry)
    }
  }
  return [...totals.values()].sort(
    (a, b) => b.count - a.count || a.term.localeCompare(b.term),
  )
}
