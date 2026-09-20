# 一张网标题含 > 切分修复与勾选联动 — 实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复知识一张网导入中标题含 `>` 导致的文件错分（title 校准切分，beta-3），并让第三步勾选与右侧文件清单联动（过滤+统计），顺带加树搜索框与首标题列。

**Architecture:** 后端在 `restore.py` 引入 `split_path_calibrated` + `build_calibration`（同 path 单一切法防树键冲突），`restore_files`/`build_path_tree`/`onenet_jsonl` 适配器三处接线；`Selection.matches` 不动（raw 两侧自洽，存量零迁移）。前端新增 `onenetSelection.ts` 纯函数（段前缀规则+父文件规则，与导入语义精确等价），视图层接 `@check` 联动。

**Tech Stack:** FastAPI + psycopg（后端 pytest）；Vue3 + Element Plus + vitest（前端）；`npm run build` 做类型验证（vue-tsc -b）。

**规格文档：** `docs/下一阶段/53-一张网标题含箭头切分修复与勾选联动-设计-2026-09-20.md`（本计划的唯一需求来源，含已知局限 4 条）

**硬约束：**
- 无新增依赖（pyproject/package.json 不动）
- 不碰 `databases/`、`docker/`（codex 数据库线并行施工）
- DB 零结构变更
- 全部命令在 worktree 根执行：`D:\mywork\AgenticKB\.claude\worktrees\fix-onenet-title-split`
- pytest 从仓库根跑（`python -m pytest ...`，已验证基线绿）

---

## Chunk 1: 后端校准切分

### Task 1: restore.py 校准核心 + restore_files/build_path_tree 接线

**Files:**
- Modify: `knowledge_mining/mining/onenet/restore.py`（新增 2 函数、改 2 函数、版本号、docstring）
- Test: `knowledge_mining/tests/onenet/test_restore.py`（新增用例 + 版本钉死断言更新）

- [ ] **Step 1: 写失败测试（校准函数与分组）**

在 `test_restore.py` 追加（放「构造用例」区末尾）：

```python
# ---------------------------------------------------------------- beta-3 校准


def _cal(path: str, title: str):
    from knowledge_mining.mining.onenet.restore import split_path_calibrated
    return split_path_calibrated(path, title)


def test_calibrated_merge_when_title_spans_segments():
    """标题含 >：path 尾部与 title 段对上 → 尾段合并为一个标题段."""
    assert _cal("包 > 接口管理 > 告警 > 处理建议", "告警 > 处理建议") == [
        "包", "接口管理", "告警 > 处理建议"]
    # 普通数据（title 单段）行为不变
    assert _cal("包 > 接口管理 > 实现原理", "实现原理") == ["包", "接口管理", "实现原理"]
    # title 缺失 / 空 → 原行为
    assert _cal("包 > A > B", None) == ["包", "A", "B"]
    assert _cal(None, "A") == []


def test_calibrated_fallback_when_tail_mismatch():
    """title 对不上 path 尾部 → 回退 raw 切分（不劣化）."""
    assert _cal("包 > 接口管理 > 告警", "不相关标题") == ["包", "接口管理", "告警"]
    assert _cal("包 > 接口管理 > 告警 > 处理建议", "告警 > 其他建议") == [
        "包", "接口管理", "告警", "处理建议"]


def test_restore_title_with_gt_splits_file_correctly():
    """Bug 主修复：标题含 > 不再产出假文件「告警」."""
    slices = [
        {"nid": "a", "part_id": 1,
         "path": "包 > 接口管理 > 告警 > 处理建议", "title": "告警 > 处理建议",
         "content": "c1"},
        {"nid": "b", "part_id": 2,
         "path": "包 > 接口管理 > 定位思路", "title": "定位思路", "content": "c2"},
    ]
    result = restore_files(slices)
    assert len(result.files) == 1                      # 只有「接口管理」一个文件
    f = result.files[0]
    assert f.file_path == "包 > 接口管理"
    assert f.file_title == "接口管理"
    assert f.heading_title == "告警 > 处理建议"          # 首切片标题=跨段合并串
    assert len(f.slices) == 2


def test_restore_whole_path_is_title_alpha():
    """path 整串即 title（两者均 A > B）→ 合并单段走 α：自身即文件."""
    slices = [{"nid": "x", "part_id": 1, "path": "A > B", "title": "A > B",
               "content": "c"}]
    result = restore_files(slices)
    assert len(result.files) == 1
    f = result.files[0]
    assert f.file_path == "A > B"                      # 合并后的完整标题串
    assert f.heading_title == "A > B"
    assert f.folder_path == ""


def test_calibration_single_segmentation_per_path():
    """同 path 不同 title（罕见）：part 序首个 title 决定，单一切法."""
    slices = [
        {"nid": "first", "part_id": 1,
         "path": "包 > 告警 > 处理建议", "title": "告警 > 处理建议", "content": "c"},
        {"nid": "second", "part_id": 2,
         "path": "包 > 告警 > 处理建议", "title": "处理建议", "content": "c"},
    ]
    result = restore_files(slices)
    assert len(result.files) == 1                      # 不允许两种切法分裂
    assert result.files[0].file_path == "包"
    assert result.files[0].heading_title == "告警 > 处理建议"


def test_tree_merges_title_gt_no_duplicate_keys():
    """章节树：跨段标题显示为单节点；全树 path 键无重复."""
    slices = [
        {"nid": "a", "part_id": 1,
         "path": "包 > 接口管理 > 告警 > 处理建议", "title": "告警 > 处理建议"},
        {"nid": "b", "part_id": 2,
         "path": "包 > 接口管理 > 定位思路", "title": "定位思路"},
    ]
    root = build_path_tree(slices)
    pkg = root.children["包"]
    node = pkg.children["接口管理"]
    assert "告警 > 处理建议" in node.children           # 单节点，title 含 " > "
    assert "告警" not in node.children                  # 不再有假层级
    assert node.children["告警 > 处理建议"].path == \
        "包 > 接口管理 > 告警 > 处理建议"                 # 键=原样串，勾选兼容

    paths: list[str] = []

    def _walk(nodes):
        for n in nodes:
            paths.append(n.path)
            _walk(n.children)

    _walk(root.children)
    assert len(paths) == len(set(paths))               # 无重复键
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/onenet/test_restore.py -q -k "calibrated or title_with_gt or whole_path or single_segmentation or duplicate_keys"`
Expected: FAIL（ImportError: split_path_calibrated / 断言失败）

- [ ] **Step 3: 实现 restore.py**

3a. 模块 docstring：β 规则清单后追加校准规则说明（见下方文本）；并把文末
「边界归并（更深层标题向上归并）留作 beta-3」的过期前向引用改为
「留作后续版本（beta-3 已用于 title 校准切分）」：

```
- beta-3：title 校准切分——``title`` 恒等于 ``path[-1]``（32 样例实证），
  标题自身含 ``>`` 时尾部多切出假段致文件错分；path 尾部各段与 title
  切段严格相等时合并为一个标题段，其余回退 raw 切分（53 号 §四）。
```

3b. `RULE_VERSION = "beta-2"` → `"beta-3"`

3c. 在 `split_path` 之后新增：

```python
def split_path_calibrated(path: str | None, title: str | None) -> list[str]:
    """path + title → 校准段列表（beta-3：标题跨段时尾部合并为一段）.

    仅当 title 切出多于一段且 path 尾部与 title 段严格相等（strip 后逐段
    比）才合并；普通数据与 raw 切分完全一致。合并段用 `` > `` 规范连接
    （与树节点路径的既有规范化一致）。
    """
    segs = split_path(path)
    tsegs = split_path(title)
    if (len(tsegs) > 1 and len(segs) >= len(tsegs)
            and segs[-len(tsegs):] == tsegs):
        return segs[:-len(tsegs)] + [" > ".join(tsegs)]
    return segs


def build_calibration(
    slices: Iterable[dict[str, Any]],
) -> dict[str, list[str]]:
    """path 字符串 → 校准段（同 path 单一切法，part_id 升序首个 title 决定）.

    同一 path 不同 title 的罕见数据取其一——否则建树会产生重复节点键
    （el-tree node-key 崩坏）且分组分裂（53 号 §四-2）。
    """
    calib: dict[str, list[str]] = {}
    for s in sorted(slices, key=lambda x: int(x.get("part_id") or 0)):
        key = str(s.get("path") or "")
        if key and key not in calib:
            calib[key] = split_path_calibrated(key, s.get("title"))
    return calib
```

3d. `restore_files` 的切片归组循环改为用校准段（替换 `segs = split_path(s.get("path"))`）：

```python
    calib = build_calibration(materialized)
    for s in sorted(materialized, key=lambda x: int(x.get("part_id") or 0)):
        key = str(s.get("path") or "")
        segs = calib.get(key) or split_path(key)
```

（其余 `len(segs) >= 2` 分支逻辑不变——校准段的 `segs[-1]` 即标题、
`" > ".join(segs[:-1])` 即文件；文件段内不会混入合并段，合并只发生在尾部。）

3e. `build_path_tree` 同样接线（函数体开头建 calib，循环内替换切分行）：

```python
def build_path_tree(slices: Iterable[dict[str, Any]]) -> TreeNode:
    """按校准段建树（beta-3；切片挂在其完整 path 的叶子节点）."""
    root = TreeNode(title="__ROOT__", path="", depth=0)
    calib = build_calibration(slices)
    for s in slices:
        key = str(s.get("path") or "")
        segs = calib.get(key) or split_path(key)
        if not segs:
            continue
        ...  # 循环体其余不变
```

3f. `__all__` 增补 `"build_calibration", "split_path_calibrated"`。

- [ ] **Step 4: 更新版本钉死断言**

`test_real_sample_grouping_pinned` 中：
`assert result.rule_version == RULE_VERSION == "beta-2"` → `"beta-3"`
（分组/目录数字断言全部不动——样例无含 `>` 标题，行为不变。）

- [ ] **Step 5: 跑 Task 1 全部测试**

Run: `python -m pytest knowledge_mining/tests/onenet/test_restore.py -q`
Expected: 全 PASS（15 旧 + 6 新）

- [ ] **Step 6: 提交**

```bash
git add knowledge_mining/mining/onenet/restore.py knowledge_mining/tests/onenet/test_restore.py
git commit -m "fix(kb): 一张网title校准切分beta-3——标题含>不再错分文件/假层级"
```

### Task 2: toc_scan 预览回归 + 适配器标题链

**Files:**
- Test: `knowledge_mining/tests/onenet/test_toc_scan.py`（增补跨段标题切片断言）
- Modify: `knowledge_mining/mining/parse_adapters/onenet_jsonl.py`
- Test: `knowledge_mining/tests/onenet/test_onenet_adapter.py`

- [ ] **Step 1: 先跑 toc_scan 现有测试（应全绿——预览与落库同源）**

Run: `python -m pytest knowledge_mining/tests/onenet/test_toc_scan.py -q`
Expected: PASS（restore_files 已校准，预览自动一致）

- [ ] **Step 2: 在 test_toc_scan.py 增补校准断言**

该文件无 pytest fixture——用其现有 `FakeScan` + `_client()` 模式（名以现文件实际为准）构造含下述两条切片的 source 后追加：

```python
def test_toc_files_preview_reflects_calibration():
    """跨段标题在预览 files/tree 中已合并（预览=落库同源，53 号 §五-1）."""
    rows = [
        {"path": "包 > 接口管理 > 告警 > 处理建议", "title": "告警 > 处理建议",
         "part_id": 1},
        {"path": "包 > 接口管理 > 定位思路", "title": "定位思路", "part_id": 2},
    ]
    client = _client(FakeScan(rows, total=2, part_max=2))   # 构造参数以现文件为准
    toc = scan_toc(client, "SRC1")
    assert toc["rule_version"] == "beta-3"
    assert toc["file_count"] == 1
    assert toc["files"][0]["file_path"] == "包 > 接口管理"
    assert toc["files"][0]["heading_title"] == "告警 > 处理建议"
    # 树上无假层级「告警」节点
    assert "告警" not in [c["title"] for c in toc["tree"][0]["children"][0]["children"]]
```

（FakeScan/_client 的真实签名以现有文件为准调整；断言意图不变。）

- [ ] **Step 3: 跑之，确认先失败后实现——本任务无实现（toc_scan 零改动），失败即说明 Task 1 接线有漏，回头修 restore.py**

Run: `python -m pytest knowledge_mining/tests/onenet/test_toc_scan.py -q`
Expected: PASS（若 FAIL，修 restore.py 直到绿）

- [ ] **Step 4: 适配器——先写失败测试**

在 `test_onenet_adapter.py` 追加（用其现有 `_jsonl()` 辅助与直调解析器惯用法——该文件无 `_parse`）：

```python
def test_heading_chain_merges_spanning_title():
    """标题含 > ：heading 链输出单层「告警 > 处理建议」，不产假层级."""
    rows = [
        {"nid": "a", "part_id": 1, "path": "包 > 接口管理 > 告警 > 处理建议",
         "title": "告警 > 处理建议", "content": "正文A"},
        {"nid": "b", "part_id": 2, "path": "包 > 接口管理 > 定位思路",
         "title": "定位思路", "content": "正文B"},
    ]
    art = OnenetJsonlParser().parse(_jsonl(rows), mime=ONENET_JSONL_MIME)
    headings = [b.text for b in art.blocks if b.block_type == "heading"]
    assert headings == ["包", "接口管理", "告警 > 处理建议", "定位思路"]


def test_fingerprint_version_bumped():
    from knowledge_mining.mining.parse_adapters.onenet_jsonl import (
        ONENET_JSONL_FINGERPRINT, ONENET_JSONL_VERSION,
    )
    assert ONENET_JSONL_VERSION == "1.1.1"
    assert ONENET_JSONL_FINGERPRINT.startswith("onenet_jsonl@1.1.1")
```

- [ ] **Step 5: 实现 onenet_jsonl.py**

5a. `ONENET_JSONL_VERSION = "1.1.0"` → `"1.1.1"`，版本注释改为：
`#: beta-3（1.1.1）：title 校准切分——跨段标题合并为单 heading，指纹变化触发新快照重挖。`

5b. import 增补：`from ... onenet.restore import build_calibration, clean_content, split_path`（路径 `knowledge_mining.mining.onenet.restore`）

5c. `parse()` 重构为两段：先收集 rows（坏行计数），再建 calib、逐行产 block：

```python
        rows: list[dict[str, Any]] = []
        bad_lines = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            if not isinstance(row, dict):
                bad_lines += 1
                continue
            rows.append(row)

        # beta-3：同 path 单一切法（行序=part 序，与后端 restore 一致）
        calib = build_calibration(rows)

        blocks: list[BackendBlock] = []
        warnings: list[str] = []
        last_heading_segs: list[str] = []
        for row in rows:
            nid = str(row.get("nid") or "")
            ...  # 现有循环体不变，仅切分行替换：
            segs = calib.get(str(row.get("path") or "")) or split_path(row.get("path"))
```

- [ ] **Step 6: 跑适配器测试**

Run: `python -m pytest knowledge_mining/tests/onenet/test_onenet_adapter.py knowledge_mining/tests/parse_adapters/test_registry.py -q`
Expected: 全 PASS

- [ ] **Step 7: 提交**

```bash
git add knowledge_mining/mining/parse_adapters/onenet_jsonl.py knowledge_mining/tests/onenet/test_onenet_adapter.py knowledge_mining/tests/onenet/test_toc_scan.py
git commit -m "fix(kb): onenet_jsonl标题链走校准切分1.1.1+toc预览校准断言"
```

## Chunk 2: 前端联动

### Task 3: onenetSelection.ts 纯函数 + 单测

**Files:**
- Modify: `kb-ui/src/utils/onenetSelection.ts`
- Test: `kb-ui/src/utils/__tests__/onenetSelection.spec.ts`

- [ ] **Step 1: 写失败测试**

在 `onenetSelection.spec.ts` 追加（顶部 import 增补新函数与类型）：

```ts
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
    file('包'),                                  // 包直属切片归……(根级 α)
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

  it('composite: descendant file + own direct slices together (审查建议)', () => {
    const out = filterFilesBySelection(
      files, ['包 > 接口管理'], nodesByPath, parentByPath)
    const paths = out.map((f) => f.file_path)
    expect(paths).toContain('包 > 接口管理 > 告警 > 处理建议')  // 规则1
    expect(paths).toContain('包')                               // 规则2
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd kb-ui && npm run test -- src/utils/__tests__/onenetSelection.spec.ts`
Expected: FAIL（新函数未导出）

- [ ] **Step 3: 实现 onenetSelection.ts（文件头注释后追加）**

```ts
import type { OnenetTocFile, OnenetTocNode } from '@/api/onenet'

/**
 * 53 号 §六：勾选联动过滤——与后端导入语义精确等价的两规则。
 * 规则1 段前缀：勾选子树 p 按段是 file_path 的前缀（含相等）→ 文件在勾选
 * 章节之下（方向与后端 Selection.matches 一致）。
 * 规则2 父文件：p 节点 direct_slice_count>0 → 直属切片归树结构父节点的文件
 * （父路径取树结构 Map，规避 ' > ' 字符串歧义）。
 * 空 selection = 整包全显。
 */
export function splitSegments(path: string): string[] {
  return path.split('>').map((p) => p.trim()).filter(Boolean)
}

function isPrefixSegments(subtree: string[], filePath: string): boolean {
  const segs = splitSegments(filePath)
  if (subtree.length > segs.length) return false
  return subtree.every((seg, i) => segs[i] === seg)
}

export function filterFilesBySelection(
  files: OnenetTocFile[],
  subtreePaths: string[],
  nodesByPath: Map<string, OnenetTocNode>,
  parentByPath: Map<string, string>,
): OnenetTocFile[] {
  if (!subtreePaths.length) return files
  const subtrees = subtreePaths.map(splitSegments)
  const shown = new Set<string>()
  for (const f of files) {
    if (subtrees.some((p) => isPrefixSegments(p, f.file_path))) shown.add(f.file_path)
  }
  for (const p of subtreePaths) {
    if ((nodesByPath.get(p)?.direct_slice_count ?? 0) > 0) {
      const parent = parentByPath.get(p)
      if (parent) shown.add(parent)
    }
  }
  return files.filter((f) => shown.has(f.file_path))
}

/** 树 → childPath → parentPath（父路径以树结构为准，不做字符串推导） */
export function buildParentPathMap(nodes: OnenetTocNode[]): Map<string, string> {
  const map = new Map<string, string>()
  const walk = (children: OnenetTocNode[], parentPath: string) => {
    for (const c of children) {
      map.set(c.path, parentPath)
      walk(c.children, c.path)
    }
  }
  walk(nodes, '')
  return map
}

/** 树 → path → node 索引（查 direct_slice_count 用） */
export function buildNodeIndex(
  nodes: OnenetTocNode[], map: Map<string, OnenetTocNode> = new Map(),
): Map<string, OnenetTocNode> {
  for (const n of nodes) {
    map.set(n.path, n)
    buildNodeIndex(n.children, map)
  }
  return map
}
```

- [ ] **Step 4: 跑测试**

Run: `cd kb-ui && npm run test -- src/utils/__tests__/onenetSelection.spec.ts`
Expected: 全 PASS（旧 4 + 新 7：splitSegments 1 + filterFilesBySelection 5 + build 1）

- [ ] **Step 5: 提交**

```bash
git add kb-ui/src/utils/onenetSelection.ts kb-ui/src/utils/__tests__/onenetSelection.spec.ts
git commit -m "feat(kb-ui): onenet勾选联动纯函数——段前缀+父文件两规则与导入语义等价"
```

### Task 4: OnenetAdminView.vue 联动 + 搜索框 + 首标题列

**Files:**
- Modify: `kb-ui/src/views/onenet/OnenetAdminView.vue`

- [ ] **Step 1: 模板改动（第三步卡片内）**

1a. 树容器上方加搜索框（`el-col :span="12"` 内、`.onenet-admin__treewrap` 之前）：

```vue
<el-input v-model="treeFilter" placeholder="按章节名过滤（保留命中祖先链）"
          size="small" clearable style="margin-bottom: 8px" />
```

1b. `el-tree` 增补属性与事件：`:filter-node-method="filterTreeNode"` 和 `@check="onTreeCheck"`。

1c. 右侧文件表 `el-table-column prop="file_title"` 之后插入：

```vue
<el-table-column prop="heading_title" label="首标题" min-width="130" show-overflow-tooltip />
```

1d. 右侧分页 `:total="toc.files?.length ?? 0"` → `:total="selectedFiles.length"`，外层 `v-if` 同步改 `selectedFiles.length > filesPageSize`。

1e. `.onenet-admin__actions`（第三步）确认按钮后加统计：

```vue
<span class="onenet-admin__hint">
  将导入 <b>{{ selectedFiles.length }}</b> 文件 /
  <b>{{ selectedSliceCount }}</b> 切片{{ checkedPaths.length ? '' : '（未勾选=整包）' }}
</span>
```

- [ ] **Step 2: 脚本改动**

2a. import 增补：`watch`（vue）、`buildNodeIndex, buildParentPathMap, filterFilesBySelection`（utils）、`OnenetTocNode`（type）。

2b. 新增状态与计算（放 `pagedFiles` 附近，替换其实现）：

```ts
// 53 号 §六：勾选联动（不勾=整包全显）；过滤规则与导入语义精确等价
const checkedPaths = ref<string[]>([])
const treeFilter = ref('')

const nodesByPath = computed(() => buildNodeIndex(toc.value?.tree ?? []))
const parentByPath = computed(() => buildParentPathMap(toc.value?.tree ?? []))
const selectedFiles = computed<OnenetTocFile[]>(() =>
  filterFilesBySelection(
    toc.value?.files ?? [], checkedPaths.value, nodesByPath.value, parentByPath.value))
const selectedSliceCount = computed(() =>
  selectedFiles.value.reduce((sum, f) => sum + f.slice_count, 0))
const pagedFiles = computed<OnenetTocFile[]>(() => {
  const start = (filesPage.value - 1) * filesPageSize.value
  return selectedFiles.value.slice(start, start + filesPageSize.value)
})

function onTreeCheck() {
  checkedPaths.value = checkedSubtreePaths()
  filesPage.value = 1
}

function filterTreeNode(value: string, data: OnenetTocNode): boolean {
  return !value || (data.title ?? '').includes(value)
}

watch(treeFilter, (v) => tocTreeRef.value?.filter(v))
```

2c. `loadToc` 成功分支重置：`filesPage.value = 1` 后加 `checkedPaths.value = []`；编辑模式 `setCheckedKeys` 之后（`await nextTick()` 后）补一行 `onTreeCheck()`（回显即联动，程序化设勾不触发 check 事件）。

- [ ] **Step 3: 类型与构建验证**

Run: `cd kb-ui && npm run build`
Expected: 构建成功零类型错误（铁律：vue-tsc -b 才是真检查）

- [ ] **Step 4: 视觉冒烟（Playwright browser 工具）**

启动前端 dev 或用现有部署预览不可行则跳过——最低要求：`npm run build` 产物成功。若本机 dev server 可起（`npm run dev`），用 Playwright 打开一张网管理页核对：搜索框过滤、勾选后右侧过滤与统计、首标题列。（无后端数据时以页面渲染不报错为底线。）

- [ ] **Step 5: 提交**

```bash
git add kb-ui/src/views/onenet/OnenetAdminView.vue
git commit -m "feat(kb-ui): onenet第三步勾选联动+统计+树搜索框+首标题列"
```

## Chunk 3: 回归、E2E 与审查

### Task 5: 全量回归 + 端到端验证

**Files:** 无新改动（只跑与修）

- [ ] **Step 1: onenet 全套后端测试**

Run: `python -m pytest knowledge_mining/tests/onenet/ -q`
Expected: 全 PASS；`test_fetch.py` Selection 用例零改动通过=存量兼容证明

- [ ] **Step 2: 前端全量**

Run: `cd kb-ui && npm run test && npm run build`
Expected: 全 PASS + 构建成功

- [ ] **Step 3: 链路级 E2E（本地模拟全链）**

写一次性脚本（不入库）串联：构造含跨段标题的切片集 → `scan_toc`（fake client）→ 拿 files/tree → `Selection(subtrees=[勾选节点])` → `fetch_selection`（fake client）→ `restore_files` → 断言：①导入文件与预览 files 一致 ②勾选「包 > 接口管理」时父文件「包」也在结果中 ③标题串完整保留 ④`OnenetJsonlParser().parse` 的 heading 链同样输出合并标题「告警 > 处理建议」（第三个消费方）。跑完删除脚本。

- [ ] **Step 4: 真库 E2E（如环境可用）**

Run: `python -m pytest knowledge_mining/tests/onenet/test_e2e_real_pg.py -m postgres -q`
若 PG 容器不可用则如实记录 SKIP 与原因（不伪装通过）。

- [ ] **Step 5: 受影响面快速回归（onnet 消费方）**

Run: `python -m pytest knowledge_mining/tests/onenet/test_import_service.py knowledge_mining/tests/onenet/test_resync.py knowledge_mining/tests/onenet/test_routes.py knowledge_mining/tests/parse_adapters/ -q`
Expected: 全 PASS（import_service/resync 走 restore_files 自动受益；Selection 未动）

### Task 6: 审查与收尾

- [ ] **Step 1: 派 code-reviewer 子代理审查全部 diff（对照 53 号规格）**
- [ ] **Step 2: 修复 CRITICAL/HIGH 发现并复测**
- [ ] **Step 3: `git log --oneline master..` 核对提交序列；工作区干净**
- [ ] **Step 4: 向用户汇报（测试结果、审查结论、遗留）**
