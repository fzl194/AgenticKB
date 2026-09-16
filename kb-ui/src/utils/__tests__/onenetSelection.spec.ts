import { describe, expect, it } from 'vitest'
import { minimalSubtreePaths } from '@/utils/onenetSelection'

describe('minimalSubtreePaths', () => {
  it('keeps deep single selection as-is (no ancestor expansion)', () => {
    // 勾深层章节：全选集合只有该节点（半选祖先由调用方排除）
    const out = minimalSubtreePaths(['Pkg.hwics > 02 特性配置 > QoS'])
    expect(out).toEqual(['Pkg.hwics > 02 特性配置 > QoS'])
  })

  it('collapses checked descendants under their checked parent', () => {
    // 勾父节点会自动勾全部子孙——getCheckedNodes 全量返回，最小化只留父
    const out = minimalSubtreePaths([
      'Pkg.hwics > 01 命令参考',
      'Pkg.hwics > 01 命令参考 > aaa',
      'Pkg.hwics > 01 命令参考 > aaa > x',
      'Pkg.hwics > 01 命令参考 > bbb',
    ])
    expect(out).toEqual(['Pkg.hwics > 01 命令参考'])
  })

  it('keeps unrelated siblings and drops empty/duplicate entries', () => {
    const out = minimalSubtreePaths([
      'Pkg.hwics > 01 命令参考 > aaa',
      'Pkg.hwics > 02 特性配置',
      'Pkg.hwics > 02 特性配置 > QoS',
      'Pkg.hwics > 02 特性配置',
      '  ',
      '',
    ])
    expect(out).toEqual([
      'Pkg.hwics > 01 命令参考 > aaa',
      'Pkg.hwics > 02 特性配置',
    ])
  })

  it('never treats package root as selected unless explicitly checked', () => {
    // 若调用方误传半选祖先（包名根），函数只做父子折叠不去根——
    // 契约由调用方保证：只传全选节点。此处固化行为防漂移。
    const out = minimalSubtreePaths(['Pkg.hwics', 'Pkg.hwics > A'])
    expect(out).toEqual(['Pkg.hwics'])
  })
})
