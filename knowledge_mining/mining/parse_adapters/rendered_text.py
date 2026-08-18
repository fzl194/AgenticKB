"""表格 rendered text 的跨格式唯一定义（整改轮不变量 I-2 / I-3）.

SRS §7.6 / D-011：TableAsset 是表格事实源；Element.text 只是**确定性
rendered view**。本模块是该 view 的唯一实现——所有格式的表格元素文本
都必须由这里渲染，保证：

- 可检索：表头与数据单元格文本全部出现在 Element.text 中；
- 确定性：同一 TableAsset 永远渲染出同一字符串（无 dict/set 迭代序）；
- 可重算：``render_table_text(asset) == element.text`` 可被 contract
  test 断言（事实源 -> 视图方向单一）。

序列化格式：行以 ``\n`` 连接、列以 ``\t`` 分隔、只取原点 cell（被合并
覆盖位置无独立文本）、跳过空行外的空白规整。选择 tab 而非 pipe 的原因：
不与 cell 文本中的 ``|`` 冲突，且与既有 DOCX/PDF 的 ``\t`` 约定一致。
"""
from __future__ import annotations

from knowledge_mining.mining.contracts.parse_ir import TableAsset


def render_table_text(asset: TableAsset) -> str:
    """TableAsset -> 确定性 rendered view 文本（行 \n、列 \t）."""
    grid: list[list[str]] = [
        ["" for _ in range(max(asset.columns, 0))]
        for _ in range(max(asset.rows, 0))
    ]
    for cell in sorted(asset.cells, key=lambda c: (c.row_index, c.column_index)):
        if 0 <= cell.row_index < len(grid) and 0 <= cell.column_index < len(
            grid[cell.row_index]
        ):
            grid[cell.row_index][cell.column_index] = cell.text
    lines = ["\t".join(row) for row in grid]
    # 去除首尾空行（中间空行保留——它们是网格事实的一部分）
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


__all__ = ["render_table_text"]
