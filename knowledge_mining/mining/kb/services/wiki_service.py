"""Wiki 制品服务层（63号批次2 Task 3）—— WikiLifecycle + WikiService 同文件。

WikiLifecycle：注入 ArtifactService 的 kind=wiki 生命周期协作者（Task 2 已用
Stub 钉死四方法契约）：发布事务（WikiDB 自带全部门禁）→ 事务外后置 units
投影 + embedding 异步拉起；rollback/discard 透传 wdb（expected_updated_at
透传做事务内 FOR UPDATE 权威比对）。WikiError → ArtifactError 族翻译（分支
调用方 routes/artifacts 只认 ArtifactError——不翻译会 500）。

WikiService：页面编辑编排。权限前置复用 ArtifactService._assert_role +
模块级 _check_freshness + WikiDB.ensure_draft（wiki 专有 checkout）；origin
恒 'manual'（63号 §3.2——machine 只能由批次3编译管线产生）；专页 frontmatter
严格校验（§4.3）；每次页写后全量重算链接表（Task 1 质量审查定稿：replace-by-
page 模式下 alias 归一与 broken 状态才能全局一致，页量 ≤200 成本可忽略）。

embedding 后置（§5.1 共享 llm_service 基建）：LlmTaskClient 是同步阻塞客户端
（httpx.Client + time.sleep 轮询），仅其调用下线程（asyncio.to_thread）——
DB 调用留在主 loop（psycopg AsyncConnectionPool 有 loop 亲和性）。失败语义：
终态失败 → mark_units_failed；超时/瞬时异常 → 保持 pending（下次发布/status
自愈重试）；整体异常只记日志，永不向上抛（投影失败不回滚发布）。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from knowledge_mining.mining.kb.db_wiki import (
    WikiError,
    compute_page_edges,
    normalize_page_key,
)
# 66号批次1 Task 1.4：目录形态校验纯函数（权威定义处，同 wiki_compile_pages
# 直连先例——单向下依赖：db_wiki_toc → db_wiki，与本模块无环）。
from knowledge_mining.mining.kb.db_wiki_toc import validate_toc
# 批次3决策7：发布前确定性校验。直连 wiki_compile_pages（校验函数权威定义
# 处——经 wiki_compile_service 转出多一层间接，审查 Minor；单向下依赖无环）。
from knowledge_mining.mining.kb.services.wiki_compile_pages import (
    validate_pages_for_publish,
)
from knowledge_mining.mining.kb.services.artifact_service import (
    ArtifactError,
    ArtifactNotFound,
    InvalidOperation,
    NoPendingChanges,
    StaleArtifact,
    UsageNoteRequired,
    ROLE_DESIGN,
    ROLE_RUNTIME_READ,
    _check_freshness,
    _expected_dt,
    _uid,
)
from knowledge_mining.mining.infra.llm_task_client import LlmTaskClient, LlmTaskError

logger = logging.getLogger(__name__)

#: 页面类型全集（63号 §4.3）。
PAGE_TYPES = ("topic", "contradiction", "open_question", "glossary")

#: serving ref 协议前缀（同 Java ArtifactWikiSearchService.REF_PREFIX="art_"
#: / routes/artifacts._REF_PREFIX，各自定义不 import——routes 反向 import 即环）。
#: ref = 前缀 + artifact_id：DB id 本身已含 art_（db_artifacts._new_artifact_id
#: = f"art_{hex}"），故 ref 真形态 art_art_<hex>；serving parseRef 单剥一个
#: 前缀还原 DB id——检索转发必须拼装，直传 DB id 会被剥成裸 hex → 404。
REF_PREFIX = "art_"

#: embedding 批大小 / 重试次数（对齐挖掘 infra/embedding.py：32 条/批 +
#: 3 次退避——llm_service worker 对一个任务一次 embed 整批 texts）。
_EMBED_BATCH = 32
_EMBED_MAX_ATTEMPTS = 3

#: 来源绑定 scope 输入上限（审查硬化：防脏输入撑爆指纹/取材 SQL 的 =ANY
#: 数组——与 kb_id 长度上限对称；超限 422 invalid_scope）。
_MAX_DIRECTORIES = 50
_MAX_DIRECTORY_CHARS = 500

#: Q01 回原文截读长度（66号批次5 Task 5.2）：引用气泡级预览，前 500 字符。
_UNIT_SNIPPET_CHARS = 500

#: (artifact_id, revision) 全局防重入——发布后置与 status 自愈共用同一集合
#: （status() 的 O(1) 查询面；清理只在 _embed_units 的 finally）。
_EMBED_INFLIGHT: set[tuple[str, int]] = set()

#: 后台 embed 任务强引用集（事件循环对 Task 只持弱引用，不持引用的任务可能
#: 中途被 GC——被 GC 的任务不会触发 done 回调、协程 finally 也不执行。照仓库
#: 既有模式：onenet/import_service._bg_tasks / purge_tasks._bg_tasks）。
_EMBED_TASKS: set["asyncio.Task[None]"] = set()

#: WikiError.code → ArtifactError 族（口径对齐 artifact_service._translate；
#: 未列出的 code 一律 InvalidOperation——路由 400）。
_WIKI_CODE_TO_ERROR: dict[str, type[ArtifactError]] = {
    "artifact_not_found": ArtifactNotFound,
    "stale_artifact": StaleArtifact,
    "usage_note_required": UsageNoteRequired,
    "no_pending_changes": NoPendingChanges,
}


def _translate_wiki(exc: WikiError) -> ArtifactError:
    """WikiError → ArtifactError：lifecycle 四方法被 ArtifactService 分支直调，
    其错误必须以 ArtifactError 族浮出（routes/artifacts._map_error 才能翻状态码）。"""
    mapped = _WIKI_CODE_TO_ERROR.get(exc.code)
    if mapped is not None:
        return mapped(exc.code)
    return InvalidOperation(exc.code)


def _extract_vectors(entry: dict[str, Any]) -> list[list[float]] | None:
    """succeeded 任务结果 → 向量列表（按 index 排序；结构残缺/空向量 → None
    ——调用方按终态失败处理，幂等键下重试只会拿回同一份缓存结果）。"""
    result = entry.get("result") or {}
    data = result.get("parsed_output") or {}
    items = data.get("data")
    if not isinstance(items, list):
        return None
    ordered = sorted(
        items,
        key=lambda x: x.get("index", 0) if isinstance(x, dict) else 0,
    )
    vectors: list[list[float]] = []
    for item in ordered:
        vector = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(vector, list) or not vector:
            return None
        vectors.append([float(v) for v in vector])
    return vectors


# ------------------------------------------------------------ 来源绑定纯函数

def _snapshot_signature(fingerprint: Any) -> set[tuple[str, int]] | None:
    """指纹 → (snapshot_id, unit_count) 集（顺序无关）；无 snapshots 键
    （从未编译，DDL 缺省 '{}'）→ None（dirty 判定恒脏哨兵）。"""
    snaps = None
    if isinstance(fingerprint, dict):
        snaps = fingerprint.get("snapshots")
    if not isinstance(snaps, list):
        return None
    signature: set[tuple[str, int]] = set()
    for snap in snaps:
        if not isinstance(snap, dict) or snap.get("snapshot_id") is None:
            continue
        try:
            count = int(snap.get("unit_count") or 0)
        except (TypeError, ValueError):
            count = 0
        signature.add((str(snap["snapshot_id"]), count))
    return signature


def _is_source_dirty(stored: Any, current: Any) -> bool:
    """脏标记（63号批次3）：stored 指纹从未编译（无 snapshots 键）→ True；
    否则与当前快照集（集合序无关）不一致 → True（「来源已变化，建议重新
    编译」横幅数据源）。"""
    stored_signature = _snapshot_signature(stored)
    if stored_signature is None:
        return True
    return stored_signature != _snapshot_signature(current)


# ------------------------------------------------------------ lifecycle

class WikiLifecycle:
    """kind=wiki 的生命周期协作者（deps 装配注入 ArtifactService._wiki）。

    wdb：WikiDB（页面/链接/units/生命周期四事务——touch/读行全在 wdb 事务
    内，无需 ArtifactDB 协作者）；task_client：LlmTaskClient 同步阻塞客户端
    （模块级单例注入）。
    """

    def __init__(self, wdb: Any, task_client: LlmTaskClient) -> None:
        self._wdb = wdb
        self._task_client = task_client

    # ------------------------------------------------------- 四方法契约

    async def publish(
        self, user: dict[str, Any], art_row: dict[str, Any], *,
        change_summary: str, expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """发布：diff 在事务前取（发布后 rev0 已清，六平键变全零）→ 批次3
        决策7 发布校验（rev0 全页确定性校验：blocking 阻断；warnings 随响应
        携带）→ wdb.publish 事务（门禁/GREATEST 版本号/pages+links 复制/清
        rev0/拨指针）→ 提交后 units 投影 + embedding 异步拉起（投影失败不
        回滚发布，只记日志）。返回 {revision, row_count=page_count, diff 三
        计数, warnings}（表格 publish 同形——其路径不经本协作者，不受校验
        影响）。"""
        artifact_id = str(art_row["artifact_id"])
        diff = await self._wdb.diff_draft_vs_current(artifact_id)
        # 决策7：发布前校验（在事务外——失败零副作用；InvalidOperation 带
        # 详情后缀由 routes/artifacts._map_error 特判 409）。
        blocking, warnings = await validate_pages_for_publish(
            self._wdb, artifact_id)
        if blocking:
            raise InvalidOperation(
                "publish_validation_failed:" + "; ".join(blocking[:10]))
        try:
            result = await self._wdb.publish(
                artifact_id, change_summary=change_summary,
                published_by=str(user["id"]),
                expected_updated_at=expected_updated_at,
            )
        except WikiError as exc:
            raise _translate_wiki(exc) from exc
        revision = int(result["revision"])
        try:
            await self._wdb.project_units(artifact_id, revision)
            self._spawn_embed(artifact_id, revision, str(art_row["domain_id"]))
        except Exception:
            logger.exception(
                "wiki 发布后置投影/embedding 拉起失败（不回滚发布）"
                " artifact=%s revision=%s", artifact_id, revision)
        return {
            "revision": revision,
            "row_count": int(result.get("page_count", 0)),
            "diff": {
                "added_count": diff["added_count"],
                "removed_count": diff["removed_count"],
                "changed_count": diff["changed_count"],
            },
            # toc 解析警示（66号批次1目录定版）并入既有校验 warnings 同构透传
            "warnings": warnings + list(result.get("warnings") or []),
        }

    async def rollback_to(
        self, user: dict[str, Any], art_row: dict[str, Any], revision: int,
        *, expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """纯指针拨回（数据不动）：权限/新鲜度由 ArtifactService 前置完成，
        expected_updated_at 透传做事务内 FOR UPDATE 权威比对。units 按版本
        保留——回滚后检索直接用目标版 units，无需重算向量。"""
        try:
            return await self._wdb.rollback_to(
                str(art_row["artifact_id"]), int(revision),
                expected_updated_at=expected_updated_at,
            )
        except WikiError as exc:
            raise _translate_wiki(exc) from exc

    async def discard(
        self, user: dict[str, Any], art_row: dict[str, Any], *,
        expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """丢弃 draft：DELETE rev0 pages+links + draft_open=FALSE（逃生门；
        已发布快照不动）。"""
        try:
            return await self._wdb.discard_draft(
                str(art_row["artifact_id"]),
                expected_updated_at=expected_updated_at,
            )
        except WikiError as exc:
            raise _translate_wiki(exc) from exc

    async def diff(self, artifact_id: str) -> dict[str, Any]:
        """rev0 vs current 六平键 diff（changed 元素为页面形态）。"""
        return await self._wdb.diff_draft_vs_current(str(artifact_id))

    # -------------------------------------------------- embedding 后置

    def _spawn_embed(
        self, artifact_id: str, revision: int, knowledge_domain: str,
    ) -> "asyncio.Task[None] | None":
        """后台拉起 embedding（fire-and-forget）：同 (artifact_id, revision)
        在飞时 no-op（幂等键另保证 llm_service 侧不重复计费）。返回 Task 便于
        测试等待。"""
        key = (str(artifact_id), int(revision))
        if key in _EMBED_INFLIGHT:
            return None
        _EMBED_INFLIGHT.add(key)
        task = asyncio.create_task(self._embed_units(key[0], key[1], knowledge_domain))
        # 强引用集防 GC：事件循环对 Task 只持弱引用，不持引用的任务可能中途
        # 被回收（届时 done 回调不会触发、协程 finally 也不执行——引用集是
        # 这条路径的唯一防线）。done 回调（完成/取消时触发）释放引用。
        # in-flight 键由 _embed_units 的 finally 清理（正常/异常/取消均会经过）。
        _EMBED_TASKS.add(task)
        task.add_done_callback(_EMBED_TASKS.discard)
        return task

    async def _embed_units(
        self, artifact_id: str, revision: int, knowledge_domain: str,
    ) -> None:
        """pending units → done：循环 32 条/批提交 llm_service 异步任务通道。
        DB 调用留在主 loop；仅 LlmTaskClient 同步阻塞调用下线程。永不向上抛。"""
        key = (artifact_id, revision)
        try:
            while True:
                units = await self._wdb.list_pending_units(
                    artifact_id, revision, limit=_EMBED_BATCH)
                if not units:
                    return
                texts = [str(u["content_text"]) for u in units]
                unit_keys = [str(u["unit_key"]) for u in units]
                # 幂等键含 artifact_id+revision+内容哈希：跨版本同 unit_key 不
                # 撞键（撞键会让 llm_service 命中旧任务拒绝复用→向量陈旧）；
                # 哈希口径照抄挖掘 infra/embedding.py 的 _batch_key。
                digest = hashlib.sha256(
                    "\x1f".join(texts).encode("utf-8")).hexdigest()[:24]
                idem = f"wiki-unit:v1:{artifact_id}:{revision}:{digest}"
                try:
                    task_id = await asyncio.to_thread(
                        self._task_client.submit_embedding, texts,
                        max_attempts=_EMBED_MAX_ATTEMPTS,
                        idempotency_key=idem,
                        knowledge_domain=knowledge_domain,
                    )
                except LlmTaskError as exc:
                    # 提交瞬时失败：保持 pending，下次发布/status 自愈重试。
                    # 幂等键是关联 llm_service 侧日志/任务的唯一手段，必带。
                    logger.warning(
                        "wiki units embedding 提交失败（保持 pending 待自愈）"
                        " artifact=%s revision=%s batch=%s idem=%s: %s",
                        artifact_id, revision, len(texts), idem, exc)
                    return
                entries = await asyncio.to_thread(
                    self._task_client.wait_for_tasks, [task_id])
                entry = entries.get(task_id) or {
                    "task_id": task_id, "status": "timeout",
                }
                status = str(entry.get("status"))
                if status == "succeeded":
                    vectors = _extract_vectors(entry)
                    if vectors is None or len(vectors) != len(units):
                        # 结构残缺/短返：幂等键下重试拿回同一结果 → 终态失败
                        logger.error(
                            "wiki embedding 任务结果不可用（mark failed）"
                            " artifact=%s revision=%s task=%s vectors=%s/%s",
                            artifact_id, revision, task_id,
                            "N" if vectors is None else len(vectors),
                            len(units))
                        await self._wdb.mark_units_failed(
                            artifact_id, revision, unit_keys)
                        continue
                    await self._wdb.update_unit_embeddings(
                        artifact_id, revision, unit_keys, vectors)
                    continue
                if status in ("failed", "dead_letter", "cancelled"):
                    await self._wdb.mark_units_failed(
                        artifact_id, revision, unit_keys)
                    continue
                # timeout：保持 pending（可重试态；failed 不自动重试的对照面）
                logger.warning(
                    "wiki embedding 任务等待超时（保持 pending 待自愈）"
                    " artifact=%s revision=%s task=%s units=%s",
                    artifact_id, revision, task_id, len(units))
                return
        except Exception:
            logger.exception(
                "wiki units embedding 后置异常（吞掉不上抛） artifact=%s"
                " revision=%s", artifact_id, revision)
        finally:
            _EMBED_INFLIGHT.discard(key)


# ------------------------------------------------------------ service

class WikiService:
    """wiki 页面编辑编排（六端点业务层）。权限复用 ArtifactService（反探测
    404 / viewer 不见设计态 403），乐观锁与 ensure_draft 用 wiki 专有链。"""

    def __init__(self, artifact_service: Any, wdb: Any,
                 lifecycle: WikiLifecycle, inputs_db: Any = None) -> None:
        self._artifacts = artifact_service
        self._wdb = wdb
        self._lifecycle = lifecycle
        # 批次3：编译取材域（sources/指纹/批次）——sources 三方法数据源。
        # 缺省 None 兼容性保留（对齐 ArtifactService(wiki=None) 先例——
        # 批次2 测试/表格-only 构造不受影响；sources 端点只在 deps 装配后可用）。
        self._inputs = inputs_db

    # ------------------------------------------------------ 前置/校验

    async def _design_session(
        self, user: dict[str, Any], artifact_id: str, expected_updated_at: Any,
    ) -> dict[str, Any]:
        """设计态编辑统一前置（对齐 artifact_service._require_design_session，
        ensure_draft 换 wiki 专有 checkout）：角色 → 乐观锁快检 → ensure_draft。"""
        artifact, _ = await self._artifacts._assert_role(
            user, artifact_id, *ROLE_DESIGN)
        _check_freshness(artifact, expected_updated_at)
        await self._wdb.ensure_draft(artifact_id)
        return artifact

    @staticmethod
    def _validate_frontmatter(page_type: str, frontmatter: dict[str, Any]) -> None:
        """专页 frontmatter 必填键校验（63号 §4.3）：contradiction 必含
        claim_a/claim_b/severity(∈low|medium|high)；open_question 必含
        question(以？或?结尾)/priority；glossary 必含 term；topic 无强制键。
        违规 → WikiError("invalid_frontmatter:字段名")。"""
        fm = frontmatter if isinstance(frontmatter, dict) else {}

        def _require(field: str) -> None:
            value = fm.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise WikiError(f"invalid_frontmatter:{field}")

        if page_type == "contradiction":
            _require("claim_a")
            _require("claim_b")
            if fm.get("severity") not in ("low", "medium", "high"):
                raise WikiError("invalid_frontmatter:severity")
        elif page_type == "open_question":
            question = fm.get("question")
            if not isinstance(question, str) or not question.strip() \
                    or not question.rstrip().endswith(("?", "？")):
                raise WikiError("invalid_frontmatter:question")
            _require("priority")
        elif page_type == "glossary":
            _require("term")
        # topic：无强制键

    async def _recompute_all_links(self, artifact_id: str) -> None:
        """全量重算 rev0 边表：get_page 拿全页（含 body_md/frontmatter——
        list_pages 是不含 body_md 的轻量清单）→ compute_page_edges（alias 归一
        + broken 全局一致）→ 逐页 replace_page_links。

        非事务的读-算-写：未持锁，并发写者的边可能交错（后写胜——边表是页内容
        的纯派生数据，下次任一页写自愈）。
        TODO：200 页上限下单次页写 O(N) 往返、N 页全编 O(N²)，内网 RTT 会放
        大；终态优化 = WikiDB 增 replace_links_bulk 单事务批量替换。"""
        listing = await self._wdb.list_pages(artifact_id, revision=0)
        pages: list[dict[str, Any]] = []
        for meta in listing:
            page = await self._wdb.get_page(
                artifact_id, 0, str(meta["page_key"]))
            if page is not None:
                pages.append(page)
        edges = compute_page_edges(pages)
        for page_key in sorted({str(p["page_key"]) for p in pages}):
            await self._wdb.replace_page_links(
                artifact_id, 0, page_key, edges)

    async def _design_revision(
        self, user: dict[str, Any], artifact_id: str,
        revision: int | None,
    ) -> int:
        """design 视图 revision（对齐表格 list_rows 口径）：显式 revision →
        历史读；draft 视图 → draft_open 时 rev0（设计态工作区，0 是合法读取
        目标），干净态投影 current（discard 语义：与已发布一致）。"""
        artifact, _ = await self._artifacts._assert_role(
            user, artifact_id, *ROLE_DESIGN)
        if revision is not None:
            return int(revision)
        if artifact.get("draft_open"):
            return 0
        return int(artifact.get("current_revision") or 0)

    async def _runtime_revision(
        self, user: dict[str, Any], artifact_id: str,
    ) -> int:
        """runtime 视图：current_revision（0=从未发布——rev0 对 runtime 不可见，
        运行态为空集）。"""
        artifact, _ = await self._artifacts._assert_role(
            user, artifact_id, *ROLE_RUNTIME_READ)
        return int(artifact.get("current_revision") or 0)

    async def resolve_view_revision(
        self, user: dict[str, Any], artifact_id: str, *,
        draft: bool = False, revision: int | None = None,
    ) -> int:
        """视图 revision 解析（66号 D03.1：pages/links 响应信封回填——图谱
        两请求混版判定）。与 list_pages/list_links 完全同口径单源复用：
        draft/显式 revision → design；缺省 → runtime。"""
        if draft or revision is not None:
            return await self._design_revision(user, artifact_id, revision)
        return await self._runtime_revision(user, artifact_id)

    # ------------------------------------------------------ 页面写

    async def upsert_page(
        self, user: dict[str, Any], artifact_id: str, *,
        page_key: str | None = None, title: str,
        page_type: str = "topic", frontmatter: dict[str, Any] | None = None,
        body_md: str = "", provenance: list[Any] | None = None,
        flags: dict[str, Any] | None = None, ordinal: int | None = None,
        expected_updated_at: Any = None, address_key: str | None = None,
    ) -> dict[str, Any]:
        """单页创建/更新（rev0 设计态工作区）。

        - page_key 缺省 = normalize_page_key(title)（非法 → invalid_page_key）；
          已存在同 key 页 → 更新。
        - 改名流（63号 §3.1：改名=新页+旧页删，服务不自动迁移——调用方自己删
          旧页）：PUT 路由的 path 键经 address_key 传入，显式 page_key 与其
          不同且新键已被其他页占用 → page_key_conflict。
        - origin 恒 'manual'（63号 §3.2——更新已有 machine 页同样翻 manual）。
        - provenance/flags/ordinal 更新时未传 → 保留库中现值；frontmatter 必
          传（None 视为 {}）；专页按 §4.3 严格校验。
        """
        await self._design_session(user, artifact_id, expected_updated_at)
        if not isinstance(title, str) or not title.strip():
            raise WikiError("invalid_title")
        if page_type not in PAGE_TYPES:
            raise WikiError("invalid_page_type")
        if frontmatter is None:
            frontmatter = {}
        if not isinstance(frontmatter, dict):
            raise WikiError("invalid_frontmatter")
        self._validate_frontmatter(page_type, frontmatter)

        if page_key is None:
            key = normalize_page_key(title)
        else:
            key = str(page_key)
            if not key:
                raise WikiError("invalid_page_key")
        if address_key is not None and key != str(address_key) \
                and await self._wdb.get_page(artifact_id, 0, key) is not None:
            raise WikiError("page_key_conflict")

        existing = await self._wdb.get_page(artifact_id, 0, key)
        if ordinal is None:
            ordinal = (int(existing["ordinal"]) if existing is not None
                       else await self._wdb.count_pages(artifact_id, 0) + 1)
        await self._wdb.upsert_page(
            artifact_id, page_key=key, title=title.strip(),
            page_type=page_type, frontmatter=frontmatter,
            body_md=body_md or "",
            provenance=(provenance if provenance is not None
                        else list((existing or {}).get("provenance") or [])),
            flags=(flags if flags is not None
                   else dict((existing or {}).get("flags") or {})),
            ordinal=ordinal,
            origin="manual",   # 63号 §3.2：人工写入口恒 manual
            updated_by=str(user["id"]),
            expected_updated_at=_expected_dt(expected_updated_at),
        )
        await self._recompute_all_links(artifact_id)
        page = await self._wdb.get_page(artifact_id, 0, key)
        if page is None:   # 防御：刚写完必在（并发极端路径被删）
            raise WikiError("page_not_found")
        return page

    async def delete_page(
        self, user: dict[str, Any], artifact_id: str, page_key: str, *,
        expected_updated_at: Any = None,
    ) -> None:
        """删页（rev0）：全量重算链接——指向它的边落 broken（63号 §3.1）；
        被删页自身出边先清空（不在 list_pages 里，replace([]) 兜底删除）。"""
        await self._design_session(user, artifact_id, expected_updated_at)
        hit = await self._wdb.delete_page(
            artifact_id, page_key,
            expected_updated_at=_expected_dt(expected_updated_at))
        if not hit:
            raise WikiError("page_not_found")
        await self._wdb.replace_page_links(artifact_id, 0, page_key, [])
        await self._recompute_all_links(artifact_id)

    # ------------------------------------------------------ 读路径

    async def list_pages(
        self, user: dict[str, Any], artifact_id: str, *, draft: bool = False,
        revision: int | None = None,
    ) -> list[dict[str, Any]]:
        """轻量清单（含 aliases 派生；不含 body_md）。未发布运行态=空集。"""
        if draft or revision is not None:
            rev = await self._design_revision(user, artifact_id, revision)
            return await self._wdb.list_pages(artifact_id, revision=rev)
        rev = await self._runtime_revision(user, artifact_id)
        if rev <= 0:
            return []
        return await self._wdb.list_pages(artifact_id, revision=rev)

    async def get_page(
        self, user: dict[str, Any], artifact_id: str, page_key: str, *,
        draft: bool = False, revision: int | None = None,
    ) -> dict[str, Any]:
        """单页全字段（含 body_md/frontmatter/provenance）。"""
        if draft or revision is not None:
            rev = await self._design_revision(user, artifact_id, revision)
            page = await self._wdb.get_page(artifact_id, rev, page_key)
        else:
            rev = await self._runtime_revision(user, artifact_id)
            page = (await self._wdb.get_page(artifact_id, rev, page_key)
                    if rev > 0 else None)
        if page is None:
            raise WikiError("page_not_found")
        return page

    async def list_links(
        self, user: dict[str, Any], artifact_id: str, *,
        page_key: str | None = None, direction: str = "out",
        draft: bool = False,
    ) -> list[dict[str, Any]]:
        """出边/入边（63号 §2.4 反向链接）：runtime 读 current；design+draft
        读 rev0。page_key 为明文（query 参数不经网关 path 校验）。"""
        if draft:
            rev = await self._design_revision(user, artifact_id, None)
            return await self._wdb.list_links(
                artifact_id, revision=rev, page_key=page_key,
                direction=direction)
        rev = await self._runtime_revision(user, artifact_id)
        if rev <= 0:
            return []
        return await self._wdb.list_links(
            artifact_id, revision=rev, page_key=page_key,
            direction=direction)

    # ------------------------------------------------------ 目录读写（66号批次1 Task 1.4）

    async def _snapshot_toc(self, artifact_id: str, revision: int) -> dict[str, Any]:
        """版本快照目录读（runtime/revision 共用）：kb_artifact_revisions.
        schema_json 的 toc 字段。行不存在 → revision_not_found 404；toc 字段
        null/畸形（目录定版前发布的旧版快照）→ sections=[] flat=true 回落
        （不伪装成有结构）。"""
        snap = await self._wdb.get_revision_snapshot(artifact_id, revision)
        if snap is None:
            raise WikiError("revision_not_found")
        toc = snap.get("toc") if isinstance(snap, dict) else None
        sections = toc.get("sections") if isinstance(toc, dict) else None
        if not isinstance(sections, list):
            sections = []
        return {"sections": sections, "flat": not sections,
                "revision": revision}

    async def toc(
        self, user: dict[str, Any], artifact_id: str, *, draft: bool = False,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """目录读三口径（draft/revision 路由对齐 pages 端点；**revision 优先**
        ——draft+revision 同传按 revision 处理，质量审查 M-1 统一口径）：

        - revision=N（ROLE_DESIGN，历史读）：读该版快照；不存在 → 404。
        - draft=true（ROLE_DESIGN）：读 **toc 行本身**（设计态编辑对象）+ 附
          pages_total（当前设计态视图页数，供前端显示未收录数）。无行 →
          {"sections":[],"flat":true}（空目录=平铺）。pages_total 的 revision
          照 _design_revision 口径：draft_open 时 rev0，干净态投影 current。
        - 缺省（ROLE_RUNTIME_READ）：读 current_revision 快照；未发布（rev0）
          → toc_not_found 404（反探测，口径同单页读——运行态查不到即 404）。
        """
        if draft or revision is not None:
            if revision is not None:
                # 返回值丢弃：调用仅为权限断言（design 角色）；快照存在性由
                # _snapshot_toc 判 revision_not_found。
                await self._design_revision(user, artifact_id, revision)
                return await self._snapshot_toc(artifact_id, int(revision))
            rev = await self._design_revision(user, artifact_id, None)
            row = await self._wdb.get_draft_toc(artifact_id)
            pages_total = await self._wdb.count_pages(artifact_id, rev)
            if row is None:
                return {"sections": [], "flat": True,
                        "pages_total": pages_total}
            sections = ((row.get("toc") or {}).get("sections")) or []
            return {
                "sections": sections,
                "flat": not sections,
                "pages_total": pages_total,
                "updated_at": row.get("updated_at"),
                "updated_by": row.get("updated_by"),
            }
        rev = await self._runtime_revision(user, artifact_id)
        if rev <= 0:
            raise WikiError("toc_not_found")
        return await self._snapshot_toc(artifact_id, rev)

    async def save_toc(
        self, user: dict[str, Any], artifact_id: str, toc: Any, *,
        expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """PUT 目录草稿行（整体替换）。

        乐观锁对象是 **toc 行自身** updated_at（页 PUT 比对 kb_artifacts.
        updated_at 的行级变体）：行已存在时 expected_updated_at 必传——缺令牌
        → stale_artifact 409（Task 1.2 审查 M-1 铁律：两 editor 并发改目录
        不静默 last-write-wins；信封同页 PUT 令牌失配先例）；行不存在（首次
        创建）免令牌（编译钩子/首次编辑场景）。**权威判定在 wdb 锁内**
        （require_token_if_exists=True，质量审查 I-1 消 TOCTOU：service 预检
        与 FOR UPDATE 非同事务，并发首写窗口内后到者不再静默覆盖先到者）；
        service 层保留同款快检（409 零副作用在 ensure_draft 检出之前，先例
        =_check_freshness 快检 + db 层权威的双层模式）。

        校验次序（畸形优先于 stale——对齐 db 层审查 I-2：校验失败不占行锁；
        且全部失败零副作用在 ensure_draft 之前）：①validate_toc 形态（422
        invalid_toc_shape:路径）②行存在缺令牌 409（快检）③ensure_draft 检出
        （页键校验对象是 rev0 草稿页）④页键存在性——body 全部 page_keys
        必须存在于 rev0，缺 → 422 toc_unknown_page_keys:键名清单（审查 M-4
        消解：写入面即拒，发布 dropped 警示不再承担逗号歧义）⑤wdb.
        upsert_draft_toc（内部重复形态校验无害 + 锁内权威判定）。返回完整
        toc 行（updated_at=下次编辑的新令牌）。
        """
        await self._artifacts._assert_role(user, artifact_id, *ROLE_DESIGN)
        try:
            normalized = validate_toc(toc)
        except ValueError as exc:
            raise WikiError(f"invalid_toc_shape:{exc}") from None
        if await self._wdb.get_draft_toc(artifact_id) is not None \
                and expected_updated_at is None:
            raise WikiError("stale_artifact")
        await self._wdb.ensure_draft(artifact_id)
        listing = await self._wdb.list_pages(artifact_id, revision=0)
        known = {str(meta["page_key"]) for meta in listing}
        wanted = {key for sec in normalized["sections"]
                  for key in sec["page_keys"]}
        missing = sorted(wanted - known)
        if missing:
            raise WikiError("toc_unknown_page_keys:" + ",".join(missing))
        return await self._wdb.upsert_draft_toc(
            artifact_id, toc, updated_by=str(user["id"]),
            expected_updated_at=_expected_dt(expected_updated_at),
            require_token_if_exists=True)

    async def status(self, user: dict[str, Any], artifact_id: str) -> dict[str, Any]:
        """units 进度（运行态信息）：{revision, units 四计数}。重启自愈——
        pending>0 且 (artifact_id,current_rev) 不在 in-flight → 顺手拉起
        embedding（幂等键保证 llm_service 侧不重复计费）。"""
        artifact, _ = await self._artifacts._assert_role(
            user, artifact_id, *ROLE_RUNTIME_READ)
        rev = int(artifact.get("current_revision") or 0)
        if rev <= 0:
            return {"revision": 0,
                    "units": {"total": 0, "done": 0, "failed": 0, "pending": 0}}
        units = await self._wdb.units_status(artifact_id, rev)
        if units.get("pending", 0) > 0 and (artifact_id, rev) not in _EMBED_INFLIGHT:
            self._lifecycle._spawn_embed(
                artifact_id, rev, str(artifact["domain_id"]))
        return {"revision": rev, "units": units}

    # ------------------------------------------- Q01 投影修复（66号批次5 Task 5.3）

    async def reproject(
        self, user: dict[str, Any], artifact_id: str,
    ) -> dict[str, Any]:
        """检索投影修复（editor+）：对 current_revision 幂等重入
        wdb.project_units——**修复仅重建既定版本的检索投影，不悄悄改正文、
        不产生伪发布版本**（66号 §14；project_units 只写
        kb_wiki_page_units，不触 pages/revisions/制品行）。

        重入条件（健康 no-op 语义，幂等键二保证）：failed>0（终态失败不自动
        重试）或 total==0（发布后置投影失败只记日志的存量面）→ 重入投影 +
        embedding 重拉起（failed 随 delete+insert 落回 pending 待重嵌）；健康
        /在飞（pending>0 且无 failed）→ reprojected=False 不动投影（不把已
        done 的 units 无谓重置）。未发布（rev0）→ 404 revision_not_found。
        """
        artifact, _ = await self._artifacts._assert_role(
            user, artifact_id, *ROLE_DESIGN)
        rev = int(artifact.get("current_revision") or 0)
        if rev <= 0:
            raise WikiError("revision_not_found")
        units = await self._wdb.units_status(artifact_id, rev)
        if units.get("total", 0) > 0 and units.get("failed", 0) <= 0:
            return {"artifact_id": artifact_id, "revision": rev,
                    "reprojected": False}
        # 运维审计（5.3 双审 Minor-1）：谁在何时触发了重建（含触发面计数）——
        # 重建幂等无害，但频繁触发常意味着投影持续落地失败，需追因。
        logger.warning(
            "wiki 检索投影重建: artifact=%s revision=%s by=%s "
            "(units total=%s failed=%s)",
            artifact_id, rev, user.get("username") or _uid(user),
            units.get("total", 0), units.get("failed", 0))
        await self._wdb.project_units(artifact_id, rev)
        self._lifecycle._spawn_embed(
            artifact_id, rev, str(artifact["domain_id"]))
        return {"artifact_id": artifact_id, "revision": rev,
                "reprojected": True}

    # ------------------------------------------- 运行态检索转发（63号批次4 Task 4）

    async def search(
        self, *, user: dict[str, Any], artifact_id: str, q: str,
        mode: str = "fusion", top_k: int = 10, forward: Any = None,
    ) -> dict[str, Any]:
        """web 阅读器搜索框转发编排：ROLE_RUNTIME_READ 前置（viewer 可搜；
        非成员 404 反探测在 mining 侧即拦）→ serving internal 三通道检索
        （dense ANN/keyword/fusion，业务整段在 Java 侧）响应原样透传。

        domain 取 _assert_role 返回的制品行 domain_id（零额外查询）；
        username 取会话用户名（serving 复核成员谓词——key_id 由转发客户端
        定死 None）；is_admin 按 _assert_role 旁路口径透传（返回角色
        admin/domain_admin → True）：站点管理员无域绑定/非制品成员时
        serving 成员谓词会误 404（批次6 冒烟实测缺陷），对齐 serving
        selectVisibleArtifact 的 admin 旁路；domain_admin 仅在其已通过
        mining 侧域管理员判定的制品域上放行（payload domain=制品自身域，
        不越权）。不做缓存（MVP）。
        """
        if not isinstance(q, str) or not q.strip():
            # 与 serving isBlank 校验同口径提前拦（省一次网络往返）→ 422
            raise WikiError("invalid_query")
        artifact, role = await self._artifacts._assert_role(
            user, artifact_id, *ROLE_RUNTIME_READ)
        if forward is None:
            # 防御：deps 装配恒注入转发客户端；旧式直造 WikiService 的调用方
            # 不触达本方法——真到此分支按 serving 不可用口径，不裸抛异常
            raise WikiError("serving_unavailable")
        return await forward.search(
            # ref 拼装见模块头 REF_PREFIX 注释（art_art_<hex>）——直传 DB id
            # 会被 serving parseRef 剥成裸 hex → artifact_not_found 404。
            artifact_ref=REF_PREFIX + artifact_id, q=q, mode=mode,
            top_k=top_k,
            domain=str(artifact["domain_id"]),
            username=str(user.get("username") or _uid(user)),
            is_admin=role in ("admin", "domain_admin"),
        )

    # ------------------------------------------- Q01 回原文（66号批次5 Task 5.2）

    async def unit_source(
        self, user: dict[str, Any], artifact_id: str, unit_id: str, *,
        draft: bool = False, revision: int | None = None,
    ) -> dict[str, Any]:
        """unit_id（=retrieval units v2 的 representation_id，provenance
        unit_ids 元素）→ 来源单元原文截读（前 500 字）。视图 revision 口径
        对齐单页读：draft/显式 revision → design 角色（rev0 草稿或历史版）；
        缺省 → runtime（current_revision；未发布=空集直接 404）。

        双重权限（66号 §2「来源回查重新校验原文读取权限；能读制品不等于
        能读整个来源知识库」）：制品读取角色前置（viewer 可读运行态）→
        **源 kb 可读二次校验**（kb_readable=KbDB.is_visible 谓词唯一真相
        源，制品 public ≠ 来源 kb 可读，两者独立判定）。失败面（非本制品
        provenance / 原文退役 / 源库无读权）一律 unit_not_found 404 反探测
        单码——不区分暴露存在性。"""
        if draft or revision is not None:
            rev = await self._design_revision(user, artifact_id, revision)
            revs = [int(rev)]
        else:
            rev = await self._runtime_revision(user, artifact_id)
            revs = [int(rev)] if rev > 0 else []
        row = (await self._inputs.unit_source(artifact_id, unit_id, revs)
               if revs else None)
        if row is None:
            raise WikiError("unit_not_found")
        if not await self._inputs.kb_readable(
                str(row["kb_id"]), str(user["id"])):
            raise WikiError("unit_not_found")
        return {
            "unit_id": unit_id,
            "content_text": str(row.get("content_text") or "")[:_UNIT_SNIPPET_CHARS],
            "kb_id": str(row["kb_id"]),
            "snapshot_id": str(row["snapshot_id"]),
            "section_ref": row.get("section_ref"),
        }

    # ------------------------------------------------------ 来源绑定（批次3）

    async def list_sources(
        self, user: dict[str, Any], artifact_id: str,
    ) -> list[dict[str, Any]]:
        """绑定列表（ROLE_RUNTIME_READ——批次3 MVP 口径：制品成员即协作者，
        看得到绑定行）。每行附：
        - dirty：**绑定范围内**编译语料是否变化——行自身 scope.directories
          传给 current_fingerprint（scope 感知口径）与 fingerprint 比对
          （_is_source_dirty）；不可读行 dirty=None（审查裁定：既省掉指纹
          重查询也不向失去库权限的成员泄露库内容变化信号，前端显示"未知"）；
        - kb_title/unreadable：kb 可读判定（KbDB.is_visible 谓词）通过才给
          标题，否则 kb_title=None + unreadable=True（绑定行仍可见，只隐库
          元数据——绑定后失去读权的行不整行消失）。"""
        await self._artifacts._assert_role(user, artifact_id, *ROLE_RUNTIME_READ)
        rows = await self._inputs.list_sources(artifact_id)
        kb_ids = sorted({str(r["kb_id"]) for r in rows})
        titles = await self._inputs.kb_titles(kb_ids)
        out: list[dict[str, Any]] = []
        for row in rows:
            kb_id = str(row["kb_id"])
            readable = await self._inputs.kb_readable(kb_id, str(user["id"]))
            if readable:
                scope = row.get("scope") or {}
                directories = [str(d) for d in (scope.get("directories") or [])]
                current = await self._inputs.current_fingerprint(
                    kb_id, directories)
                dirty: bool | None = _is_source_dirty(
                    row.get("fingerprint"), current)
                kb_title = titles.get(kb_id)
            else:
                dirty = None
                kb_title = None
            out.append({
                "kb_id": kb_id,
                "scope": row.get("scope") or {},
                "fingerprint": row.get("fingerprint") or {},
                "status": row.get("status"),
                "created_by": row.get("created_by"),
                "created_at": row.get("created_at"),
                "dirty": dirty,
                "kb_title": kb_title,
                "unreadable": not readable,
            })
        return out

    async def upsert_source(
        self, user: dict[str, Any], artifact_id: str, kb_id: str,
        directories: list[str], *, expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """绑定/更新来源（ROLE_DESIGN + 新鲜度前置；不走 ensure_draft——绑定
        不是页写，不该触发 current→rev0 检出）：scope={"directories": [...]}
        落库（空数组=全库，批次3 决策6）。kb 可读校验（KbDB.is_visible），
        不可读 → ArtifactNotFound（404 反探测——防探测绑定他人私有库）。
        权威乐观锁比对 + touch 同事务在 CompileInputsDB.upsert_source（绑定
        变更须失效编辑器令牌）。返回绑定行。"""
        artifact, _ = await self._artifacts._assert_role(
            user, artifact_id, *ROLE_DESIGN)
        _check_freshness(artifact, expected_updated_at)
        # scope 契约（决策6）：directories 必须是字符串数组且无空串（纯空白
        # 视同空串拒收；元素 strip 归一后落库）；量上限防脏输入撑爆指纹/
        # 取材 SQL 的 =ANY 数组（审查硬化：与 kb_id 长度上限对称）
        if not isinstance(directories, list) or not all(
                isinstance(d, str) and d.strip() for d in directories):
            raise WikiError("invalid_scope")
        if len(directories) > _MAX_DIRECTORIES or any(
                len(d) > _MAX_DIRECTORY_CHARS for d in directories):
            raise WikiError("invalid_scope")
        kb_id = str(kb_id).strip()
        if not kb_id:
            raise WikiError("invalid_scope")
        if not await self._inputs.kb_readable(kb_id, str(user["id"])):
            raise ArtifactNotFound(kb_id)
        return await self._inputs.upsert_source(
            artifact_id, kb_id,
            scope={"directories": [d.strip() for d in directories]},
            created_by=str(user["id"]),
            expected_updated_at=_expected_dt(expected_updated_at),
        )

    async def delete_source(
        self, user: dict[str, Any], artifact_id: str, kb_id: str, *,
        expected_updated_at: Any = None,
    ) -> None:
        """解绑（软删——绑定历史/指纹保留供复活；ROLE_DESIGN + 新鲜度同
        upsert 前置）。无 active 绑定 → source_not_found。"""
        artifact, _ = await self._artifacts._assert_role(
            user, artifact_id, *ROLE_DESIGN)
        _check_freshness(artifact, expected_updated_at)
        hit = await self._inputs.delete_source(
            artifact_id, str(kb_id),
            expected_updated_at=_expected_dt(expected_updated_at))
        if not hit:
            raise WikiError("source_not_found")
