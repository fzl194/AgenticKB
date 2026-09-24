import { describe, expect, it } from 'vitest'
import {
  buildNodeIndex, buildParentPathMap, buildPathHints, filterFilesBySelection,
  countUnassignedFileAnchorSlices, effectivePreviewPaths, minimalSubtreePaths,
  resolveRestoreMode, splitSegments,
} from '@/utils/onenetSelection'
import type { OnenetTocFile, OnenetTocNode } from '@/api/onenet'

const node = (path: string, direct: number, children: OnenetTocNode[] = []): OnenetTocNode =>
  ({ title: path.split(' > ').pop() ?? path, path, depth: 1,
     slice_count: direct + children.reduce((sum, child) => sum + child.slice_count, 0),
     direct_slice_count: direct,
     direct_part_min: direct ? 1 : null, direct_part_max: direct ? direct : null,
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
  // 树和 files 均遵守 beta 规则：一个节点的直属切片归其树父节点文件。
  const tree: OnenetTocNode[] = [node('包', 0, [
    node('包 > 接口管理', 1, [
      { ...node('包 > 接口管理 > 告警 > 处理建议', 2, [
        { ...node('包 > 接口管理 > 告警 > 处理建议 > 操作步骤', 1),
          direct_part_min: 4, direct_part_max: 4 },
      ]), title: '告警 > 处理建议', direct_part_min: 2, direct_part_max: 3 },
    ]),
    node('包 > 性能指标', 0, [
      { ...node('包 > 性能指标 > 定位思路', 1), direct_part_min: 5, direct_part_max: 5 },
    ]),
  ])]
  const nodesByPath = buildNodeIndex(tree)
  const parentByPath = buildParentPathMap(tree)
  const files: OnenetTocFile[] = [
    { ...file('包'), heading_title: '接口管理', part_min: 1, part_max: 1 },
    { ...file('包 > 接口管理', 2), heading_title: '告警 > 处理建议', part_min: 2, part_max: 3 },
    { ...file('包 > 接口管理 > 告警 > 处理建议'), heading_title: '操作步骤', part_min: 4, part_max: 4 },
    { ...file('包 > 性能指标'), heading_title: '定位思路', part_min: 5, part_max: 5 },
  ]

  it('empty selection = all files (整包)', () => {
    expect(filterFilesBySelection(files, [], nodesByPath, parentByPath)).toEqual(files)
  })

  it('rule 1: chapter at-or-below filter (勾选章节是文件前缀)', () => {
    const out = filterFilesBySelection(
      files, ['包 > 性能指标'], nodesByPath, parentByPath)
    expect(out.map((f) => f.file_path)).toEqual(['包 > 性能指标'])
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
    expect(paths).toContain('包 > 性能指标')                    // 子树2 后代的宿主文件
    expect(out).toHaveLength(4)
  })

  it('non-minimal subtree inputs still exact (级联子孙已收编)', () => {
    const minimal = filterFilesBySelection(
      files, ['包 > 接口管理'], nodesByPath, parentByPath)
    const out = filterFilesBySelection(
      files,
      ['包 > 接口管理', '包 > 接口管理 > 告警 > 处理建议'],
      nodesByPath, parentByPath)
    expect(out).toEqual(minimal)   // 同一节点命中多棵子树也只统计一次
  })

  it('recomputes count, first heading and part range for a partial parent file', () => {
    const partialTree: OnenetTocNode[] = [node('包', 0, [
      { ...node('包 > 章节A', 1), title: '章节A', direct_part_min: 1, direct_part_max: 1 },
      { ...node('包 > 章节B', 1), title: '章节B', direct_part_min: 2, direct_part_max: 2 },
    ])]
    const partialFiles: OnenetTocFile[] = [{
      ...file('包', 2), heading_title: '章节A', part_min: 1, part_max: 2,
    }]
    const original = partialFiles.map((item) => ({ ...item }))
    const out = filterFilesBySelection(
      partialFiles,
      ['包 > 章节B'],
      buildNodeIndex(partialTree),
      buildParentPathMap(partialTree),
    )
    expect(out).toEqual([{
      ...partialFiles[0], slice_count: 1, heading_title: '章节B', part_min: 2, part_max: 2,
    }])
    expect(partialFiles).toEqual(original)
  })

  it('file-anchor mode assigns descendant slices to the explicit file ancestor', () => {
    const anchorPath = '资料 > 手册.pdf'
    const anchorTree: OnenetTocNode[] = [node('资料', 0, [
      node(anchorPath, 0, [
        node(`${anchorPath} > 第一章`, 0, [
          { ...node(`${anchorPath} > 第一章 > 操作一`, 1), title: '操作一',
            direct_part_min: 1, direct_part_max: 1 },
        ]),
        node(`${anchorPath} > 第二章`, 0, [
          { ...node(`${anchorPath} > 第二章 > 操作二`, 1), title: '操作二',
            direct_part_min: 2, direct_part_max: 2 },
        ]),
      ]),
    ])]
    const anchorFiles: OnenetTocFile[] = [{
      ...file(anchorPath, 2), file_title: '手册.pdf', heading_title: '第一章',
      part_min: 1, part_max: 2,
    }]
    const out = filterFilesBySelection(
      anchorFiles,
      [`${anchorPath} > 第二章 > 操作二`],
      buildNodeIndex(anchorTree),
      buildParentPathMap(anchorTree),
      'file_anchor',
    )
    expect(out).toEqual([{
      ...anchorFiles[0], slice_count: 1, heading_title: '操作二', part_min: 2, part_max: 2,
    }])
  })

  it('file-anchor mode omits selected slices that have no file suffix ancestor', () => {
    const tree: OnenetTocNode[] = [node('资料', 0, [
      { ...node('资料 > 无后缀手册 > 第一章', 1), title: '第一章',
        direct_part_min: 1, direct_part_max: 1 },
    ])]
    expect(filterFilesBySelection(
      [],
      ['资料 > 无后缀手册 > 第一章'],
      buildNodeIndex(tree),
      buildParentPathMap(tree),
      'file_anchor',
    )).toEqual([])
  })
})

describe('resolveRestoreMode', () => {
  it('resolves auto to the backend recommendation but keeps explicit choices', () => {
    expect(resolveRestoreMode('auto', 'file_anchor')).toBe('file_anchor')
    expect(resolveRestoreMode('product_document', 'file_anchor')).toBe('product_document')
  })
})

describe('countUnassignedFileAnchorSlices', () => {
  const anchorPath = '资料 > 手册.pdf'
  const tree: OnenetTocNode[] = [node('资料', 0, [
    node(anchorPath, 0, [
      { ...node(`${anchorPath} > 第一章`, 1), title: '第一章',
        direct_part_min: 1, direct_part_max: 1 },
    ]),
    node('资料 > 无后缀手册', 0, [
      { ...node('资料 > 无后缀手册 > 第一章', 2), title: '第一章',
        direct_part_min: 2, direct_part_max: 3 },
    ]),
  ])]
  const nodesByPath = buildNodeIndex(tree)
  const parentByPath = buildParentPathMap(tree)
  const files: OnenetTocFile[] = [{
    ...file(anchorPath), file_title: '手册.pdf', heading_title: '第一章',
    part_min: 1, part_max: 1,
  }]

  it('does not report whole-document misses when the selected subtree is fully anchored', () => {
    const paths = [`${anchorPath} > 第一章`]
    const selectedFiles = filterFilesBySelection(
      files, paths, nodesByPath, parentByPath, 'file_anchor')
    expect(countUnassignedFileAnchorSlices(
      paths, nodesByPath, selectedFiles, 2,
    )).toBe(0)
  })

  it('reports only misses inside the selected subtree and keeps whole count for empty selection', () => {
    const paths = ['资料 > 无后缀手册']
    const selectedFiles = filterFilesBySelection(
      files, paths, nodesByPath, parentByPath, 'file_anchor')
    expect(countUnassignedFileAnchorSlices(
      paths, nodesByPath, selectedFiles, 2,
    )).toBe(2)
    expect(countUnassignedFileAnchorSlices(
      [], nodesByPath, files, 2,
    )).toBe(2)
  })
})

describe('effectivePreviewPaths', () => {
  it('keeps the current range when edit mode has no newly checked paths', () => {
    expect(effectivePreviewPaths([], ['包 > 当前范围'], true)).toEqual(['包 > 当前范围'])
  })

  it('uses empty paths as whole-package only for a new import', () => {
    expect(effectivePreviewPaths([], ['包 > 当前范围'], false)).toEqual([])
  })
})

describe('buildParentPathMap / buildNodeIndex', () => {
  it('parent from tree structure not string ops', () => {
    const tree: OnenetTocNode[] = [node('r', 0, [node('r > A > B', 1)])]
    expect(buildParentPathMap(tree).get('r > A > B')).toBe('r')
    expect(buildNodeIndex(tree).get('r > A > B')?.path).toBe('r > A > B')
  })


  it('minimalSubtreePaths uses the real tree parent for titles containing >', () => {
    const tree: OnenetTocNode[] = [node('包', 0, [
      { ...node('包 > 告警 > 处理建议', 1), title: '告警 > 处理建议' },
    ])]
    const parentMap = buildParentPathMap(tree)
    expect(minimalSubtreePaths(
      ['包', '包 > 告警 > 处理建议'], parentMap,
    )).toEqual(['包'])
  })


  it('buildPathHints preserves titles containing > as one semantic segment', () => {
    const tree: OnenetTocNode[] = [node('包', 0, [
      { ...node('包 > 告警 > 处理建议', 0, [
        { ...node('包 > 告警 > 处理建议 > 操作步骤', 1), title: '操作步骤' },
      ]), title: '告警 > 处理建议' },
    ])]
    const nodeIndex = buildNodeIndex(tree)
    const parentMap = buildParentPathMap(tree)
    expect(buildPathHints(
      ['包 > 告警 > 处理建议 > 操作步骤'], nodeIndex, parentMap,
    )).toEqual({
      '包 > 告警 > 处理建议 > 操作步骤': ['包', '告警 > 处理建议', '操作步骤'],
    })
  })
})
