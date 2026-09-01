"""KB management — async repository for kb_users / knowledge_bases / kb_members.

Used by the async FastAPI routes in mining/kb/routes/. Mirrors the codebase pattern:
raw parameterized SQL over the shared async pool (like knowledge.py routes), wrapped
in a thin repository for testability. Each method opens its own connection (one tx).

Style aligned with knowledge_mining/mining/infra/db.py (TEXT ids, ISO timestamps,
JSONB via ::jsonb cast, dict_row return).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.rows import dict_row

from knowledge_mining.mining.workflow.presets import DEFAULT_WORKFLOW_ID


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(obj: Any) -> str:
    if obj is None:
        return "{}"
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


#: readiness 档位（低 → 高）。展示层按 level 索引取标签/颜色。
READINESS_LEVELS = (
    "empty", "parsed", "segmented", "lexical_ready", "vector_ready",
)


def derive_readiness_level(
    *,
    documents: int,
    segments: int,
    retrieval_units: int,
    embeddings: int,
    embedding_fallback: bool = False,
) -> str:
    """批次4 readiness 档位派生（纯函数，供 get_kb_readiness 与测试直接用）。

    - empty: 连文档都还没有
    - parsed: 有文档快照（解析完成）
    - segmented: 快照已有切片（挖掘链已跑）
    - lexical_ready: 有检索单元（关键词检索可命中）
    - vector_ready: 有向量且无嵌入降级留痕（语义检索可用；fallback 留痕时
      最高只到 lexical_ready——语义检索缺失但已显式留痕，不算静默）
    """
    if documents <= 0:
        return "empty"
    if segments <= 0:
        return "parsed"
    if retrieval_units <= 0:
        return "segmented"
    if embeddings <= 0 or embedding_fallback:
        return "lexical_ready"
    return "vector_ready"


# ── 文档状态派生 SQL 片段（alias d = asset_documents）────────────────────────
# CASE 在 SELECT；LATERAL 在 FROM。优先级：
#   published > failed > mined(committed) > mining(pending/processing) > withdrawn(removed) > uploaded
# 「mined」档专门给 KB 挖掘用：KB 走 publish=False（只 build 不进域级 active release），
# 文档在 mining_run_documents 到达 committed 后，若没有这一档会被 ELSE 兜底回 'uploaded'，
# 与「没挖过」无法区分——这正是「挖掘后状态没打通」的根因。
# 折进列表/详情查询，避免对每个文档单独查（N+1，远程库下 2-3s 卡顿的根因）。
_STATUS_CASE_SQL = """CASE
    WHEN COALESCE(pub.published, FALSE) THEN 'published'
    WHEN rs.rd_status = 'failed' THEN 'failed'
    WHEN rs.rd_status = 'committed' THEN 'mined'
    WHEN rs.rd_status IN ('pending', 'processing') THEN 'mining'
    WHEN COALESCE(rm.removed, FALSE) THEN 'withdrawn'
    ELSE 'uploaded'
END"""

# 同一套派生，**去掉 release 两档**。给概览统计用：published/withdrawn 要靠
# _RELEASE_JOIN_SQL 的两次 release⋈snapshot EXISTS（每文档两次），而纯 KB 部署
# （publish=False，永不产 release）下这两档恒为 0 —— 为一对恒零的桶付全域文档的
# 扫描代价不值得。域里确实有 active release 时才切回完整版（见 stats_document_status）。
_STATUS_CASE_NO_RELEASE_SQL = """CASE
    WHEN rs.rd_status = 'failed' THEN 'failed'
    WHEN rs.rd_status = 'committed' THEN 'mined'
    WHEN rs.rd_status IN ('pending', 'processing') THEN 'mining'
    ELSE 'uploaded'
END"""


# ⚠️ document_key 不是全局唯一的：build_document_key() 产的是 doc:/{相对路径}，**不含
# kb_id**（全局唯一的是 storage_path）。只按 document_key 关联 mining_run_documents，
# 两个 KB 各有一个根目录 spec.pdf 时状态会互相串味——A 库挖失败，B 库那篇也显示 failed。
# 所以经 run 补上归属维度：
#   mr.kb_id IS NOT DISTINCT FROM d.kb_id  ——「NULL = NULL」要成立：legacy 文档
#       （kb_id 为 NULL）该由 legacy 域级 run 决定状态，普通 = 比较会把它判成 NULL 而漏掉
#   mr.domain = d.domain                   —— 同一文件在两个域各挖一次时再隔一层
_RUN_DOC_JOIN_SQL = """
LEFT JOIN LATERAL (
    SELECT r.status AS rd_status
    FROM mining_run_documents r
    JOIN mining_runs mr ON mr.id = r.run_id
    WHERE r.document_key = d.document_key
      AND mr.domain = d.domain
      AND mr.kb_id IS NOT DISTINCT FROM d.kb_id
    ORDER BY r.finished_at DESC NULLS LAST, r.started_at DESC NULLS LAST, r.id DESC
    LIMIT 1
) rs ON TRUE"""

# published / withdrawn 两档要查 active release，是这段里最贵的部分（每文档两次
# release⋈snapshot 的 EXISTS）。单独拆出来，好让只关心「挖没挖成」的调用方（概览页聚合）
# 不必付这笔钱——它每个用户登录后都要跑一次，admin 还要跑全域文档。
_RELEASE_JOIN_SQL = """
LEFT JOIN LATERAL (
    SELECT EXISTS (
        SELECT 1 FROM asset_publish_releases rel
        JOIN asset_build_document_snapshots bs ON bs.build_id = rel.build_id
        WHERE rel.domain = d.domain AND rel.status = 'active'
          AND bs.document_id = d.id AND bs.selection_status = 'active'
    ) AS published
) pub ON TRUE
LEFT JOIN LATERAL (
    SELECT EXISTS (
        SELECT 1 FROM asset_publish_releases rel
        JOIN asset_build_document_snapshots bs ON bs.build_id = rel.build_id
        WHERE rel.domain = d.domain AND rel.status = 'active'
          AND bs.document_id = d.id AND bs.selection_status = 'removed'
    ) AS removed
) rm ON TRUE"""

# 完整派生（六态）= 归属收敛的 run 关联 + release 关联。列表/详情用它。
_STATUS_JOIN_SQL = _RUN_DOC_JOIN_SQL + _RELEASE_JOIN_SQL


class KbDB:
    """Async repository over kb_users / knowledge_bases / kb_members.

    Constructed with a psycopg AsyncConnectionPool opened with row_factory=dict_row.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # ---------------------------------------------------------------- users

    async def upsert_user_by_username(
        self, username: str, *, display_name: str | None = None
    ) -> dict[str, Any]:
        """Idempotent user upsert by username.

        冲突时只更新 display_name —— 绝不动 site_role / password_hash（§5.3 不变量），
        否则某 admin 用户的日常 KB 流量会把他降级或清空密码。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO kb_users (id, username, display_name, status, created_at)
                   VALUES (%(id)s, %(u)s, %(d)s, 'active', %(t)s)
                   ON CONFLICT (username) DO UPDATE
                     SET display_name = COALESCE(%(d)s, kb_users.display_name)
                   RETURNING id, username, display_name, status, site_role""",
                {"id": _new_id(), "u": username, "d": display_name, "t": _utcnow()},
            )
            row = await cur.fetchone()
            return dict(row)  # type: ignore[arg-type]

    # ---------------------------------------------------- user management (Phase 2)

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, username, display_name, status, site_role, password_hash, created_at
                   FROM kb_users WHERE username = %s""",
                [username],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, username, display_name, status, site_role, password_hash, created_at
                   FROM kb_users WHERE id = %s""",
                [user_id],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_users(self) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, username, display_name, status, site_role,
                          (password_hash IS NOT NULL) AS has_password, created_at
                   FROM kb_users ORDER BY created_at""",
            )
            return [dict(r) for r in await cur.fetchall()]

    async def create_user(
        self, *, username: str, password_hash: str | None = None,
        site_role: str = "member", display_name: str | None = None,
    ) -> dict[str, Any]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO kb_users (id, username, display_name, status, created_at,
                                         password_hash, site_role)
                   VALUES (%(id)s, %(u)s, %(d)s, 'active', %(t)s, %(ph)s, %(sr)s)
                   RETURNING id, username, display_name, status, site_role, password_hash, created_at""",
                {"id": _new_id(), "u": username, "d": display_name, "t": _utcnow(),
                 "ph": password_hash, "sr": site_role},
            )
            return dict(await cur.fetchone())  # type: ignore[arg-type]

    async def update_user(
        self, user_id: str, *,
        display_name: str | None = None, site_role: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """PATCH 风格：只更新提供的字段；未提供的不动。None = 不传（非 SET NULL）。"""
        sets: list[str] = []
        params: dict[str, Any] = {"id": user_id}
        if display_name is not None:
            params["d"] = display_name
            sets.append("display_name = %(d)s")
        if site_role is not None:
            params["sr"] = site_role
            sets.append("site_role = %(sr)s")
        if status is not None:
            params["st"] = status
            sets.append("status = %(st)s")
        if not sets:
            return await self.get_user(user_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE kb_users SET " + ", ".join(sets) + " WHERE id = %(id)s "
                "RETURNING id, username, display_name, status, site_role, created_at",
                params,
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_password_hash(self, user_id: str, password_hash: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE kb_users SET password_hash = %s WHERE id = %s",
                [password_hash, user_id],
            )

    async def has_admin(self) -> bool:
        """是否存在可登录的 admin（site_role='admin' AND password_hash IS NOT NULL）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM kb_users "
                "WHERE site_role='admin' AND password_hash IS NOT NULL LIMIT 1"
            )
            return (await cur.fetchone()) is not None

    async def count_active_admins(self) -> int:
        """启用的 admin 数（site_role='admin' AND status='active'）—— last-admin 守卫用。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM kb_users "
                "WHERE site_role='admin' AND status='active'"
            )
            return int((await cur.fetchone())["n"])

    # -------------------------------------------------------- knowledge bases

    async def create_kb(
        self,
        *,
        domain: str,
        name: str,
        owner_id: str,
        visibility: str = "private",
        description: str | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO knowledge_bases
                     (id, domain, name, description, owner_id, visibility, status,
                      metadata_json, created_at, updated_at, mining_workflow_id)
                   VALUES
                     (%(id)s, %(dom)s, %(n)s, %(desc)s, %(own)s, %(vis)s, 'active',
                       %(meta)s::jsonb, %(t)s, %(t)s, %(wf)s)
                   RETURNING id, domain, name, description, owner_id, visibility,
                             status, created_at, updated_at, mining_workflow_id""",
                {
                    "id": _new_id(), "dom": domain, "n": name, "desc": description,
                    "own": owner_id, "vis": visibility, "meta": _json(metadata), "t": _utcnow(),
                    "wf": (
                        (metadata or {}).get("mining_workflow_id")
                        or DEFAULT_WORKFLOW_ID
                    ),
                },
            )
            row = await cur.fetchone()
            return dict(row)  # type: ignore[arg-type]

    async def get_kb_readiness(self, kb_id: str) -> dict[str, Any]:
        """批次4 readiness 四档的纯查询派生（无 DDL）。

        口径：KB 内活跃（未软删）文档的**最新快照**——与检索侧 per-document
        最新快照语义一致。embedding_fallback 取该 KB 最新 build 的冻结签名。
        档位：empty → parsed → segmented → lexical_ready → vector_ready。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """WITH latest AS (
                       SELECT DISTINCT ON (l.document_id)
                              l.document_id, l.document_snapshot_id
                       FROM asset_document_snapshot_links l
                       JOIN asset_documents d ON d.id = l.document_id
                       WHERE d.kb_id = %(kb)s AND d.deleted_at IS NULL
                       ORDER BY l.document_id, l.linked_at DESC
                   )
                   SELECT
                     (SELECT COUNT(*) FROM latest) AS documents,
                     (SELECT COUNT(*) FROM asset_raw_segments s
                       JOIN latest ON s.document_snapshot_id = latest.document_snapshot_id) AS segments,
                     -- 2026-09-01 用户反馈：旧链表恒 0——v2 算子链只写
                     -- asset_retrieval_units_v2 / _embeddings_v2（按 snapshot_id 关联）。
                     (SELECT COUNT(*) FROM asset_retrieval_units_v2 u
                       JOIN latest ON u.snapshot_id = latest.document_snapshot_id) AS retrieval_units,
                     (SELECT COUNT(*) FROM asset_retrieval_embeddings_v2 e
                       JOIN latest ON e.snapshot_id = latest.document_snapshot_id) AS embeddings,
                     (SELECT COALESCE(
                               (b.summary_json ->> 'embedding_fallback')::boolean,
                               false)
                        FROM asset_builds b
                        WHERE b.kb_id = %(kb)s
                        ORDER BY b.created_at DESC LIMIT 1) AS embedding_fallback""",
                {"kb": kb_id},
            )
            row = dict(await cur.fetchone())
        level = derive_readiness_level(
            documents=int(row["documents"] or 0),
            segments=int(row["segments"] or 0),
            retrieval_units=int(row["retrieval_units"] or 0),
            embeddings=int(row["embeddings"] or 0),
            embedding_fallback=bool(row["embedding_fallback"]),
        )
        return {
            "level": level,
            "documents": int(row["documents"] or 0),
            "segments": int(row["segments"] or 0),
            "retrieval_units": int(row["retrieval_units"] or 0),
            "embeddings": int(row["embeddings"] or 0),
            "embedding_fallback": bool(row["embedding_fallback"]),
        }

    async def get_kb(self, kb_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        clause = "" if include_deleted else " AND status = 'active'"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, domain, name, description, owner_id, visibility, status,
                          deleted_at, created_at, updated_at, mining_workflow_id,
                          default_paradigm_id
                   FROM knowledge_bases WHERE id = %s""" + clause,
                [kb_id],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_visible(self, *, user_id: str, domain: str) -> list[dict[str, Any]]:
        """KBs visible to user in domain: owned + member + public, status='active'.

        site admin 短路：看域内全部 KB，my_role='admin'（admin 全通）。
        其余附带 my_role（owner/editor/viewer 有效访问级别）与 document_count（KB 内文档数），
        供列表页一次拿全、免 N+1。my_role 语义：owner 优先；否则 editor 成员；否则 viewer
        （含 viewer 成员与 public 的非成员读者——都是「只读有效角色」）。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT site_role FROM kb_users WHERE id = %(uid)s", {"uid": user_id},
            )
            urow = await cur.fetchone()
            if urow and urow.get("site_role") == "admin":
                # admin：域内全部 active KB，my_role='admin'
                cur = await conn.execute(
                    """SELECT kb.id, kb.domain, kb.name, kb.description,
                              kb.owner_id, kb.visibility, kb.created_at,
                              kb.mining_workflow_id, kb.default_paradigm_id,
                              'admin' AS my_role,
                              COALESCE(NULLIF(u.display_name, ''), u.username) AS owner_name,
                              (SELECT COUNT(*) FROM asset_documents d
                               WHERE d.kb_id = kb.id AND d.deleted_at IS NULL) AS document_count
                       FROM knowledge_bases kb
                       LEFT JOIN kb_users u ON u.id = kb.owner_id
                       WHERE kb.domain = %(dom)s AND kb.status = 'active'
                       ORDER BY kb.created_at DESC""",
                    {"dom": domain},
                )
                return [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                """SELECT kb.id, kb.domain, kb.name, kb.description,
                          kb.owner_id, kb.visibility, kb.created_at,
                          kb.mining_workflow_id, kb.default_paradigm_id,
                          CASE
                            WHEN kb.owner_id = %(uid)s THEN 'owner'
                            WHEN EXISTS (SELECT 1 FROM kb_members m
                                         WHERE m.kb_id = kb.id AND m.user_id = %(uid)s
                                           AND m.role = 'editor') THEN 'editor'
                            ELSE 'viewer'
                          END AS my_role,
                          COALESCE(NULLIF(u.display_name, ''), u.username) AS owner_name,
                          (SELECT COUNT(*) FROM asset_documents d
                           WHERE d.kb_id = kb.id AND d.deleted_at IS NULL) AS document_count
                   FROM knowledge_bases kb
                   LEFT JOIN kb_users u ON u.id = kb.owner_id
                   WHERE kb.domain = %(dom)s AND kb.status = 'active'
                     AND (kb.owner_id = %(uid)s
                          OR kb.visibility = 'public'
                          OR EXISTS (SELECT 1 FROM kb_members m
                                     WHERE m.kb_id = kb.id AND m.user_id = %(uid)s))
                   ORDER BY kb.created_at DESC""",
                {"uid": user_id, "dom": domain},
            )
            return [dict(r) for r in await cur.fetchall()]

    async def list_visible_kb_ids(self, *, user_id: str, domain: str) -> list[str]:
        """域内该用户可见的 KB id 集——list_visible 的轻量版。

        可见性条件与 list_visible / is_visible 逐项一致（admin 全通 / owner / public /
        任意成员），只是不带 my_role 与 document_count 那个 COUNT 子查询。给「按可见集
        收窄」这类调用方用（/api/runs 护栏、概览页聚合），它们只要 id 边界。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT kb.id FROM knowledge_bases kb
                   WHERE kb.domain = %(dom)s AND kb.status = 'active'
                     AND (EXISTS (SELECT 1 FROM kb_users u
                                  WHERE u.id = %(uid)s AND u.site_role = 'admin')
                          OR kb.owner_id = %(uid)s
                          OR kb.visibility = 'public'
                          OR EXISTS (SELECT 1 FROM kb_members m
                                     WHERE m.kb_id = kb.id AND m.user_id = %(uid)s))""",
                {"uid": user_id, "dom": domain},
            )
            return [r["id"] for r in await cur.fetchall()]

    # ------------------------------------------------------------- 概览页聚合
    # 首页（检索入口 + 我的知识库）一次取齐所需。分成几段独立查询而不是一条巨型 SQL：
    # 每段的分组维度不同（文档 / run / release），硬拼会互相放大行数。

    async def overview_status_counts(
        self, *, kb_ids: list[str],
    ) -> dict[str, dict[str, int]]:
        """按 KB 统计文档总数 / 挖掘中 / 失败。

        **只回首页真正渲染的三个数**。曾想过回六态齐全，但 published/withdrawn/uploaded/
        mined 在页面上没有渲染位，而算它们要多挂两条 release⋈snapshot 的 EXISTS
        （_RELEASE_JOIN_SQL）——这是登录落地页，不值得。所以这里只用 _RUN_DOC_JOIN_SQL。
        """
        if not kb_ids:
            return {}
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT d.kb_id,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (
                               WHERE rs.rd_status IN ('pending', 'processing')
                           ) AS mining,
                           COUNT(*) FILTER (WHERE rs.rd_status = 'failed') AS failed
                    FROM asset_documents d
                    {_RUN_DOC_JOIN_SQL}
                    WHERE d.kb_id = ANY(%s) AND d.deleted_at IS NULL
                    GROUP BY d.kb_id""",
                [kb_ids],
            )
            return {
                r["kb_id"]: {
                    "total": r["total"], "mining": r["mining"], "failed": r["failed"],
                }
                for r in await cur.fetchall()
            }

    async def overview_run_rollup(self, *, kb_ids: list[str]) -> dict[str, dict[str, Any]]:
        """按 KB 汇总 run：最近一次成功挖掘时间 + 最新一条待人审 run。

        `last_mined_at` 只认 `completed`：这个字段在卡片上是「最近一次成功产出知识」，
        把 failed/cancelled 算进去会让一个反复失败的库看起来很新鲜。
        两项合成一次扫描——都是按 kb_id 分组的 mining_runs 聚合，没有理由查两遍。
        """
        if not kb_ids:
            return {}
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT kb_id,
                          MAX(finished_at) FILTER (WHERE status = 'completed')
                              AS last_mined_at,
                          (ARRAY_AGG(id ORDER BY started_at DESC)
                              FILTER (WHERE status = 'awaiting_review'))[1]
                              AS awaiting_review_run_id
                   FROM mining_runs
                   WHERE kb_id = ANY(%s)
                   GROUP BY kb_id""",
                [kb_ids],
            )
            return {r["kb_id"]: dict(r) for r in await cur.fetchall()}

    async def overview_recent_runs(
        self, *, kb_ids: list[str], limit: int = 5,
    ) -> list[dict[str, Any]]:
        """跨 KB 的最近挖掘记录。**kb_id 必须回传**——前端要用它拼 /kb/{kbId}/run/{runId}，
        缺了前端只能拼出已删除的 /mining/{runId}，点击进空白页。"""
        if not kb_ids:
            return []
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT r.id, r.kb_id, kb.name AS kb_name, r.status,
                          r.total_documents, r.new_count, r.updated_count,
                          r.started_at, r.finished_at
                   FROM mining_runs r
                   JOIN knowledge_bases kb ON kb.id = r.kb_id
                   WHERE r.kb_id = ANY(%s)
                   ORDER BY r.started_at DESC
                   LIMIT %s""",
                [kb_ids, limit],
            )
            return [dict(r) for r in await cur.fetchall()]

    async def has_active_release(self, *, domain: str) -> bool:
        """该域有无域级 active release —— 决定检索范围选择器是否呈现「域级发布」项。

        KB 挖掘 publish=False 永不产 release，纯 KB 部署下恒为 False；此时把那个选项
        摆出来只会让人选中后撞 no_active_release。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT EXISTS (
                       SELECT 1 FROM asset_publish_releases
                       WHERE domain = %s AND status = 'active'
                   ) AS present""",
                [domain],
            )
            row = await cur.fetchone()
            return bool(row and row["present"])

    # ------------------------------------------------------------- 概览页统计
    # 概览页的数字与图表（GET /api/kb/stats）。全部按调用方**可见的 kb_ids** 收敛 ——
    # 传进来的 id 列表由路由用 list_visible 算好，这一层不再重复判权限。
    #
    # 「当前知识」的口径统一是 _CURRENT_SNAPSHOT_CTE：每个文档取它最近一次进入
    # validated/published build 的快照。不能对 asset_* 表直接 COUNT —— 每次重挖都会
    # 产生一份新快照，累计计数会把「挖了 3 遍的 10 篇文档」显示成 30 篇的知识量。

    _STATUS_KEYS = ("uploaded", "mining", "mined", "published", "withdrawn", "failed")

    # 每文档一行的「当前快照」。DISTINCT ON 取 build 最新的那条；selection_status
    # 'removed'（撤回）不算当前知识。与 get_document_knowledge 的单文档口径一致。
    _CURRENT_SNAPSHOT_CTE = """
WITH cur AS (
    SELECT DISTINCT ON (bs.document_id) bs.document_snapshot_id
    FROM asset_build_document_snapshots bs
    JOIN asset_builds b ON b.id = bs.build_id
    WHERE b.kb_id = ANY(%(kb)s)
      AND bs.selection_status = 'active'
      AND b.status IN ('validated', 'published')
    ORDER BY bs.document_id, b.created_at DESC
)"""

    async def stats_document_status(
        self, *, kb_ids: list[str], with_release: bool = False,
    ) -> dict[str, int]:
        """文档状态分布（六个键恒存在，无数据给 0）。

        with_release=False（默认）时 published/withdrawn 恒为 0 —— 不是「没有」，是这一
        口径在纯 KB 部署下不适用。路由按 has_active_release 决定是否开启完整派生。
        """
        counts = dict.fromkeys(self._STATUS_KEYS, 0)
        if not kb_ids:
            return counts
        case_sql = _STATUS_CASE_SQL if with_release else _STATUS_CASE_NO_RELEASE_SQL
        join_sql = _STATUS_JOIN_SQL if with_release else _RUN_DOC_JOIN_SQL
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT {case_sql} AS status, COUNT(*) AS c
                    FROM asset_documents d
                    {join_sql}
                    WHERE d.kb_id = ANY(%s) AND d.deleted_at IS NULL
                    GROUP BY 1""",
                [kb_ids],
            )
            for row in await cur.fetchall():
                if row["status"] in counts:
                    counts[row["status"]] = int(row["c"])
        return counts

    async def stats_assets(self, *, kb_ids: list[str]) -> dict[str, int]:
        """当前知识的资产量：快照 / 切片 / 检索单元 / 向量。

        2026-09-01 口径切换：v2 算子链只写 ``asset_retrieval_units_v2`` /
        ``asset_retrieval_embeddings_v2``（旧链表恒 0）；实体提及/切片关系两
        指标随实体本体线下线移除，新增向量数。
        """
        empty = {
            "snapshots": 0, "segments": 0, "retrieval_units": 0, "embeddings": 0,
        }
        if not kb_ids:
            return empty
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""{self._CURRENT_SNAPSHOT_CTE}
                    SELECT
                      (SELECT COUNT(*) FROM cur) AS snapshots,
                      (SELECT COUNT(*) FROM asset_raw_segments s
                         JOIN cur ON cur.document_snapshot_id = s.document_snapshot_id)
                        AS segments,
                      (SELECT COUNT(*) FROM asset_retrieval_units_v2 u
                         JOIN cur ON cur.document_snapshot_id = u.snapshot_id)
                        AS retrieval_units,
                      (SELECT COUNT(*) FROM asset_retrieval_embeddings_v2 e
                         JOIN cur ON cur.document_snapshot_id = e.snapshot_id)
                        AS embeddings""",
                {"kb": kb_ids},
            )
            row = await cur.fetchone()
            return {k: int(row[k]) for k in empty} if row else empty

    async def stats_retrieval_unit_types(self, *, kb_ids: list[str]) -> dict[str, int]:
        """检索单元类型分布（只回非零的类型；前端按拿到的键渲染，不假定全集）。

        2026-09-01 口径切换：v2 表 representation_type。
        """
        if not kb_ids:
            return {}
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""{self._CURRENT_SNAPSHOT_CTE}
                    SELECT u.representation_type AS unit_type, COUNT(*) AS c
                    FROM asset_retrieval_units_v2 u
                    JOIN cur ON cur.document_snapshot_id = u.snapshot_id
                    GROUP BY u.representation_type
                    ORDER BY c DESC""",
                {"kb": kb_ids},
            )
            return {r["unit_type"]: int(r["c"]) for r in await cur.fetchall()}

    async def stats_mining_trend(
        self, *, kb_ids: list[str], days: int = 30,
    ) -> list[dict[str, Any]]:
        """近 N 天每日挖掘量。**空天补零**，前端直接照数组画折线。

        缺的那些天必须由后端补：折线图跳过没有数据的日期会把「三周没挖」画成一段平缓
        上升，读起来像一直在产出。
        `started_at` 是 ISO-8601 UTC 文本，前 10 位即日期、字典序即时序，故可直接比较。
        """
        today = datetime.now(timezone.utc).date()
        buckets = {
            (today - timedelta(days=i)).isoformat(): {"runs": 0, "completed": 0, "documents": 0}
            for i in range(days)
        }
        if kb_ids:
            since = (today - timedelta(days=days - 1)).isoformat()
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    """SELECT substring(started_at from 1 for 10) AS day,
                              COUNT(*) AS runs,
                              COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                              COALESCE(SUM(committed_count), 0) AS documents
                       FROM mining_runs
                       WHERE kb_id = ANY(%s) AND started_at >= %s
                       GROUP BY 1""",
                    [kb_ids, since],
                )
                for row in await cur.fetchall():
                    bucket = buckets.get(row["day"])
                    if bucket is not None:
                        bucket["runs"] = int(row["runs"])
                        bucket["completed"] = int(row["completed"])
                        bucket["documents"] = int(row["documents"])
        return [{"date": d, **buckets[d]} for d in sorted(buckets)]

    # 允许 PATCH 更新的列白名单。值可为 None → 显式清空（SET NULL）。
    _KB_UPDATABLE_COLUMNS = {
        "name", "description", "visibility", "mining_workflow_id",
        "default_paradigm_id",  # 阶段 A：库级默认检索范式（跨库引用，service 层校验）
    }

    async def update_kb(
        self,
        kb_id: str,
        *,
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        """PATCH 更新。fields 只含**显式提供**的列（路由层用 model_fields_set 过滤），
        None 表示显式清空（SET NULL）；未提供的列不动。列名经白名单校验，防注入。"""
        allowed = {k: v for k, v in fields.items() if k in self._KB_UPDATABLE_COLUMNS}
        if not allowed:
            return await self.get_kb(kb_id)
        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": kb_id, "t": _utcnow()}
        for col, val in allowed.items():
            key = f"f{len(params)}"  # 唯一参数键
            set_clauses.append(f"{col} = %({key})s")
            params[key] = val
        set_clauses.append("updated_at = %(t)s")
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE knowledge_bases SET """ + ", ".join(set_clauses) + """
                   WHERE id = %(id)s AND status = 'active'
                   RETURNING id, domain, name, description, owner_id, visibility, status,
                             mining_workflow_id, default_paradigm_id""",
                params,
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def soft_delete(self, kb_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE knowledge_bases
                   SET status = 'deleted', deleted_at = %(t)s, updated_at = %(t)s
                   WHERE id = %(id)s AND status = 'active'
                   RETURNING id, status, deleted_at""",
                {"id": kb_id, "t": _utcnow()},
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def restore_kb(self, kb_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE knowledge_bases
                   SET status = 'active', deleted_at = NULL, updated_at = %(t)s
                   WHERE id = %(id)s AND status = 'deleted'
                   RETURNING id, domain, name, description, owner_id,
                             visibility, status, deleted_at, created_at,
                             updated_at, mining_workflow_id,
                             default_paradigm_id""",
                {"id": kb_id, "t": _utcnow()},
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    # ------------------------------------------------ MCP 用户接入（阶段 A / 批次5）
    # 一人一钥：mcp_access 每用户至多一行；rotate 覆盖 key_hash（旧钥立即失效，无并存期）。

    async def get_mcp_access(self, user_id: str) -> dict[str, Any] | None:
        """本人视角的接入状态（无 hash；含开放库列表）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT user_id, key_prefix, status, created_at,
                          last_used_at, rotated_at,
                          open_tools, instructions, tool_descriptions
                   FROM mcp_access WHERE user_id = %s""",
                [user_id],
            )
            row = await cur.fetchone()
            if row is None:
                return None
            result = dict(row)
            # 开放清单只报"仍有效"的库（active ∩ 可见）——软删/权限收走的库自动消失，
            # 不再把幽灵 id 交给前端混进下一次保存请求（批次7 bug：整单被 not visible 拒）。
            cur = await conn.execute(
                """SELECT o.kb_id FROM mcp_open_kbs o
                   JOIN knowledge_bases k ON k.id = o.kb_id
                   WHERE o.user_id = %s AND k.status = 'active'
                   ORDER BY o.granted_at""",
                [user_id],
            )
            result["open_kb_ids"] = [r["kb_id"] for r in await cur.fetchall()]
            return result

    async def upsert_mcp_key(
        self, user_id: str, *, key_hash: str, key_prefix: str,
    ) -> dict[str, Any]:
        """生成/轮换接入密钥：覆盖 hash 即轮换（status 复位 active、旧钥立即失效）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO mcp_access (user_id, key_hash, key_prefix, rotated_at)
                   VALUES (%(u)s, %(h)s, %(p)s, now())
                   ON CONFLICT (user_id) DO UPDATE SET
                     key_hash = EXCLUDED.key_hash,
                     key_prefix = EXCLUDED.key_prefix,
                     status = 'active',
                     rotated_at = EXCLUDED.rotated_at
                   RETURNING user_id, key_prefix, status, created_at, rotated_at""",
                {"u": user_id, "h": key_hash, "p": key_prefix},
            )
            return dict(await cur.fetchone())  # type: ignore[arg-type]

    async def verify_mcp_key(
        self, key_hash: str, *, last_used_throttle_s: int = 60,
    ) -> dict[str, Any] | None:
        """按 sha256 命中 active 密钥 → {username, user_id, open_kb_ids}；miss → None。

        last_used_at 节流更新（距上次 ≥60s 才写），避免每次检索一次 UPDATE。
        开放库随响应带出——MCP 免二次查询；权限收窄由检索层 authorize 兜底。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE mcp_access a
                   SET last_used_at = now()
                   WHERE a.key_hash = %(h)s AND a.status = 'active'
                     AND (a.last_used_at IS NULL
                          OR a.last_used_at <= now() - %(throttle)s * interval '1 second')
                   RETURNING a.user_id""",
                {"h": key_hash, "throttle": last_used_throttle_s},
            )
            hit = await cur.fetchone()
            user_id = hit["user_id"] if hit else None
            if user_id is None:
                # 节流窗口内或无需更新：只读校验
                cur = await conn.execute(
                    """SELECT user_id FROM mcp_access
                       WHERE key_hash = %s AND status = 'active'""",
                    [key_hash],
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                user_id = row["user_id"]
            cur = await conn.execute(
                "SELECT username FROM kb_users WHERE id = %s", [user_id],
            )
            urow = await cur.fetchone()
            if urow is None:
                return None
            cur = await conn.execute(
                """SELECT k.id, k.name, k.domain FROM mcp_open_kbs o
                   JOIN knowledge_bases k ON k.id = o.kb_id
                   WHERE o.user_id = %s AND k.status = 'active'
                   ORDER BY k.name""",
                [user_id],
            )
            open_kbs = [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                """SELECT open_tools, instructions, tool_descriptions
                   FROM mcp_access WHERE user_id = %s""",
                [user_id],
            )
            cfg = dict(await cur.fetchone())
            return {
                "user_id": user_id,
                "username": urow["username"],
                "open_kb_ids": [r["id"] for r in open_kbs],
                "open_kbs": open_kbs,
                "open_tools": cfg.get("open_tools"),
                "instructions": cfg.get("instructions"),
                "tool_descriptions": cfg.get("tool_descriptions"),
            }

    async def replace_open_kbs(self, user_id: str, kb_ids: list[str]) -> list[str]:
        """全量覆盖开放库勾选（调用方负责校验本人可见性）。返回最终列表。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM mcp_open_kbs WHERE user_id = %s", [user_id],
                )
                for kb_id in kb_ids:
                    await conn.execute(
                        """INSERT INTO mcp_open_kbs (user_id, kb_id)
                           VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                        [user_id, kb_id],
                    )
            cur = await conn.execute(
                "SELECT kb_id FROM mcp_open_kbs WHERE user_id = %s ORDER BY granted_at",
                [user_id],
            )
            return [r["kb_id"] for r in await cur.fetchall()]

    async def update_mcp_config(
        self,
        user_id: str,
        *,
        open_tools: list[str] | None,
        instructions: str | None,
        tool_descriptions: dict[str, str] | None,
    ) -> None:
        """批次7：工具开关 / 提示词 / 工具描述。None = 不改该字段；
        open_tools 传 [] 语义上等于"全关"——由 service 层拒绝（至少留一个工具）。"""
        async with self._pool.connection() as conn:
            await conn.execute(
                """UPDATE mcp_access SET
                     open_tools = COALESCE(%(ot)s::jsonb, open_tools),
                     instructions = COALESCE(%(ins)s, instructions),
                     tool_descriptions = COALESCE(%(td)s::jsonb, tool_descriptions)
                   WHERE user_id = %(u)s""",
                {
                    "u": user_id,
                    "ot": _json(open_tools) if open_tools is not None else None,
                    "ins": instructions,
                    "td": _json(tool_descriptions) if tool_descriptions is not None else None,
                },
            )

    # ------------------------------------------------------------- kb mining
    # KB 中心化挖掘（融合设计 §5.2）：本库挖掘记录 + 文档当前知识（供前端「挖掘」tab / 文件多 tab）。

    async def list_kb_runs(self, kb_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """本 KB 的挖掘记录（mining_runs by kb_id），最新在前。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, status, current_stage, execution_engine,
                          workflow_id, workflow_version,
                          started_at, finished_at, error_summary,
                          total_documents, new_count, updated_count, skipped_count,
                          failed_count, committed_count
                   FROM mining_runs
                   WHERE kb_id = %s
                   ORDER BY started_at DESC
                   LIMIT %s""",
                [kb_id, limit],
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_document_knowledge(
        self, kb_id: str, document_id: str, *, max_rows: int = 2000,
    ) -> dict[str, Any]:
        """文档当前知识：查「包含该文档的最新 validated/published build」对应的 snapshot，
        返回该 snapshot 的正式 segments / retrieval_units。研究实体与关系不进入产品 API。

        注意：不能只读「KB 全局最新 build」——KB 多次/选择性挖掘下，每次 mine 产生的 build
        只含当次入选文档（增量父级继承未生效），全局最新 build 未必包含此文档，会误判 mined:False。
        改为按 document_id 反查最新含它的 build，才能稳定拿到该文档最近一次挖掘的知识。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT bs.document_snapshot_id, bs.build_id
                   FROM asset_build_document_snapshots bs
                   JOIN asset_builds b ON b.id = bs.build_id
                   WHERE bs.document_id = %s
                     AND bs.selection_status = 'active'
                     AND b.kb_id = %s
                     AND b.status IN ('validated', 'published')
                   ORDER BY b.created_at DESC
                   LIMIT 1""",
                [document_id, kb_id],
            )
            row = await cur.fetchone()
            if row is None:
                return {"mined": False, "build_id": None}
            snap_id = row["document_snapshot_id"]
            build_id = row["build_id"]
            # 批次3-问题2：四类知识读全部限量（窗口函数带出精确总数）——
            # 几千切片的大文档不再一次整包（截断标志随响应返回前端）。
            cur = await conn.execute(
                """SELECT segment_index, block_type, semantic_role, section_title,
                          raw_text, normalized_text,
                          COUNT(*) OVER() AS _total
                   FROM asset_raw_segments
                   WHERE document_snapshot_id = %s ORDER BY segment_index
                   LIMIT %s""",
                [snap_id, max_rows + 1],
            )
            seg_rows = [dict(r) for r in await cur.fetchall()]
            segments_truncated = len(seg_rows) > max_rows
            segments_total = (seg_rows[0].get("_total", len(seg_rows)) if seg_rows else 0)
            segments = [{k: v for k, v in r.items() if k != "_total"}
                        for r in seg_rows[:max_rows]]
            cur = await conn.execute(
                """SELECT unit_key, unit_type, title, text, block_type, semantic_role
                   FROM asset_retrieval_units
                   WHERE document_snapshot_id = %s ORDER BY unit_key
                   LIMIT %s""",
                [snap_id, max_rows],
            )
            units = [dict(r) for r in await cur.fetchall()]
            return {
                "mined": True,
                "truncated": segments_truncated,
                "total_segments": segments_total,
                "build_id": build_id,
                "document_snapshot_id": snap_id,
                "segments": segments,
                "retrieval_units": units,
            }

    # ---------------------------------------------------------------- members

    async def add_member(self, *, kb_id: str, user_id: str, role: str = "viewer") -> dict[str, Any]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO kb_members (kb_id, user_id, role, added_at)
                   VALUES (%(kb)s, %(u)s, %(r)s, %(t)s)
                   ON CONFLICT (kb_id, user_id) DO UPDATE SET role = EXCLUDED.role
                   RETURNING kb_id, user_id, role, added_at""",
                {"kb": kb_id, "u": user_id, "r": role, "t": _utcnow()},
            )
            row = await cur.fetchone()
            return dict(row)  # type: ignore[arg-type]

    async def list_members(self, kb_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT m.kb_id, m.user_id, m.role, m.added_at, u.username, u.display_name
                   FROM kb_members m JOIN kb_users u ON u.id = m.user_id
                   WHERE m.kb_id = %s ORDER BY m.added_at""",
                [kb_id],
            )
            return [dict(r) for r in await cur.fetchall()]

    async def remove_member(self, *, kb_id: str, user_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM kb_members WHERE kb_id = %s AND user_id = %s",
                [kb_id, user_id],
            )

    async def list_member_candidates(
        self, *, kb_id: str, q: str | None = None,
    ) -> list[dict[str, Any]]:
        """可加入该 KB 的候选用户:排除 owner 自己 + 已是 kb_members 的用户。

        仅返回 id/username/display_name 最小集 —— 不暴露 site_role/status/has_password
        等敏感字段(区别于 admin-only 的 list_users)。供成员面板的用户选择器使用。
        可选 q 做 username 前缀过滤(防用户规模膨胀;前端默认不传)。
        """
        params: list[Any] = [kb_id, kb_id]
        where_extra = ""
        if q:
            where_extra = " AND u.username ILIKE %s"
            params.append(f"{q}%")
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT u.id, u.username, u.display_name FROM kb_users u
                    WHERE u.status = 'active'
                      AND u.id <> (SELECT owner_id FROM knowledge_bases WHERE id = %s)
                      AND NOT EXISTS (
                          SELECT 1 FROM kb_members m
                          WHERE m.kb_id = %s AND m.user_id = u.id
                      ){where_extra}
                    ORDER BY u.username""",
                params,
            )
            return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------- visibility

    async def is_visible(self, *, kb_id: str, user_id: str) -> bool:
        """True iff user can read this KB (admin 全通 / owner / member / public) and KB is active."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 1 FROM knowledge_bases kb
                   WHERE kb.id = %s AND kb.status = 'active'
                     AND (EXISTS (SELECT 1 FROM kb_users u
                                  WHERE u.id = %s AND u.site_role = 'admin')
                          OR kb.owner_id = %s
                          OR kb.visibility = 'public'
                          OR EXISTS (SELECT 1 FROM kb_members m
                                     WHERE m.kb_id = kb.id AND m.user_id = %s))""",
                [kb_id, user_id, user_id, user_id],
            )
            return (await cur.fetchone()) is not None

    async def can_write(self, *, kb_id: str, user_id: str) -> bool:
        """True iff user can write this KB (admin 全通 / owner / editor member) and KB is active."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 1 FROM knowledge_bases kb
                   WHERE kb.id = %s AND kb.status = 'active'
                     AND (EXISTS (SELECT 1 FROM kb_users u
                                  WHERE u.id = %s AND u.site_role = 'admin')
                          OR kb.owner_id = %s
                          OR EXISTS (SELECT 1 FROM kb_members m
                                     WHERE m.kb_id = kb.id AND m.user_id = %s AND m.role = 'editor'))
                   """,
                [kb_id, user_id, user_id, user_id],
            )
            return (await cur.fetchone()) is not None

    async def can_restore(self, *, kb_id: str, user_id: str) -> bool:
        """Deleted KBs are restorable only by their owner or a site admin."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 1 FROM knowledge_bases kb
                   WHERE kb.id = %s
                     AND (EXISTS (SELECT 1 FROM kb_users u
                                  WHERE u.id = %s AND u.site_role = 'admin')
                          OR kb.owner_id = %s)""",
                [kb_id, user_id, user_id],
            )
            return (await cur.fetchone()) is not None

    # --------------------------------------------- documents (asset_documents identity)

    async def insert_document_identity(
        self, *, domain: str, kb_id: str, document_key: str, document_name: str,
        storage_path: str, directory_path: str | None = None,
        document_type: str | None = None, owner_id: str | None = None,
        file_size: int | None = None, modified_at: str | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """KB 上传：建文档身份行（不计算 hash、不建 snapshot——挖掘时才算）。

        写方归属：asset_documents 身份由 KB package 独占（设计铁律 1）。
        file_size / modified_at 由调用方 stat 落盘文件得到。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO asset_documents
                     (id, domain, document_key, document_name, document_type, metadata_json,
                      created_at, kb_id, storage_path, directory_path, owner_id,
                      file_size, modified_at)
                   VALUES
                     (%(id)s, %(dom)s, %(k)s, %(n)s, %(t)s, %(m)s::jsonb, %(now)s,
                      %(kb)s, %(sp)s, %(dp)s, %(own)s, %(fs)s, %(ma)s)
                   RETURNING id, domain, kb_id, document_key, document_name, document_type,
                             storage_path, directory_path, owner_id, created_at,
                             file_size, modified_at""",
                {
                    "id": _new_id(), "dom": domain, "k": document_key, "n": document_name,
                    "t": document_type, "m": _json(metadata), "now": _utcnow(),
                    "kb": kb_id, "sp": storage_path, "dp": directory_path, "own": owner_id,
                    "fs": file_size, "ma": modified_at,
                },
            )
            return dict(await cur.fetchone())  # type: ignore[arg-type]

    async def insert_document_from_storage(
        self, *, domain: str, kb_id: str, document_key: str, document_name: str,
        storage_object_id: str, source_raw_hash: str,
        directory_path: str | None = None, document_type: str | None = None,
        owner_id: str | None = None, file_size: int | None = None,
        modified_at: str | None = None,
    ) -> dict[str, Any]:
        """Create a KB document that points at an AVAILABLE object-store object.

        ``storage_path`` intentionally remains NULL.  It is a legacy migration
        field and must not be populated by new uploads: object identity plus
        the first content revision are committed with the document row.
        """
        now = _utcnow()
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO asset_documents
                     (id, domain, document_key, document_name, document_type,
                      metadata_json, created_at, kb_id, directory_path, owner_id,
                      file_size, modified_at, storage_object_id, source_raw_hash,
                      content_revision, content_updated_at)
                   VALUES
                     (%(id)s, %(dom)s, %(k)s, %(n)s, %(t)s, '{}'::jsonb,
                      %(now)s, %(kb)s, %(dp)s, %(own)s, %(fs)s, %(ma)s,
                      %(so)s, %(hash)s, 1, %(now2)s)
                   RETURNING id, domain, kb_id, document_key, document_name,
                             document_type, storage_path, directory_path, owner_id,
                             created_at, file_size, modified_at, storage_object_id,
                             source_raw_hash, content_revision""",
                {
                    "id": _new_id(), "dom": domain, "k": document_key,
                    "n": document_name, "t": document_type, "now": now,
                    # created_at 是 TEXT（001 legacy），content_updated_at 是
                    # TIMESTAMPTZ（008）——同一参数喂两列会触发
                    # AmbiguousParameter，必须拆成两个绑定参数。
                    "now2": now,
                    "kb": kb_id, "dp": directory_path, "own": owner_id,
                    "fs": file_size, "ma": modified_at,
                    "so": storage_object_id, "hash": source_raw_hash,
                },
            )
            return dict(await cur.fetchone())  # type: ignore[arg-type]

    async def list_documents_in_kb(
        self, *, kb_id: str, directory: str | None = None,
        limit: int = 200, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列 KB 内文档，**状态内联派生**（一次 SQL，避免 N+1 远程查询）。

        状态优先级与原 derive_document_status 一致：published > failed > mining > withdrawn > uploaded。
        """
        clause = "d.kb_id = %s AND d.deleted_at IS NULL"
        params: list[Any] = [kb_id]
        if directory is not None:
            clause += " AND d.directory_path = %s"
            params.append(directory)
        params.extend([limit, offset])
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT d.id, d.domain, d.kb_id, d.document_key, d.document_name,
                           d.document_type, d.storage_path, d.directory_path, d.owner_id,
                           d.created_at, d.file_size, d.modified_at,
                           d.storage_object_id, d.source_raw_hash, d.content_revision,
                           {_STATUS_CASE_SQL} AS status
                    FROM asset_documents d
                    {_STATUS_JOIN_SQL}
                    WHERE {clause}
                    ORDER BY d.created_at DESC LIMIT %s OFFSET %s""",
                params,
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_document_identity(
        self, document_id: str, *, include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        """单文档身份 + 内联派生状态（一次 SQL）。

        默认过滤软删行（详情/下载/预览/patch/删除的共同入口，P08-S1）；
        restore 与重传复活走 include_deleted=True。
        """
        soft_delete_clause = "" if include_deleted else " AND d.deleted_at IS NULL"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT d.id, d.domain, d.kb_id, d.document_key, d.document_name,
                           d.document_type, d.storage_path, d.directory_path, d.owner_id,
                           d.metadata_json, d.created_at, d.file_size, d.modified_at,
                           d.storage_object_id, d.source_raw_hash, d.content_revision,
                           {_STATUS_CASE_SQL} AS status
                    FROM asset_documents d
                    {_STATUS_JOIN_SQL}
                    WHERE d.id = %s{soft_delete_clause}""",
                [document_id],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def revive_document_from_storage(
        self, document_id: str, *, storage_object_id: str, source_raw_hash: str,
        file_size: int | None = None, modified_at: str | None = None,
    ) -> dict[str, Any] | None:
        """软删文档的重传复活（P08-S1）：软删行占着 uq_asset_documents_kb_key，
        同名重传不能 409——清软删标记并把对象指针/哈希/revision 前移到新内容。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE asset_documents
                   SET deleted_at = NULL, storage_object_id = %(so)s,
                       source_raw_hash = %(hash)s, file_size = COALESCE(%(fs)s, file_size),
                       modified_at = %(ma)s,
                       content_revision = content_revision + 1,
                       content_updated_at = %(ca)s
                   WHERE id = %(id)s AND deleted_at IS NOT NULL
                   RETURNING id, domain, kb_id, document_key, document_name,
                             document_type, storage_path, directory_path, owner_id,
                             created_at, file_size, modified_at, storage_object_id,
                             source_raw_hash, content_revision, deleted_at""",
                {"so": storage_object_id, "hash": source_raw_hash,
                 # modified_at 是 TEXT、content_updated_at 是 TIMESTAMPTZ——同一参数
                 # 供两列会 AmbiguousParameter（PG 会把转型绑定到参数本身），
                 # 必须拆成两个独立参数。
                 "fs": file_size, "ma": modified_at or _utcnow(),
                 "ca": modified_at or _utcnow(), "id": document_id},
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def find_document_by_key(
        self, kb_id: str, document_key: str, *, include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        """按 KB 内唯一键查身份（重传冲突预检用）。默认只找活文档。"""
        soft = "" if include_deleted else " AND deleted_at IS NULL"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, document_key, deleted_at FROM asset_documents
                   WHERE kb_id = %s AND document_key = %s""" + soft,
                [kb_id, document_key],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def soft_delete_document(self, document_id: str) -> None:
        """软删文档（P08-S1）：盖 deleted_at，不触 FK CASCADE——历史 Build 的
        selection 行完整保留（硬删会借 CASCADE 改写历史 Build，属 P0 事故面）。
        读面/检索退出由各查询的 deleted_at IS NULL 过滤实现。
        """
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE asset_documents SET deleted_at = %s WHERE id = %s",
                [datetime.now(timezone.utc).isoformat(), document_id],
            )

    async def clear_document_deleted(self, document_id: str) -> None:
        """restore：清软删标记（身份行与对象指针不变）。"""
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE asset_documents SET deleted_at = NULL WHERE id = %s",
                [document_id],
            )

    async def update_document_identity(
        self, document_id: str, *,
        document_name: str | None = None, document_type: str | None = None,
    ) -> dict[str, Any] | None:
        fields: list[str] = []
        params: dict[str, Any] = {"id": document_id}
        if document_name is not None:
            fields.append("document_name = %(n)s")
            params["n"] = document_name
        if document_type is not None:
            fields.append("document_type = %(t)s")
            params["t"] = document_type
        if not fields:
            return await self.get_document_identity(document_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE asset_documents SET " + ", ".join(fields) + " WHERE id = %(id)s "
                "RETURNING id, document_key, document_name, document_type, storage_path, directory_path",
                params,
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def derive_document_status(self, document_id: str) -> str:
        """单文档状态派生（保留作单点查询；列表/详情已用内联 _STATUS_CASE_SQL 一次取齐）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT {_STATUS_CASE_SQL} AS status
                    FROM asset_documents d
                    {_STATUS_JOIN_SQL}
                    WHERE d.id = %s AND d.deleted_at IS NULL""",
                [document_id],
            )
            row = await cur.fetchone()
            return row["status"] if row else "unknown"

    # ------------------------------------------------------------- folders (kb_folders)

    async def get_folder(self, folder_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, kb_id, parent_id, name, path, created_at, created_by
                   FROM kb_folders WHERE id = %s""",
                [folder_id],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_folders(self, kb_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, kb_id, parent_id, name, path, created_at, created_by
                   FROM kb_folders WHERE kb_id = %s ORDER BY path""",
                [kb_id],
            )
            return [dict(r) for r in await cur.fetchall()]

    async def find_folder_by_parent(
        self, *, kb_id: str, parent_id: str | None, name: str
    ) -> dict[str, Any] | None:
        """同父同名查找（parent_id NULL 用 IS NOT DISTINCT FROM 视作相等）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, kb_id, parent_id, name, path FROM kb_folders
                   WHERE kb_id = %s AND parent_id IS NOT DISTINCT FROM %s AND name = %s""",
                [kb_id, parent_id, name],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def find_folder_by_path(self, *, kb_id: str, path: str) -> dict[str, Any] | None:
        """按规范化完整 path 查找（如 ``5G/AMF``）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, kb_id, parent_id, name, path FROM kb_folders
                   WHERE kb_id = %s AND path = %s""",
                [kb_id, path],
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def insert_folder(
        self, *, folder_id: str, kb_id: str, parent_id: str | None, name: str,
        path: str, created_by: str | None = None,
    ) -> dict[str, Any]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO kb_folders (id, kb_id, parent_id, name, path, created_at, created_by)
                   VALUES (%(id)s, %(kb)s, %(p)s, %(n)s, %(path)s, %(t)s, %(cb)s)
                   RETURNING id, kb_id, parent_id, name, path, created_at, created_by""",
                {"id": folder_id, "kb": kb_id, "p": parent_id, "n": name,
                 "path": path, "t": _utcnow(), "cb": created_by},
            )
            return dict(await cur.fetchone())  # type: ignore[arg-type]

    async def count_child_folders(self, *, kb_id: str, parent_id: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) AS n FROM kb_folders WHERE kb_id = %s AND parent_id = %s",
                [kb_id, parent_id],
            )
            return int((await cur.fetchone())["n"])

    async def count_docs_under_path(self, *, kb_id: str, path: str) -> int:
        """统计某文件夹下（含子文件夹）的文档数。path 非空（根删除由上层处理）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT COUNT(*) AS n FROM asset_documents
                   WHERE kb_id = %s AND deleted_at IS NULL
                     AND (directory_path = %s OR directory_path LIKE %s)""",
                [kb_id, path, path + "/%"],
            )
            return int((await cur.fetchone())["n"])

    async def delete_folder_row(self, folder_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM kb_folders WHERE id = %s", [folder_id])

    # -- folder move / rename (G3)：身份键不变，只改位置（path / directory_path / storage_path）--

    async def update_folder_name(self, folder_id: str, name: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE kb_folders SET name = %s WHERE id = %s", [name, folder_id],
            )

    async def set_folder_parent(self, folder_id: str, parent_id: str | None) -> None:
        """parent_id 为 None 表示移到根。path 由 rewrite_folder_subtree_paths 处理。"""
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE kb_folders SET parent_id = %s WHERE id = %s", [parent_id, folder_id],
            )

    async def rewrite_folder_subtree_paths(
        self, *, kb_id: str, old_prefix: str, new_prefix: str,
    ) -> int:
        """把 path == old_prefix 或 LIKE 'old_prefix/%' 的文件夹 path 前缀替换为 new_prefix。

        返回受影响行数。path 是单列、纯前缀关系，SQL substr 重写安全。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """UPDATE kb_folders
                   SET path = %s || substr(path, %s)
                   WHERE kb_id = %s
                     AND (path = %s OR path LIKE %s)""",
                [new_prefix, len(old_prefix) + 1, kb_id, old_prefix, old_prefix + "/%"],
            )
            return cur.rowcount if hasattr(cur, "rowcount") else 0

    async def list_docs_under_prefix(self, *, kb_id: str, prefix: str) -> list[dict[str, Any]]:
        """列出身处某文件夹（含子文件夹）下的文档：directory_path = prefix 或 LIKE 'prefix/%'。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT id, document_name, directory_path, storage_path
                   FROM asset_documents
                   WHERE kb_id = %s AND deleted_at IS NULL
                     AND (directory_path = %s OR directory_path LIKE %s)""",
                [kb_id, prefix, prefix + "/%"],
            )
            return [dict(r) for r in await cur.fetchall()]

    async def update_doc_location(
        self, document_id: str, *, directory_path: str, storage_path: str, document_key: str,
    ) -> None:
        """移动文件：更新位置 + document_key。

        document_key 必须同步为新磁盘相对路径——挖掘从磁盘相对路径派生 key
        （jobs/run.py: doc_key = doc:/{relative_path}），若移动后 asset_documents.document_key
        仍停在旧路径，状态派生 LATERAL 按 document_key 匹配 mining_run_documents 会失败 →
        永远显示 uploaded。改名（patch_document）不动磁盘文件，document_key 不变。
        """
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE asset_documents SET directory_path = %s, storage_path = %s, document_key = %s "
                "WHERE id = %s",
                [directory_path, storage_path, document_key, document_id],
            )
