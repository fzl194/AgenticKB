"""Parser Adapter SDK contract (M2, SRS §C06) + Normalizer seam (§C07) +
Backend Registry subset (§C04).

This module freezes the seam between "one parse execution" and "any parse
backend" (legacy, Docling, cloud, ...):

```text
FrozenInput bytes (ObjectStoreSourceArtifactReader)
  -> DocumentParser.parse(text)        # backend-specific, third-party lib
  -> BackendParseArtifact              # candidate, NOT yet Parse IR
  -> ParseIRNormalizer.normalize()     # type mapping + stable ids + spans
  -> ParsedDocument (contracts.parse_ir)  # validated canonical IR
```

Design (ADR-0003 D-001/D-022 style):
- Pure stdlib. No third-party parser imports, no DB, no FastAPI. Concrete
  adapters live in ``mining/parse_adapters/`` and import THIS module, never
  the other way around.
- ``DocumentParser.parse`` is a synchronous pure function over decoded text:
  streaming bytes off MinIO is the Operator's job (SRS §4.6 "Adapter 将本次
  Run 冻结的 Storage Object 转换为第三方库输入"), so adapters stay trivially
  testable.
- ``BackendParseArtifact`` keeps the backend raw output (SRS §4.6: "保存
  backend 原始输出") so an adapter-mapping bug can be re-normalized later
  without re-running an expensive backend (SRS §9.5 replay row).
- The adapter CANNOT write business tables, choose segment policy, publish a
  Build, or silently call a fallback (SRS §4.6 "Adapter 不负责" list).

References: SRS §C04 (Backend Registry), §C06 (Parser Adapter SDK), §C07
(Parse IR Normalizer), §4.5-§4.7, §10.2; ADR-0003 D-001 (frozen dataclasses).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from knowledge_mining.mining.contracts.parse_ir.types import ParsedDocument


# ---------------------------------------------------------------------------
# Errors (SRS §C06 error normalization)
# ---------------------------------------------------------------------------


class ParserAdapterError(Exception):
    """Base class for adapter-level failures (normalized across backends).

    Raw third-party exceptions must not cross the adapter boundary; adapters
    wrap them with this family so Operators record stable codes (SRS §4.6
    "把后端 errors、warnings、usage 和版本返回给 Operator").
    """

    code = "parser_adapter_error"


class UnsupportedFormat(ParserAdapterError):
    """The backend cannot parse this MIME/format (SRS §C03 "unsupported").

    Operators treat this as a routing signal (try fallback), not a crash.
    """

    code = "unsupported_format"


# ---------------------------------------------------------------------------
# Backend output model (SRS §4.6 BackendParseArtifact)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackendBlock:
    """One flat block emitted by a backend, pre-normalization.

    Locator fields are per-format and independently optional (contract v1.1,
    ADR-0003 D-028 — "未知可缺，不得伪造", SRS §7.4):

    - Line-oriented backends (M2 MD/TXT) fill ``line_start``/``line_end``
      (0-based, end-exclusive) for line-addressable EvidenceSpans (§A01).
    - Structured backends (M3 PDF/Office/HTML) fill instead:
      * ``container_ref`` — owning native container, e.g.
        ``{"container_type": "page", "index": 3}`` (PDF),
        ``{"container_type": "sheet", "name": "Sheet1"}`` (XLSX),
        ``{"container_type": "slide", "index": 0}`` (PPTX).
      * ``bbox`` — on-page bounding box ``(x0, top, x1, bottom)`` in the
        container's coordinate system (PDF/PPTX shape).
      * ``native_ref`` — native structural locator, e.g. ``{"sheet": "S1",
        "cell": "A1"}`` (XLSX), ``{"paragraph_index": 12}`` (DOCX),
        ``{"xpath": "/html/body/div[2]/p[1]"}`` (HTML).

    ``block_type`` is backend vocabulary — the Normalizer owns the mapping to
    project element types (SRS §4.7).
    """

    block_type: str
    text: str
    line_start: int | None = None
    line_end: int | None = None
    level: int | None = None  # heading level / list depth when known
    container_ref: dict[str, Any] | None = None
    bbox: tuple[float, float, float, float] | None = None
    native_ref: dict[str, Any] | None = None
    structure: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendParseArtifact:
    """Candidate parse output from ONE backend run (SRS §4.6).

    ``raw_output`` preserves the backend's own representation (for legacy
    MD/TXT this is the decoded source text) for later replay through an
    upgraded Normalizer (SRS §9.5 "adapter mapping bug" row).
    """

    parser_id: str
    parser_version: str
    mime: str
    blocks: tuple[BackendBlock, ...]
    raw_output: str = ""
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Backend descriptor + registry (SRS §C04 subset for M2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParserDescriptor:
    """Registry entry for one parse backend (SRS §C04).

    M2 subset: identity, format coverage, deployment kind and license slot.
    Health tracking, cost and capability matrix arrive with WP6 (Router).
    """

    parser_id: str
    display_name: str
    version: str
    supported_mimes: frozenset[str]
    backend_kind: str = "local"  # "local" | "cloud"
    license_status: str = "ok"  # M2 placeholder; WP13 gates this
    parser_fingerprint: str = ""
    capabilities: frozenset[str] = frozenset()
    # M3 最小演进（SRS §C04 云端槽位）：占位 backend 的说明性元数据，
    # 例如用户将来配置云端模型的位置。带默认值，不破坏 M2 既有构造。
    note: str = ""

    def supports(self, mime: str) -> bool:
        """True if this backend claims ``mime`` (normalized, lower-case)."""
        return mime.lower() in self.supported_mimes


class BackendRegistry:
    """In-memory backend registry (SRS §C04 M2 subset).

    Selection is deterministic: first registered backend whose descriptor
    supports the MIME wins (M2 has at most one backend per format). Real
    rule-based routing with reason codes is WP6 / M3.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, ParserDescriptor] = {}

    def register(self, descriptor: ParserDescriptor) -> ParserDescriptor:
        if descriptor.parser_id in self._by_id:
            raise ValueError(f"parser already registered: {descriptor.parser_id!r}")
        self._by_id[descriptor.parser_id] = descriptor
        return descriptor

    def get(self, parser_id: str) -> ParserDescriptor | None:
        return self._by_id.get(parser_id)

    def select_for(self, mime: str) -> ParserDescriptor | None:
        """Deterministic primary-backend pick for ``mime`` (or None)."""
        for descriptor in self._by_id.values():
            if descriptor.supports(mime):
                return descriptor
        return None

    def all(self) -> tuple[ParserDescriptor, ...]:
        return tuple(self._by_id.values())


# ---------------------------------------------------------------------------
# Adapter Protocol (SRS §C06 / §4.6)
# ---------------------------------------------------------------------------


@runtime_checkable
class DocumentParser(Protocol):
    """One parse backend behind the project seam (SRS §C06).

    Implementations wrap a third-party library (or legacy in-repo logic) and
    return a :class:`BackendParseArtifact`. They must NOT write business
    tables, choose segment policy, publish Builds, or call fallbacks.
    """

    descriptor: ParserDescriptor

    def supports(self, mime: str) -> bool:
        """True if this backend can parse ``mime``."""
        ...

    def parse(self, data: bytes, *, mime: str) -> BackendParseArtifact:
        """Parse raw source ``data`` (bytes) into a candidate backend artifact.

        Synchronous and pure (no filesystem/network IO): the Operator is
        responsible for streaming bytes off the frozen Storage Object.
        Contract v1.1 (ADR-0003 D-028): the input is **bytes** so binary
        formats (PDF/DOCX/XLSX/PPTX) flow through the same seam; text-format
        adapters decode UTF-8 themselves and wrap decode failures in
        :class:`ParserAdapterError` (code ``invalid_encoding``).
        """
        ...


# ---------------------------------------------------------------------------
# Normalizer Protocol (SRS §C07 / §4.7)
# ---------------------------------------------------------------------------


@runtime_checkable
class ParseIRNormalizer(Protocol):
    """Convert a BackendParseArtifact into canonical Parse IR (SRS §C07).

    Owns: backend block-type mapping to project element types, container
    fabrication, stable element ids, line-addressable EvidenceSpans and
    deterministic relations. Runs schema + referential validation and raises
    on error-level issues (SRS §4.7 "normalization failure 不可进入质量门禁").
    """

    def normalize(
        self,
        artifact: BackendParseArtifact,
        *,
        source_raw_hash: str,
        parse_run_id: str | None = None,
    ) -> ParsedDocument:
        """Return a validated :class:`ParsedDocument` or raise."""
        ...


__all__ = [
    # errors
    "ParserAdapterError",
    "UnsupportedFormat",
    # backend output
    "BackendBlock",
    "BackendParseArtifact",
    # registry
    "ParserDescriptor",
    "BackendRegistry",
    # protocols
    "DocumentParser",
    "ParseIRNormalizer",
]
