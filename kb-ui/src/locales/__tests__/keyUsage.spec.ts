import { describe, expect, it } from 'vitest'
import { zh } from '../zh'

// src 目录的源码语料（locales 与测试文件自身除外），经 vite 的 raw glob 在转换期读入——
// 不依赖 node:fs / import.meta.url：src 的 tsconfig 没有 node 类型，且 jsdom 下
// import.meta.url 不是 file 协议。
const sources = import.meta.glob(
  ['@/**/*.vue', '@/**/*.ts', '!@/locales/**', '!@/**/__tests__/**', '!@/**/*.spec.ts', '!@/**/*.test.ts'],
  { eager: true, query: '?raw', import: 'default' },
) as Record<string, string>

// 形如字典 key、但确实不是文案 key 的字面量——出现误报时在此登记并写明来源
const ALLOWED_NON_KEYS = new Set<string>([
  // RetrievalRecordsPanel.vue `v-if="records.length" …`：属性里的 JS 表达式被引号正则当作字面量
  'records.length',
])

// 全部字典 part 的 key 前缀（新增 part 时同步）
const KEY_PREFIX = /^(common|nav|title|header|pwd|login|release|sidebar|dashboard|kbView|kbSearch|kbManage|kbStruct|records|mcp)\./

describe('dictionary key usage', () => {
  const corpus = Object.values(sources).join('\n')
  // 三种引号形态的字符串字面量：'key' / "key" / `key`
  const literals = new Set(
    corpus.match(/['"`]([A-Za-z][A-Za-z0-9_.]*)['"`]/g)?.map(text => text.slice(1, -1)) ?? [],
  )

  it('reads a non-empty source corpus', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(100)
  })

  it('every zh key is referenced by a string literal somewhere in src', () => {
    const unused = Object.keys(zh).filter(key => !literals.has(key))
    expect(unused, '未被引用的字典 key：调用点拼写错误（界面会直接显示 key）或死 key').toEqual([])
  })

  it('every key-shaped literal at call sites is defined in the dictionary', () => {
    const known = new Set(Object.keys(zh))
    const unknown = [...literals]
      .filter(text => KEY_PREFIX.test(text) && !known.has(text) && !ALLOWED_NON_KEYS.has(text))
      .sort()
    expect(unknown, '调用点使用了字典里不存在的 key（zh/en 都会原样显示 key）').toEqual([])
  })
})
