"""A2 稳定章节身份（34 号 P0-2；39 号 §2.1）.

section 节点身份从「标题路径」迁移为「序号路径」：

- ref = ``{doc}#section:{o1}/{o2}/…``（o 为各级同级序号，0 基）；
- 同名兄弟章节、重开章节（同名标题二次出现）得到独立身份，不再按
  标题路径折叠（旧实现的 ``seen_sections`` 缺陷）；
- ``ordinal`` 是同级顺序（prev/next 导航的数据基础）；
- ``element_id`` 取 opener 切片（首个引入该章节的切片）的首个元素——
  纯标题切片即标题元素本身；标题并入首段时编译器把标题元素 id 放在
  首位。这是**结构化锚**（与大纲元素 id 同源），不是标题文本匹配。
  切片无元素时为 None（有缺口的降级，进质量报告口径）。

身份算法镜像编译器的标题栈：按阅读序遍历切片，链与当前栈的最长公共
前缀之后的部分即新开章节。纯函数、确定性、只依赖 CompiledSegment。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from knowledge_mining.mining.contracts.segment_compiler import CompiledSegment

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
    """身份索引：全量身份（创建序）+ 链 → 身份的最新映射（重开取后者）."""

    identities: tuple[SectionIdentity, ...]
    by_path: dict[Path, SectionIdentity]

    def ref_of(self, chain: Sequence[tuple[int, str]] | Path) -> str | None:
        """完整标题链 → 所属 section 的 ref；空链/未知链返回 None."""
        key = tuple(chain)
        identity = self.by_path.get(key)
        return identity.ref if identity is not None else None


def build_section_identities(
    segments: Iterable[CompiledSegment], *, document_ref: str
) -> SectionIdentityIndex:
    """按阅读序推导章节身份（结构投影与检索投影共用，保证 ref 一致）."""
    doc_node_ref = f"{document_ref}{_DOC_NODE_SUFFIX}"
    identities: list[SectionIdentity] = []
    # 每 parent path 的下一个同级序号（跨弹出持久——重开章节继续递增）
    next_ordinal: dict[Path, int] = {}
    by_path: dict[Path, SectionIdentity] = {}
    # 当前活跃身份栈（镜像编译器标题栈）
    stack: list[tuple[Path, SectionIdentity]] = []

    def _create(path: Path, opener: CompiledSegment) -> SectionIdentity:
        parent_path: Path = path[:-1]
        parent_identity = by_path.get(parent_path)
        parent_ref = parent_identity.ref if parent_identity else doc_node_ref
        # 同级序号按父身份（唯一 ref）计数——重开章节是全新父，子序号从 0 起
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
        element_id = opener.element_ids[0] if opener.element_ids else None
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
            by_path[path] = identity
        if chain:
            stack = [
                (chain[: d + 1], by_path[chain[: d + 1]])
                for d in range(len(chain))
            ]
        else:
            stack = []

    return SectionIdentityIndex(
        identities=tuple(identities), by_path=dict(by_path)
    )


__all__ = ["Path", "SectionIdentity", "SectionIdentityIndex",
           "build_section_identities"]
