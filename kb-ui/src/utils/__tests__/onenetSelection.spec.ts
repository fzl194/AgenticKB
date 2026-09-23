import { describe, expect, it } from 'vitest'
import {
  buildNodeIndex, buildParentPathMap, filterFilesBySelection,
  minimalSubtreePaths, splitSegments,
} from '@/utils/onenetSelection'
import type { OnenetTocFile, OnenetTocNode } from '@/api/onenet'

const node = (path: string, direct: number, children: OnenetTocNode[] = []): OnenetTocNode =>
  ({ title: path, path, depth: 1, slice_count: direct, direct_slice_count: direct,
     part_min: null, part_max: null, children })

const file = (filePath: string, slices = 1): OnenetTocFile =>
  ({ file_path: filePath, file_title: filePath.split(' > ').pop() ?? filePath,
     heading_title: '', folder_path: '', slice_count: slices, part_min: 0, part_max: 0 })

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

describe('splitSegments', () => {
  it('splits on >, trims and drops empties', () => {
    expect(splitSegments(' A > B >  C ')).toEqual(['A', 'B', 'C'])
    expect(splitSegments('')).toEqual([])
  })
})

describe('filterFilesBySelection', () => {
  // 树：包 > [接口管理(direct=1, 子: 告警 > 处理建议), 性能指标(子: 定位思路)]
  const tree: OnenetTocNode[] = [node('包', 0, [
    node('包 > 接口管理', 1, [
      node('包 > 接口管理 > 告警 > 处理建议', 2),
    ]),
    node('包 > 性能指标', 0, [
      node('包 > 性能指标 > 定位思路', 1),
    ]),
  ])]
  const nodesByPath = buildNodeIndex(tree)
  const parentByPath = buildParentPathMap(tree)
  const files: OnenetTocFile[] = [
    file('包'),                                  // 根级 α 文件（包直属切片的宿主不存在——本树包 direct=0，此文件仅作干扰项）
    file('包 > 接口管理'),                       // 告警>处理建议 等 heading 的宿主文件
    file('包 > 接口管理 > 告警 > 处理建议'),      // 其下更深层切片的文件
    file('包 > 性能指标 > 定位思路'),
  ]

  it('empty selection = all files (整包)', () => {
    expect(filterFilesBySelection(files, [], nodesByPath, parentByPath)).toEqual(files)
  })

  it('rule 1: chapter at-or-below filter (勾选章节是文件前缀)', () => {
    const out = filterFilesBySelection(
      files, ['包 > 性能指标'], nodesByPath, parentByPath)
    expect(out.map((f) => f.file_path)).toEqual(['包 > 性能指标 > 定位思路'])
  })

  it('rule 2: checked node with direct slices pulls in tree-parent file', () => {
    // 勾「包 > 接口管理」（直属1片）→ 其直属切片归父文件「包」，且子树文件都显示
    const out = filterFilesBySelection(
      files, ['包 > 接口管理'], nodesByPath, parentByPath)
    // 实现按 files 原序过滤——两侧都 sort 后比较，避免顺序耦合
    expect(out.map((f) => f.file_path).sort()).toEqual([
      '包 > 接口管理',                        // 恰等（规则1）
      '包 > 接口管理 > 告警 > 处理建议',      // 之下（规则1）
      '包',                                   // 直属切片宿主（规则2）
    ].sort())
    expect(out).toHaveLength(3)
  })

  it('composite: two subtrees at once, both rules per subtree (审查建议)', () => {
    // 同时勾「包 > 接口管理」(direct=1) 与「包 > 性能指标」(direct=0)
    const out = filterFilesBySelection(
      files, ['包 > 接口管理', '包 > 性能指标'], nodesByPath, parentByPath)
    const paths = out.map((f) => f.file_path)
    expect(paths).toContain('包 > 接口管理 > 告警 > 处理建议')  // 子树1 规则1
    expect(paths).toContain('包')                               // 子树1 规则2（direct=1 → 父文件）
    expect(paths).toContain('包 > 性能指标 > 定位思路')         // 子树2 规则1
    expect(paths).not.toContain('包 > 性能指标')                // 子树2 direct=0 → 不触发规则2
    expect(out).toHaveLength(4)
  })

  it('non-minimal subtree inputs still exact (级联子孙已收编)', () => {
    const out = filterFilesBySelection(
      files,
      ['包 > 接口管理', '包 > 接口管理 > 告警 > 处理建议'],
      nodesByPath, parentByPath)
    expect(out).toHaveLength(3)   // 与只传最小子树等价
  })
})

describe('buildParentPathMap / buildNodeIndex', () => {
  it('parent from tree structure not string ops', () => {
    const tree: OnenetTocNode[] = [node('r', 0, [node('r > A > B', 1)])]
    expect(buildParentPathMap(tree).get('r > A > B')).toBe('r')
    expect(buildNodeIndex(tree).get('r > A > B')?.path).toBe('r > A > B')
  })
})
