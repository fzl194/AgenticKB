"""A2 稳定章节身份（34 号 P0-2；39 号 §2.1）.

section 节点身份从「标题路径」迁移为「序号路径」：

- ref = ``{doc}#section:{o1}/{o2}/…``（o 为各级同级序号，0 基）；
- 同名兄弟章节、重开章节（同名标题二次出现）得到独立身份；
- ``ordinal`` 是同级顺序（prev/next 导航的数据基础）；
- ``element_id`` 仅绑定 IR 中已确认的 heading 元素：在 opener 的
  element_ids 中按 IR 标题层级选择唯一元素；缺 IR、缺层级或同层有多个
  候选时留空。父子标题合并进一个切片也保持各自的元素身份。
  IR 只补锚，不改变序号 ref 或逐 segment 归属。

身份算法镜像编译器的标题栈：按阅读序遍历切片，链与当前栈的最长公共
前缀之后的部分即新开章节。纯函数、确定性、只依赖 CompiledSegment。

绑定模型（Codex 审查 P1-3 修复）：章节归属按**阅读顺序**逐 segment
记录——``refs_by_segment`` 在构建期写入（遍历到该 segment 时的栈顶
身份），而非事后按标题链查最终映射。重开章节（A → B → A）时早期
段落绑第一个 A、后期段落绑第二个 A；检索单元 / structure 节点 /
section 表示三方投影面同源消费该映射。``ref_of``（标题链 → 最新身份）
已删除——它是错绑缺陷的根因，不允许再有调用方。

已知边界（39 号 §2.1，编译器层信息丢失）：连续同级同名标题在
CompiledSegment 层链元组完全相同（编译器标题栈折叠），投影层无
区分依据，合并为同一身份——修它需动 compiler 指纹（本轮明确不做）。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment
from knowledge_mining.mining.contracts.parse_ir.types import Element

PathEntry = tuple[int, str]
Path = tuple[PathEntry, ...]

_DOC_NODE_SUFFIX = "#document"


@dataclass(frozen=True)
class SectionIdentity:
    """一个章节的稳定身份（投影期确定，nodes/units/导航共用）."""

    path: Path
    ordinal_path: str
    ref: str
    parent_ref: str
    level: int
    title: str
    ordinal: int
    element_id: str | None


@dataclass(frozen=True)
class SectionIdentityIndex:
    """身份索引：全量身份（创建序）+ 逐 segment 阅读序绑定.

    - ``identities``：按创建序（阅读序）的全部身份；
    - ``refs_by_segment``：segment_index → 遍历该 segment 时的栈顶
      section ref。无标题链的 segment 不在映射中（bound_ref 返回 None）。
    """

    identities: tuple[SectionIdentity, ...]
    refs_by_segment: dict[int, str] = field(default_factory=dict)

    def bound_ref(self, segment_index: int) -> str | None:
        """segment_index → 所属 section 的 ref（阅读序绑定；无标题 → None）."""
        return self.refs_by_segment.get(segment_index)


def build_section_identities(
    segments: Iterable[CompiledSegment], *, document_ref: str,
    ir_elements: Iterable[Element] = (),
) -> SectionIdentityIndex:
    """按阅读序推导章节身份与逐 segment 绑定（结构/检索投影共用）."""
    heading_levels = {
        element.element_id: element.style["level"]
        for element in ir_elements
        if element.element_type == "heading"
        and isinstance(element.style.get("level"), int)
        and not isinstance(element.style["level"], bool)
        and element.style["level"] > 0
    }
    doc_node_ref = f"{document_ref}{_DOC_NODE_SUFFIX}"
    identities: list[SectionIdentity] = []
    # 每 parent ref 的下一个同级序号（跨弹出持久——重开章节继续递增）
    next_ordinal: dict[str, int] = {}
    # parent path → 该 path 当前（最近创建的）身份——父子 ref 接线的
    # 工作映射；segment 绑定绝不查它（查 refs_by_segment）
    identity_by_path: dict[Path, SectionIdentity] = {}
    # 当前活跃身份栈（镜像编译器标题栈）
    stack: list[tuple[Path, SectionIdentity]] = []
    refs_by_segment: dict[int, str] = {}

    def _create(path: Path, opener: CompiledSegment) -> SectionIdentity:
        parent_path: Path = path[:-1]
        parent_identity = identity_by_path.get(parent_path)
        parent_ref = parent_identity.ref if parent_identity else doc_node_ref
        # 同级序号按父 ref（唯一身份）计数——重开章节是全新父，子序号从 0 起
        ordinal = next_ordinal.get(parent_ref, 0)
        next_ordinal[parent_ref] = ordinal + 1
        parent_ordinal_path = (
            parent_identity.ordinal_path if parent_identity else None
        )
        ordinal_path = (
            f"{parent_ordinal_path}/{ordinal}"
            if parent_ordinal_path is not None
            else str(ordinal)
        )
        level, title = path[-1]
        candidates = {
            element_id for element_id in opener.element_ids
            if heading_levels.get(element_id) == level
        }
        element_id = next(iter(candidates)) if len(candidates) == 1 else None
        return SectionIdentity(
            path=path,
            ordinal_path=ordinal_path,
            ref=f"{document_ref}#section:{ordinal_path}",
            parent_ref=parent_ref,
            level=level,
            title=title,
            ordinal=ordinal,
            element_id=element_id,
        )

    for segment in segments:
        chain: Path = tuple(segment.heading_chain)
        # 与当前栈的最长公共前缀（栈位置 i 的身份路径末项即该层条目）
        common = 0
        for (stack_path, _identity), chain_entry in zip(stack, chain):
            if stack_path[-1] != chain_entry:
                break
            common += 1
        # 公共前缀之外即（重）开章节——重开路径拿到全新序号身份
        for depth in range(common + 1, len(chain) + 1):
            path = chain[:depth]
            identity = _create(path, segment)
            identities.append(identity)
            identity_by_path[path] = identity
        if chain:
            stack = [
                (chain[: d + 1], identity_by_path[chain[: d + 1]])
                for d in range(len(chain))
            ]
            # segment 绑定 = 遍历到此处的栈顶身份（重开后的最新身份——
            # 正确：该 segment 的标题链就是刚（重）开的这条链）
            refs_by_segment[segment.segment_index] = stack[-1][1].ref
        else:
            stack = []

    return SectionIdentityIndex(
        identities=tuple(identities), refs_by_segment=refs_by_segment,
    )


__all__ = ["Path", "SectionIdentity", "SectionIdentityIndex",
           "build_section_identities"]
