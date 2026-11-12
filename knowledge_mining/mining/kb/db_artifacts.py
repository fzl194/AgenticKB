"""KB 知识制品（61号）—— kb_artifacts 六表异步仓储。

风格契约与 mining/kb/db.py 一致：TEXT id、参数化 SQL、dict_row、
多语句显式 ``conn.transaction()``、每方法自开连接（一个事务）。

机制权威 = docs/下一阶段/61号 §四（发布事务 / ensure_draft / diff）：
- 行地址 row_key = 业务主键（ordinal 仅展示）；jsonb 三字段分工：
  data=业务值 / provenance=来源账（数组） / conflicts=质量标记，永不混装。
- 发布事务三件事原子：rev0→revN 复制定格 + schema 快照 + 清 draft 拨指针；
  类型化表达式索引在事务成功后重建（失败不回滚发布本身，下次发布自愈）。
- ensure_draft 用 kb_artifacts.draft_open 显式状态（FALSE 且 current>0 才
  checkout 复制），避免「检出后删光」被误判需要复活。
"""
from __future__ import annotations

import binascii
import json
import logging
import uuid
from typing import Any

from psycopg import sql

logger = logging.getLogger(__name__)

#: 单批多行 INSERT 的行数（10 万行上限 ÷ 1000/批；参数量 8×1000 远低于 65535）。
_ROW_INSERT_BATCH = 1000

#: list_rows 的页大小护栏。
_MAX_PAGE_SIZE = 500

#: diff 样例截断（counts 恒全量，added/removed/changed 各只回前 N 条）。
_DIFF_SAMPLE_LIMIT = 100

_VALID_KINDS = {"table", "wiki"}
_VALID_MEMBER_ROLES = {"owner", "editor", "viewer"}
_VALID_COLUMN_TYPES = {"text", "int", "float", "date", "enum", "bool"}

#: batch_assign / 行过滤允许的操作词表（列名恒为参数，无拼接注入面）。
_VALID_FILTER_OPS = {"eq", "ne", "in", "contains", "is_null", "not_null"}


def _new_artifact_id() -> str:
    """art_ 前缀 + hex——serving/MCP 侧按前缀解析 ref（61号 Task 7 契约）。"""
    return f"art_{uuid.uuid4().hex}"


def _json(obj: Any) -> str:
    if obj is None:
        return "null"
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _encode_key_parts(parts: list[str]) -> str:
    """主键值段 → UTF-8 hex 编码、'.' 连接（无歧义可逆）：单列 ``414243``
    （"ABC"），多列 ``4d4f502d31.41``。codex三审批号：网关
    （main_control_service/auth.py _has_unsafe_proxy_path）对代理路径拒绝一切
    % 编码字节——旧长度前缀 ``3:ABC`` 经前端 encodeURIComponent 变 ``3%3AABC``
    即被网关 400，中文主键值同理必炸；hex 输出恒匹配 ``[0-9a-f.]``，是
    encodeURIComponent 不转义的 URL 安全 ASCII 字符集（'.' 同安全），任何
    Unicode/``/``/``:``/``%`` 主键值都不会触发编码。导入向导
    artifact_import_service._row_key 复用同一编码，保证追加 UPSERT 与
    set_primary_key 重算的键同构。展示不受影响（data 里主键原值可见）。"""
    return ".".join(
        binascii.hexlify(str(s).encode("utf-8")).decode("ascii") for s in parts
    )


def _row_key_value(data: dict[str, Any], column_ids: list[str]) -> str:
    """主键值拼接：None→空串（hex 空串）；多列 hex 编码 '.' 连接（见
    _encode_key_parts）；无主键由调用方生成 uuid。"""
    if not column_ids:
        return uuid.uuid4().hex
    return _encode_key_parts(
        "" if data.get(c) is None else str(data.get(c)) for c in column_ids
    )


class ArtifactError(RuntimeError):
    """仓储层不变量冲突（发布门禁 / 主键冲突 / 非法状态）。service 负责翻译。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ArtifactDB:
    """kb_artifacts / kb_artifact_revisions / kb_artifact_members /
    kb_artifact_batches / kb_table_columns / kb_table_rows 仓储。

    构造入参为 row_factory=dict_row 的 psycopg AsyncConnectionPool。
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # ------------------------------------------------------------ 制品登记

    async def create_artifact(
        self,
        *,
        artifact_id: str | None = None,
        domain_id: str,
        kind: str = "table",
        title: str,
        description: str = "",
        owner_user_id: str,
        visibility: str = "private",
    ) -> dict[str, Any]:
        if kind not in _VALID_KINDS:
            raise ArtifactError("invalid_kind")
        if visibility not in ("private", "public"):
            raise ArtifactError("invalid_visibility")
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """INSERT INTO kb_artifacts
                       (artifact_id, domain_id, kind, title, description,
                        owner_user_id, visibility, status, current_revision,
                        draft_open)
                       VALUES (%(id)s, %(dom)s, %(kind)s, %(title)s, %(desc)s,
                               %(owner)s, %(vis)s, 'draft', 0, FALSE)
                       RETURNING artifact_id, domain_id, kind, title, description,
                                 usage_note, owner_user_id, visibility, status,
                                 current_revision, draft_open, created_at, updated_at""",
                    {
                        "id": artifact_id or _new_artifact_id(),
                        "dom": domain_id, "kind": kind, "title": title,
                        "desc": description, "owner": owner_user_id,
                        "vis": visibility,
                    },
                )
                row = dict(await cur.fetchone())  # type: ignore[arg-type]
                await conn.execute(
                    """INSERT INTO kb_artifact_members (artifact_id, user_id, role)
                       VALUES (%s, %s, 'owner')""",
                    (row["artifact_id"], owner_user_id),
                )
                return row

    async def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT artifact_id, domain_id, kind, title, description,
                          usage_note, owner_user_id, visibility, status,
                          current_revision, draft_open, created_at, updated_at
                   FROM kb_artifacts WHERE artifact_id = %s""",
                (artifact_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_artifact_meta(
        self,
        artifact_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        usage_note: str | None = None,
        visibility: str | None = None,
    ) -> dict[str, Any] | None:
        """PATCH 语义：None=未提供不动（显式清空传空串）。恒 bump updated_at。"""
        sets: list[str] = []
        params: dict[str, Any] = {"id": artifact_id}
        if title is not None:
            params["t"] = title
            sets.append("title = %(t)s")
        if description is not None:
            params["d"] = description
            sets.append("description = %(d)s")
        if usage_note is not None:
            params["u"] = usage_note
            sets.append("usage_note = %(u)s")
        if visibility is not None:
            params["v"] = visibility
            sets.append("visibility = %(v)s")
        if not sets:
            return await self.get_artifact(artifact_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE kb_artifacts SET " + ", ".join(sets)
                + ", updated_at = now() WHERE artifact_id = %(id)s"
                " RETURNING artifact_id, domain_id, kind, title, description,"
                " usage_note, owner_user_id, visibility, status, current_revision,"
                " draft_open, created_at, updated_at",
                params,
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def touch_updated_at(self, artifact_id: str) -> None:
        """设计态编辑后的乐观锁令牌推进（updated_at 即 ETag 源）。"""
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE kb_artifacts SET updated_at = now() WHERE artifact_id = %s",
                (artifact_id,),
            )

    async def delete_artifact(self, artifact_id: str) -> None:
        """owner 硬删（service 层已门禁 status=deprecated）：显式级联（列约定
        无 FK，删除顺序自底向上；钥匙开放行无 artifact 侧 FK，一并显式清）。
        63号批次2补批次1遗留缺口①：wiki 四表（units→links→pages 自底向上）
        与通用来源表 kb_artifact_sources 同入级联——对 table 制品为空删零
        副作用（制品删除是 units 唯一清理点，批次2决策①按版本保留不随发布删）。
        级联后清本制品动态表达式索引（对抗审查修复⑤：idx_kbtr_{art}_* 不随
        行表清空）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM kb_table_rows WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM kb_table_columns WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM kb_artifact_batches WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM kb_wiki_page_units WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM kb_wiki_page_links WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM kb_wiki_pages WHERE artifact_id = %s",
                    (artifact_id,),
                )
                # 66号批次1审查 I-1：toc 草稿行同入级联（无 FK 的清理责任在
                # 管线；对非 wiki 制品为空删零副作用）。
                await conn.execute(
                    "DELETE FROM kb_wiki_tocs WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM kb_artifact_sources WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM mcp_key_open_artifacts WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM kb_artifact_members WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM kb_artifact_revisions WHERE artifact_id = %s",
                    (artifact_id,),
                )
                await conn.execute(
                    "DELETE FROM kb_artifacts WHERE artifact_id = %s",
                    (artifact_id,),
                )
        await self.drop_draft_expression_indexes(artifact_id)

    async def list_artifacts_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """成员视角列表（含 owner 不落 members 行的兜底）；site/domain admin 全量
        走 list_all_artifacts（service 层分派，不过滤）。

        codex三审批号 P2-2：加当前域绑定过滤——被撤域的用户即使残留成员行/
        owner 身份，制品也不再出现在列表（与详情 404 反探测口径对齐；
        admin 路径在 service 层已分流到 list_all_artifacts，不受影响）。

        008 权限模型：public 制品对本域绑定用户开放——非成员出现在列表，
        my_role 合成为 'viewer'（只读；写权限仍走成员行，前端 canWrite 判定
        owner/editor 不受影响）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT a.artifact_id, a.domain_id, a.kind, a.title, a.description,
                          a.usage_note, a.owner_user_id, a.visibility, a.status,
                          a.current_revision, a.draft_open, a.created_at, a.updated_at,
                          CASE WHEN a.owner_user_id = %(u)s THEN 'owner'
                               WHEN m.role IS NOT NULL THEN m.role
                               WHEN a.visibility = 'public' THEN 'viewer'
                               ELSE NULL END AS my_role
                   FROM kb_artifacts a
                   LEFT JOIN kb_artifact_members m
                          ON m.artifact_id = a.artifact_id AND m.user_id = %(u)s
                   WHERE (a.owner_user_id = %(u)s OR m.user_id IS NOT NULL
                          OR a.visibility = 'public')
                     AND EXISTS (SELECT 1 FROM user_domains ud
                                 WHERE ud.user_id = %(u)s
                                   AND ud.domain = a.domain_id)
                   ORDER BY a.created_at""",
                {"u": user_id},
            )
            return [dict(r) for r in await cur.fetchall()]

    async def list_all_artifacts(
        self, domain_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """admin 全量列表（可选按域收窄）。"""
        clause, params = "", []
        if domain_id is not None:
            clause = " WHERE a.domain_id = %s"
            params.append(domain_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT a.artifact_id, a.domain_id, a.kind, a.title, a.description,
                          a.usage_note, a.owner_user_id, a.visibility, a.status,
                          a.current_revision, a.draft_open, a.created_at, a.updated_at
                   FROM kb_artifacts a""" + clause + " ORDER BY a.created_at",
                params,
            )
            return [dict(r) for r in await cur.fetchall()]

    async def readable_artifact_domain(
        self, artifact_id: str, user_id: str,
    ) -> str | None:
        """钥匙开放集候选校验：仅已发布且用户可读的制品返回 domain_id；
        草稿、已停用、不可读或不存在的制品返回 None。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT a.domain_id
                   FROM kb_artifacts a
                   WHERE a.artifact_id = %(a)s
                     AND a.status = 'published'
                     AND (EXISTS (SELECT 1 FROM kb_users u
                                  WHERE u.id = %(u)s AND u.status = 'active'
                                    AND u.deleted_at IS NULL AND u.site_role = 'admin')
                          OR (EXISTS (SELECT 1 FROM user_domains ud
                                      WHERE ud.user_id = %(u)s
                                        AND ud.domain = a.domain_id)
                              AND (a.owner_user_id = %(u)s OR a.visibility = 'public'
                                   OR EXISTS (SELECT 1 FROM kb_artifact_members m
                                              WHERE m.artifact_id = a.artifact_id
                                                AND m.user_id = %(u)s)
                                   OR EXISTS (SELECT 1 FROM user_domains ud
                                              JOIN kb_users u ON u.id = ud.user_id
                                              WHERE ud.user_id = %(u)s
                                                AND ud.domain = a.domain_id
                                                AND ud.domain_role = 'admin'
                                                AND u.status = 'active'
                                                AND u.deleted_at IS NULL))))""",
                {"a": artifact_id, "u": user_id},
            )
            row = await cur.fetchone()
            return str(row["domain_id"]) if row else None

    # ---------------------------------------------------------------- 成员

    async def list_members(self, artifact_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT m.user_id, m.role, u.username,
                          COALESCE(NULLIF(u.display_name, ''), u.username) AS user_name
                   FROM kb_artifact_members m
                   LEFT JOIN kb_users u ON u.id = m.user_id
                   WHERE m.artifact_id = %s
                   ORDER BY m.role, u.username""",
                (artifact_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def upsert_member(
        self, artifact_id: str, user_id: str, role: str,
    ) -> dict[str, Any]:
        if role not in _VALID_MEMBER_ROLES:
            raise ArtifactError("invalid_member_role")
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO kb_artifact_members (artifact_id, user_id, role)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (artifact_id, user_id) DO UPDATE SET role = EXCLUDED.role
                   RETURNING artifact_id, user_id, role""",
                (artifact_id, user_id, role),
            )
            return dict(await cur.fetchone())  # type: ignore[arg-type]

    async def remove_member(self, artifact_id: str, user_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM kb_artifact_members WHERE artifact_id = %s AND user_id = %s",
                (artifact_id, user_id),
            )

    async def artifact_role(self, artifact_id: str, user_id: str) -> str | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT role FROM kb_artifact_members"
                " WHERE artifact_id = %s AND user_id = %s",
                (artifact_id, user_id),
            )
            row = await cur.fetchone()
            return str(row["role"]) if row else None

    async def get_user_status(self, user_id: str) -> str | None:
        """成员资格预检（对抗审查修复⑪）：kb_users 行存在且未软删 → status；
        不存在/已软删 → None。是否 active 由 service 判定。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT status FROM kb_users WHERE id = %s AND deleted_at IS NULL",
                (user_id,),
            )
            row = await cur.fetchone()
            return str(row["status"]) if row else None

    async def is_domain_admin(self, user_id: str, domain_id: str) -> bool:
        """口径同 kb/db.py can_manage_domain：active site admin 全通 / 本域
        domain_role='admin'。以库为准（不信 user 对象缓存的域列表）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 1 FROM kb_users u
                   WHERE u.id = %(uid)s AND u.status = 'active'
                     AND u.deleted_at IS NULL
                     AND (u.site_role = 'admin'
                          OR EXISTS (SELECT 1 FROM user_domains ud
                                     WHERE ud.user_id = u.id
                                       AND ud.domain = %(dom)s
                                       AND ud.domain_role = 'admin'))""",
                {"uid": user_id, "dom": domain_id},
            )
            return await cur.fetchone() is not None

    async def can_create_in_domain(self, user_id: str, domain_id: str) -> bool:
        """创建资格（61号创建域门禁）：active site admin 全通；其余须有该域
        user_domains 绑定（member / domain admin 任一即放行）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT 1 FROM kb_users u
                   WHERE u.id = %(uid)s AND u.status = 'active'
                     AND u.deleted_at IS NULL
                     AND (u.site_role = 'admin'
                          OR EXISTS (SELECT 1 FROM user_domains ud
                                     WHERE ud.user_id = u.id
                                       AND ud.domain = %(dom)s))""",
                {"uid": user_id, "dom": domain_id},
            )
            return await cur.fetchone() is not None

    async def first_domain_for_user(self, user_id: str) -> str | None:
        """用户首个绑定域（创建向导 domain_id 缺省回落；确定性=字典序）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT ud.domain FROM user_domains ud
                   JOIN kb_users u ON u.id = ud.user_id
                   WHERE ud.user_id = %s AND u.status = 'active'
                     AND u.deleted_at IS NULL
                   ORDER BY ud.domain LIMIT 1""",
                (user_id,),
            )
            row = await cur.fetchone()
            return str(row["domain"]) if row else None

    async def user_in_domain(self, user_id: str, domain_id: str) -> bool:
        """当前域绑定存在性（codex二审批号⑧）：非 admin 成员路径的域外拦截
        依据——撤销域绑定后成员行残留不得继续放行（404 反探测口径，
        与 KB 面硬边界对齐）。admin 路径不走此检查（site/domain admin 自证）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM user_domains WHERE user_id = %s AND domain = %s",
                (user_id, domain_id),
            )
            return await cur.fetchone() is not None

    # ------------------------------------------------------------ 生命周期

    async def publish(
        self, artifact_id: str, change_summary: str, published_by: str,
        expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """发布事务（61号 §4.2）：①rev0→revN 复制定格 ②schema 快照+版本记录
        ③清 draft+拨指针+状态。usage_note 非空由 service 先拦、此处兜底（竞态安全）。

        draft 门禁（对抗审查修复③）：draft_open=FALSE 且 current>0 = 干净态，
        无待发布变更 → no_pending_changes（service 翻 409）。首次发布
        （current=0）不受此门禁——rev0 即首发工作区。

        键归一（codex三审 P1-2b）：复制 rev0→N+1 之前，对每个 active 列的每个
        alias 旧名迁键到当前列名（行同时含新旧键时保留新键值、仅清旧键，见
        _RENAME_DATA_KEYS_SQL 的 COALESCE 口径）。发布=键与 schema 对齐的定格
        点：discard 后 checkout 回来的 rev0 行可能仍是旧键（上一版快照键名），
        不归一会产生 schema=新名、数据键=旧名的断裂版本。

        expected_updated_at（codex三审 P1-4）：本方法开头即 FOR UPDATE 取行，
        取行后立即全精度比对 updated_at（失配 stale_artifact）——并发权威校验，
        service 层 _check_freshness 只是前置快检。

        new_rev 取 max(current, 历史最大 revision)+1——回滚拨低指针后重发布
        不得与既有版本行撞主键（「数据不动」语义下历史版本仍留在表里）。
        返回 {"revision", "row_count"}；索引重建在事务外自愈式执行。
        """
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT * FROM kb_artifacts WHERE artifact_id = %s FOR UPDATE",
                    (artifact_id,),
                )
                art = await cur.fetchone()
                if art is None:
                    raise ArtifactError("artifact_not_found")
                if expected_updated_at is not None \
                        and art["updated_at"] != expected_updated_at:
                    raise ArtifactError("stale_artifact")
                if art["status"] not in ("draft", "published", "deprecated"):
                    raise ArtifactError("invalid_status")
                if not (art["usage_note"] or "").strip():
                    raise ArtifactError("usage_note_required")
                if not art["draft_open"] and int(art["current_revision"]) > 0:
                    raise ArtifactError("no_pending_changes")
                cur = await conn.execute(
                    """SELECT GREATEST(
                           %(cur)s,
                           COALESCE((SELECT MAX(revision) FROM kb_artifact_revisions
                                     WHERE artifact_id = %(a)s), 0),
                           COALESCE((SELECT MAX(revision) FROM kb_table_rows
                                     WHERE artifact_id = %(a)s AND revision > 0), 0)
                       ) + 1 AS new_rev""",
                    {"cur": art["current_revision"], "a": artifact_id},
                )
                new_rev = int((await cur.fetchone())["new_rev"])  # type: ignore[index]
                cur = await conn.execute(
                    """SELECT column_id, column_name, column_type, enum_values,
                              is_primary_key, is_indexed, ordinal, alias
                       FROM kb_table_columns
                       WHERE artifact_id = %s AND status = 'active'
                       ORDER BY ordinal, column_id""",
                    (artifact_id,),
                )
                columns = [dict(r) for r in await cur.fetchall()]
                for col in columns:
                    current_name = str(col["column_name"])
                    for alias_name in (col.get("alias") or []):
                        alias_name = str(alias_name)
                        if alias_name and alias_name != current_name:
                            await self._rename_data_keys(
                                conn, artifact_id, alias_name, current_name,
                            )
                cur = await conn.execute(
                    """INSERT INTO kb_table_rows
                       (artifact_id, revision, row_key, data, provenance, conflicts,
                        ordinal, updated_at, updated_by)
                       SELECT artifact_id, %(r)s, row_key, data, provenance,
                              conflicts, ordinal, now(), %(by)s
                       FROM kb_table_rows
                       WHERE artifact_id = %(a)s AND revision = 0""",
                    {"r": new_rev, "by": published_by, "a": artifact_id},
                )
                # INSERT...SELECT 无 RETURNING：rowcount 才是行数（fetchval 拿不到）
                row_count = int(cur.rowcount or 0)
                await conn.execute(
                    """INSERT INTO kb_artifact_revisions
                       (artifact_id, revision, schema_json, row_count,
                        change_summary, published_by)
                       VALUES (%s, %s, %s::jsonb, %s, %s, %s)""",
                    (artifact_id, new_rev, _json(columns), row_count,
                     change_summary, published_by),
                )
                await conn.execute(
                    "DELETE FROM kb_table_rows WHERE artifact_id = %s AND revision = 0",
                    (artifact_id,),
                )
                await conn.execute(
                    """UPDATE kb_artifacts
                       SET current_revision = %s, status = 'published',
                           draft_open = FALSE, updated_at = now()
                       WHERE artifact_id = %s""",
                    (new_rev, artifact_id),
                )
        # 事务成功后（新连接）：重建类型化索引。draft 不建索引（编辑频繁）。
        await self.drop_draft_expression_indexes(artifact_id)
        indexed = [c for c in columns if c.get("is_indexed")]
        await self.create_expression_indexes(artifact_id, new_rev, indexed)
        return {"revision": new_rev, "row_count": row_count}

    async def get_revision(
        self, artifact_id: str, revision: int,
    ) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT artifact_id, revision, schema_json, row_count,
                          change_summary, published_by, published_at
                   FROM kb_artifact_revisions
                   WHERE artifact_id = %s AND revision = %s""",
                (artifact_id, revision),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_revisions(self, artifact_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT revision, row_count, change_summary, published_by,
                          published_at
                   FROM kb_artifact_revisions
                   WHERE artifact_id = %s ORDER BY revision DESC""",
                (artifact_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def rollback_to(
        self, artifact_id: str, revision: int, expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """拨指针（数据不动）。仅校验目标版本存在于 kb_artifact_revisions——
        「数据不动」语义下拨任何历史版（含 > current，回滚往返）都安全；
        历史版本行留在 kb_table_rows / kb_artifact_revisions，之后重发布会
        接到 max+1（对抗审查修复④：删去 revision > current 拒绝判据）。
        expected_updated_at：FOR UPDATE 取行后立即比对（codex三审 P1-4）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT * FROM kb_artifacts WHERE artifact_id = %s FOR UPDATE",
                    (artifact_id,),
                )
                art = await cur.fetchone()
                if art is None:
                    raise ArtifactError("artifact_not_found")
                if expected_updated_at is not None \
                        and art["updated_at"] != expected_updated_at:
                    raise ArtifactError("stale_artifact")
                revision = int(revision)
                if revision < 1:
                    raise ArtifactError("invalid_revision")
                cur = await conn.execute(
                    "SELECT 1 FROM kb_artifact_revisions"
                    " WHERE artifact_id = %s AND revision = %s",
                    (artifact_id, revision),
                )
                if await cur.fetchone() is None:
                    raise ArtifactError("invalid_revision")
                cur = await conn.execute(
                    """UPDATE kb_artifacts
                       SET current_revision = %s, status = 'published', updated_at = now()
                       WHERE artifact_id = %s
                       RETURNING artifact_id, domain_id, kind, title, description,
                                 usage_note, owner_user_id, status, current_revision,
                                 draft_open, created_at, updated_at""",
                    (revision, artifact_id),
                )
                return dict(await cur.fetchone())  # type: ignore[arg-type]

    async def deprecate(
        self, artifact_id: str, expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """停用：draft/published → deprecated。008 起从未发布的草稿也可停用
        ——删除门禁=必须先停用，若 draft 不达 deprecated 则废稿永远删不掉。"""
        return await self._set_status(
            artifact_id, "deprecated", expected_updated_at,
        )

    async def republish(
        self, artifact_id: str, expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """deprecated → published（保持 current_revision，不产生新版本）；
        从未发布过（current_revision=0）的停用稿恢复回 draft。"""
        return await self._set_status(
            artifact_id, "published", expected_updated_at,
        )

    async def _set_status(
        self, artifact_id: str, target: str, expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """codex三审 P1-4：事务内 FOR UPDATE 取行 + updated_at 比对后再生效——
        旧实现裸 UPDATE 无锁内校验（service 前置快检与写之间存在竞态窗口）。

        008 状态机：deprecate 接受 draft/published 双源（废稿也要能停用→删除）；
        republish 按是否有发布版分流——从未发布回 draft，发布过回 published。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._assert_expected_updated_at(
                    conn, artifact_id, expected_updated_at,
                )
                if target == "deprecated":
                    cur = await conn.execute(
                        """UPDATE kb_artifacts
                           SET status = 'deprecated', updated_at = now()
                           WHERE artifact_id = %s AND status IN ('draft', 'published')
                           RETURNING artifact_id, domain_id, kind, title, description,
                                     usage_note, owner_user_id, visibility, status,
                                     current_revision, draft_open, created_at, updated_at""",
                        (artifact_id,),
                    )
                else:
                    cur = await conn.execute(
                        """UPDATE kb_artifacts
                           SET status = CASE WHEN current_revision > 0
                                             THEN 'published' ELSE 'draft' END,
                               updated_at = now()
                           WHERE artifact_id = %s AND status = 'deprecated'
                           RETURNING artifact_id, domain_id, kind, title, description,
                                     usage_note, owner_user_id, visibility, status,
                                     current_revision, draft_open, created_at, updated_at""",
                        (artifact_id,),
                    )
                row = await cur.fetchone()
                if row is None:
                    raise ArtifactError("invalid_status")
                return dict(row)

    async def discard_draft(
        self, artifact_id: str, expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """丢弃 draft：DELETE rev0 全部行 + draft_open=FALSE（乱改后的逃生门）。
        expected_updated_at：事务内 FOR UPDATE 取行后比对（codex三审 P1-4）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._assert_expected_updated_at(
                    conn, artifact_id, expected_updated_at,
                )
                await conn.execute(
                    "DELETE FROM kb_table_rows WHERE artifact_id = %s AND revision = 0",
                    (artifact_id,),
                )
                cur = await conn.execute(
                    """UPDATE kb_artifacts
                       SET draft_open = FALSE, updated_at = now()
                       WHERE artifact_id = %s
                       RETURNING artifact_id, domain_id, kind, title, description,
                                 usage_note, owner_user_id, status, current_revision,
                                 draft_open, created_at, updated_at""",
                    (artifact_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    raise ArtifactError("artifact_not_found")
                return dict(row)

    async def ensure_draft(self, artifact_id: str) -> dict[str, Any]:
        """设计态编辑统一入口（61号 §4.3）：draft_open=FALSE 且 current>0 →
        checkout（current→rev0 复制）再置 TRUE；draft_open=FALSE 且 current=0
        （从未发布）直接置 TRUE（rev0 即工作区）。幂等。

        codex二审批号⑥裁定：checkout **不推进 updated_at**——rev0 是 current
        的逐字节副本，用户可见内容未变；若推进，发布后首次编辑（携带发布时
        token）会在 db 层原子校验被自己的 ensure_draft touch 误杀（恒 409）。
        乐观锁令牌只随真实内容变更前进（写行/改列/发布族）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT * FROM kb_artifacts WHERE artifact_id = %s FOR UPDATE",
                    (artifact_id,),
                )
                art = await cur.fetchone()
                if art is None:
                    raise ArtifactError("artifact_not_found")
                if not art["draft_open"]:
                    if int(art["current_revision"]) > 0:
                        await conn.execute(
                            """INSERT INTO kb_table_rows
                               (artifact_id, revision, row_key, data, provenance,
                                conflicts, ordinal, updated_at, updated_by)
                               SELECT artifact_id, 0, row_key, data, provenance,
                                      conflicts, ordinal, now(), ''
                               FROM kb_table_rows
                               WHERE artifact_id = %s AND revision = %s""",
                            (artifact_id, art["current_revision"]),
                        )
                    cur = await conn.execute(
                        """UPDATE kb_artifacts
                           SET draft_open = TRUE
                           WHERE artifact_id = %s
                           RETURNING artifact_id, domain_id, kind, title, description,
                                     usage_note, owner_user_id, status,
                                     current_revision, draft_open, created_at,
                                     updated_at""",
                        (artifact_id,),
                    )
                    art = await cur.fetchone()
                return dict(art)  # type: ignore[arg-type]

    # --------------------------------------------------------------- schema

    async def list_columns(
        self, artifact_id: str, include_dropped: bool = False,
    ) -> list[dict[str, Any]]:
        """active 列（含 alias——61号用例⑨旧列名兼容读取）。"""
        clause = "" if include_dropped else " AND status = 'active'"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT artifact_id, column_id, column_name, column_type,
                          enum_values, is_primary_key, is_indexed, alias, status,
                          ordinal
                   FROM kb_table_columns WHERE artifact_id = %s""" + clause +
                " ORDER BY ordinal, column_id",
                (artifact_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def upsert_column(
        self, artifact_id: str, column_id: str, fields: dict[str, Any],
        *, rename_from: str | None = None,
        expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """PATCH 式列变更。主键变更必须走 set_primary_key（要重算 row_key），
        is_primary_key 不在白名单。

        codex二审两项：①enum_values/alias 走 ``%(key)s::jsonb`` 的分支必须
        传 _json(val)——原生 list 会被 psycopg 适配成 text[] ``'{old}'``，
        ::jsonb 非法；②列改名（rename_from=旧名）在同一事务内迁移 rev0 行
        data 键（用例⑨闭环：Java/前端按新 schema 键名读数，旧键残留=读空；
        已发布快照不回改——旧版快照旧键+旧 schema 自洽，读取侧经 alias 映射
        闭环，Java 无需改）。本方法恒推进 kb_artifacts.updated_at（乐观锁
        令牌随 schema 变更前进）。

        expected_updated_at（codex三审 P1-4）：事务内原子校验（FOR UPDATE +
        改名迁键 + touch 同事务）。"""
        allowed = {"column_name", "column_type", "enum_values", "is_indexed",
                   "ordinal", "alias"}
        sets: list[str] = []
        params: dict[str, Any] = {"a": artifact_id, "c": column_id}
        for col, val in fields.items():
            if col not in allowed:
                raise ArtifactError("invalid_column_field")
            if col == "column_type" and val not in _VALID_COLUMN_TYPES:
                raise ArtifactError("invalid_column_type")
            key = f"f{len(params)}"
            if col in ("enum_values", "alias"):
                sets.append(f"{col} = %({key})s::jsonb")
                params[key] = _json(val)
            else:
                sets.append(f"{col} = %({key})s")
                params[key] = val
        if rename_from is not None and "column_name" not in fields:
            raise ArtifactError("invalid_column_field")
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._assert_expected_updated_at(
                    conn, artifact_id, expected_updated_at,
                )
                cur = await conn.execute(
                    """INSERT INTO kb_table_columns
                       (artifact_id, column_id, column_name, column_type,
                        is_indexed, ordinal)
                       VALUES (%(a)s, %(c)s,
                               COALESCE(%(name)s, %(c)s), %(type)s, %(idx)s, %(ord)s)
                       ON CONFLICT (artifact_id, column_id) DO NOTHING""",
                    {
                        "a": artifact_id, "c": column_id,
                        "name": fields.get("column_name"),
                        "type": fields.get("column_type", "text"),
                        "idx": fields.get("is_indexed", False),
                        "ord": fields.get("ordinal", 0),
                    },
                )
                if sets:
                    cur = await conn.execute(
                        "UPDATE kb_table_columns SET " + ", ".join(sets)
                        + " WHERE artifact_id = %(a)s AND column_id = %(c)s",
                        params,
                    )
                if rename_from is not None:
                    await self._rename_data_keys(
                        conn, artifact_id, rename_from,
                        str(fields["column_name"]),
                    )
                await conn.execute(
                    "UPDATE kb_artifacts SET updated_at = now()"
                    " WHERE artifact_id = %s",
                    (artifact_id,),
                )
                cur = await conn.execute(
                    """SELECT artifact_id, column_id, column_name, column_type,
                              enum_values, is_primary_key, is_indexed, alias, status,
                              ordinal
                       FROM kb_table_columns
                       WHERE artifact_id = %s AND column_id = %s""",
                    (artifact_id, column_id),
                )
                row = await cur.fetchone()
                if row is None:
                    raise ArtifactError("invalid_column_field")
                return dict(row)

    async def rename_column_data_keys(
        self, artifact_id: str, old_name: str, new_name: str,
    ) -> int:
        """列改名后独立迁移 rev0 行 data 键（旧键值原样搬到新键）。常态路径
        经 upsert_column(rename_from=...) 在同一事务内完成；本方法留给补账/
        测试直调。只迁 rev0——已发布快照永不回改（见 upsert_column 注释）。
        返回迁移行数。

        codex三审 P1-2a：参数显式 ``::text``——psycopg3 参数默认 unknown
        类型，``-``/``->``/``?`` 运算符有 text/int 重载、jsonb_build_object 的
        key 类型推断不出，真库必炸/歧义。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                self._RENAME_DATA_KEYS_SQL,
                {"old": old_name, "new": new_name, "a": artifact_id},
            )
            return int(cur.rowcount or 0)

    #: 迁键 SQL（rename_column_data_keys / _rename_data_keys / publish 归一共用）。
    #: COALESCE 口径（codex三审 P1-2b）：行 data 同时含新旧键（discard 后
    #: checkout 旧快照、用户又按新 schema 编辑过该列）时保留**新键值**、
    #: 仅清除旧键——不让旧键值倒灌覆盖新编辑值，也不残留旧键。
    _RENAME_DATA_KEYS_SQL = """UPDATE kb_table_rows
                   SET data = (data - %(old)s::text)
                       || jsonb_build_object(
                            %(new)s::text,
                            COALESCE(data->%(new)s::text, data->%(old)s::text))
                   WHERE artifact_id = %(a)s AND revision = 0
                     AND data ? %(old)s::text"""

    @staticmethod
    async def _rename_data_keys(
        conn: Any, artifact_id: str, old_name: str, new_name: str,
    ) -> None:
        """upsert_column / publish 事务内共用的迁键 SQL（与
        rename_column_data_keys 同句；::text cast 与 COALESCE 口径见其注释）。
        只迁 rev0——已发布快照永不回改。"""
        await conn.execute(
            ArtifactDB._RENAME_DATA_KEYS_SQL,
            {"old": old_name, "new": new_name, "a": artifact_id},
        )

    async def drop_column(self, artifact_id: str, column_id: str) -> None:
        """软删（单调扩展铁律：动存量数据=不动）。同时清 is_primary_key 标记。"""
        async with self._pool.connection() as conn:
            await conn.execute(
                """UPDATE kb_table_columns
                   SET status = 'dropped', is_primary_key = FALSE
                   WHERE artifact_id = %s AND column_id = %s""",
                (artifact_id, column_id),
            )

    async def set_primary_key(
        self, artifact_id: str, column_ids: list[str],
        expected_updated_at: Any = None,
    ) -> dict[str, Any]:
        """改主键 = 重算 rev0 全部 row_key（61号 §6.3：有数据时独立确认流程）。
        新键冲突（两行折到同键）→ ArtifactError("primary_key_conflict") 整体回滚。
        column_ids 为空 = 无主键：每行换新 uuid（Excel 一次性数据语义）。
        expected_updated_at：FOR UPDATE 取行后立即比对（codex三审 P1-4）。
        """
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "SELECT * FROM kb_artifacts WHERE artifact_id = %s FOR UPDATE",
                    (artifact_id,),
                )
                art = await cur.fetchone()
                if art is None:
                    raise ArtifactError("artifact_not_found")
                if expected_updated_at is not None \
                        and art["updated_at"] != expected_updated_at:
                    raise ArtifactError("stale_artifact")
                cur = await conn.execute(
                    """SELECT column_id, column_name FROM kb_table_columns
                       WHERE artifact_id = %s AND status = 'active'""",
                    (artifact_id,),
                )
                # data 键 = column_name（不是 column_id）——重算键必须先做 id→name
                # 映射（对抗审查修复②：曾直接拿 column_id 取 data，恒得空键）。
                id_to_name = {
                    str(r["column_id"]): str(r["column_name"])
                    for r in await cur.fetchall()
                }
                unknown = [c for c in column_ids if c not in id_to_name]
                if unknown:
                    raise ArtifactError("unknown_column")
                pk_names = [id_to_name[c] for c in column_ids]
                cur = await conn.execute(
                    """SELECT row_key, data, provenance, conflicts, ordinal,
                              updated_by
                       FROM kb_table_rows
                       WHERE artifact_id = %s AND revision = 0 FOR UPDATE""",
                    (artifact_id,),
                )
                rows = [dict(r) for r in await cur.fetchall()]
                remap: dict[str, str] = {}
                seen: set[str] = set()
                for row in rows:
                    new_key = _row_key_value(row["data"], pk_names)
                    if new_key in seen:
                        raise ArtifactError("primary_key_conflict")
                    seen.add(new_key)
                    if new_key != row["row_key"]:
                        remap[row["row_key"]] = new_key
                # 先删后插：避免两行 row_key 互换时撞主键
                if remap:
                    await conn.execute(
                        """DELETE FROM kb_table_rows
                           WHERE artifact_id = %s AND revision = 0
                             AND row_key = ANY(%s)""",
                        (artifact_id, list(remap)),
                    )
                    await self._reinsert_remapped(conn, artifact_id, rows, remap)
                await conn.execute(
                    """UPDATE kb_table_columns
                       SET is_primary_key = (column_id = ANY(%s))
                       WHERE artifact_id = %s""",
                    (list(column_ids), artifact_id),
                )
                await conn.execute(
                    "UPDATE kb_artifacts SET updated_at = now() WHERE artifact_id = %s",
                    (artifact_id,),
                )
                return {"artifact_id": artifact_id,
                        "primary_key": list(column_ids),
                        "rekeyed_rows": len(remap)}

    @staticmethod
    async def _reinsert_remapped(
        conn: Any, artifact_id: str,
        rows: list[dict[str, Any]], remap: dict[str, str],
    ) -> None:
        """把被改键的行按新 row_key 原样重插（codex二审批号③：SELECT 已补全
        provenance/conflicts/ordinal/updated_by——重插完整保留来源账与质量标记，
        只有 row_key 换新、updated_at=now()）。"""
        to_insert = [
            {
                "row_key": remap[r["row_key"]],
                "data": _json(r["data"]),
                "provenance": _json(r.get("provenance") or []),
                "conflicts": _json(r.get("conflicts") or {}),
                "ordinal": r.get("ordinal") or 0,
                "updated_by": str(r.get("updated_by") or ""),
            }
            for r in rows if r["row_key"] in remap
        ]
        for start in range(0, len(to_insert), _ROW_INSERT_BATCH):
            chunk = to_insert[start:start + _ROW_INSERT_BATCH]
            flat: list[Any] = []
            for item in chunk:
                flat.extend([
                    artifact_id, item["row_key"], item["data"],
                    item["provenance"], item["conflicts"], item["ordinal"],
                    item["updated_by"],
                ])
            await conn.execute(
                """INSERT INTO kb_table_rows
                   (artifact_id, revision, row_key, data, provenance, conflicts,
                    ordinal, updated_at, updated_by)
                   VALUES """ + ", ".join(
                       ["(%s, 0, %s, %s, %s, %s, %s, now(), %s)"] * len(chunk),
                   ),
                flat,
            )

    async def replace_columns_for_artifact(
        self, artifact_id: str, columns: list[dict[str, Any]],
        expected_updated_at: Any = None,
    ) -> None:
        """首次导入建列（删旧插新；draft 态，无历史列则等价全建）。
        codex二审批号⑥c：schema 变更路径必须推进 updated_at——否则旧乐观锁
        令牌永远有效。expected_updated_at：事务内原子校验（codex三审 P1-4）。"""
        for col in columns:
            if col.get("column_type") not in _VALID_COLUMN_TYPES:
                raise ArtifactError("invalid_column_type")
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._assert_expected_updated_at(
                    conn, artifact_id, expected_updated_at,
                )
                await conn.execute(
                    "DELETE FROM kb_table_columns WHERE artifact_id = %s",
                    (artifact_id,),
                )
                for col in columns:
                    await conn.execute(
                        """INSERT INTO kb_table_columns
                           (artifact_id, column_id, column_name, column_type,
                            enum_values, is_primary_key, is_indexed, alias, ordinal)
                           VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s)""",
                        (artifact_id, col["column_id"], col["column_name"],
                         col["column_type"], _json(col.get("enum_values")),
                         bool(col.get("is_primary_key", False)),
                         bool(col.get("is_indexed", False)),
                         _json(col.get("alias") or []), int(col.get("ordinal", 0))),
                    )
                await self._touch_in_conn(conn, artifact_id)

    # ------------------------------------------------------------------ 行

    async def insert_rows_upsert(
        self, artifact_id: str, revision: int, rows: list[dict[str, Any]],
        updated_by: str = "", expected_updated_at: Any = None,
    ) -> dict[str, int]:
        """按 row_key UPSERT（61号 §6 追加导入）：命中→data 覆盖+provenance
        数组拼接+conflicts 覆盖；未命中插入。多行 VALUES 分批 1000/批，
        RETURNING (xmax=0) 区分 inserted/updated。
        expected_updated_at（codex三审 P1-4，手工行增路径）：事务内 FOR UPDATE
        取行+比对后再写——整批原子（校验+写入同事务，多批同进一事务）。
        """
        inserted = updated = 0
        flat_rows = [
            {
                "row_key": r["row_key"],
                "data": _json(r.get("data") or {}),
                "provenance": _json(r.get("provenance") or []),
                "conflicts": _json(r.get("conflicts") or {}),
                "ordinal": int(r.get("ordinal", 0)),
            }
            for r in rows
        ]
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._assert_expected_updated_at(
                    conn, artifact_id, expected_updated_at,
                )
                for start in range(0, len(flat_rows), _ROW_INSERT_BATCH):
                    chunk = flat_rows[start:start + _ROW_INSERT_BATCH]
                    # 8 占位符 = 8 列（artifact_id..updated_by）；与 flat.extend 一一对应
                    # （对抗审查修复①：曾漏 2 个占位符，真库必炸——静态测试钉死）。
                    values_sql = ", ".join(
                        ["(%s, %s, %s, %s, %s, %s, %s, %s)"] * len(chunk),
                    )
                    flat: list[Any] = []
                    for item in chunk:
                        flat.extend([
                            artifact_id, revision, item["row_key"], item["data"],
                            item["provenance"], item["conflicts"], item["ordinal"],
                            updated_by,
                        ])
                    cur = await conn.execute(
                        """INSERT INTO kb_table_rows
                           (artifact_id, revision, row_key, data, provenance, conflicts,
                            ordinal, updated_by)
                           VALUES """ + values_sql + """
                           ON CONFLICT (artifact_id, revision, row_key) DO UPDATE SET
                               data = EXCLUDED.data,
                               provenance = kb_table_rows.provenance || EXCLUDED.provenance,
                               conflicts = EXCLUDED.conflicts,
                               ordinal = EXCLUDED.ordinal,
                               updated_at = now(),
                               updated_by = EXCLUDED.updated_by
                           RETURNING (xmax = 0) AS _inserted""",
                        flat,
                    )
                    for hit in await cur.fetchall():
                        if hit["_inserted"]:
                            inserted += 1
                        else:
                            updated += 1
        return {"inserted": inserted, "updated": updated}

    async def _assert_expected_updated_at(
        self, conn: Any, artifact_id: str, expected_updated_at: Any,
    ) -> None:
        """写路径乐观锁权威校验（codex二审批号⑥）：FOR UPDATE 锁 kb_artifacts
        行 → 全精度比对 updated_at（PG TIMESTAMPTZ 微秒）→ 失配 stale_artifact。
        service 层 _check_freshness 只是前置快检（体验），此处才是并发权威。"""
        if expected_updated_at is None:
            return
        cur = await conn.execute(
            "SELECT updated_at FROM kb_artifacts WHERE artifact_id = %s FOR UPDATE",
            (artifact_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise ArtifactError("artifact_not_found")
        if row["updated_at"] != expected_updated_at:
            raise ArtifactError("stale_artifact")

    @staticmethod
    async def _touch_in_conn(conn: Any, artifact_id: str) -> None:
        await conn.execute(
            "UPDATE kb_artifacts SET updated_at = now() WHERE artifact_id = %s",
            (artifact_id,),
        )

    async def update_cell(
        self, artifact_id: str, row_key: str, column: str,
        value: Any, updated_by: str, expected_updated_at: Any = None,
    ) -> dict[str, Any] | None:
        """单格更新（jsonb_set），只动 rev0。行不存在返回 None。
        expected_updated_at 见 _assert_expected_updated_at（原子校验+touch 同事务）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._assert_expected_updated_at(
                    conn, artifact_id, expected_updated_at)
                cur = await conn.execute(
                    """UPDATE kb_table_rows
                       SET data = jsonb_set(data, ARRAY[%s], %s::jsonb),
                           updated_at = now(), updated_by = %s
                       WHERE artifact_id = %s AND revision = 0 AND row_key = %s
                       RETURNING row_key, data, provenance, conflicts, ordinal""",
                    (column, _json(value), updated_by, artifact_id, row_key),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                await self._touch_in_conn(conn, artifact_id)
                return dict(row)

    async def update_cell_conflict(
        self, artifact_id: str, row_key: str, column: str,
        marker: str, updated_by: str, expected_updated_at: Any = None,
    ) -> dict[str, Any] | None:
        """类型校验失败的单格更新（§7.2，与导入 tolerant 路径对称）：该格置
        null + conflicts[column]=marker，不阻断。行不存在返回 None。
        expected_updated_at 见 _assert_expected_updated_at。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._assert_expected_updated_at(
                    conn, artifact_id, expected_updated_at)
                cur = await conn.execute(
                    """UPDATE kb_table_rows
                       SET data = jsonb_set(data, ARRAY[%s], 'null'::jsonb),
                           conflicts = conflicts || jsonb_build_object(%s, %s),
                           updated_at = now(), updated_by = %s
                       WHERE artifact_id = %s AND revision = 0 AND row_key = %s
                       RETURNING row_key, data, provenance, conflicts, ordinal""",
                    (column, column, marker, updated_by, artifact_id, row_key),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                await self._touch_in_conn(conn, artifact_id)
                return dict(row)

    async def get_row(
        self, artifact_id: str, revision: int, row_key: str,
    ) -> dict[str, Any] | None:
        """单行读取（行增端点冲突预检与回读）。"""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT row_key, data, provenance, conflicts, ordinal
                   FROM kb_table_rows
                   WHERE artifact_id = %s AND revision = %s AND row_key = %s""",
                (artifact_id, revision, row_key),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def batch_assign(
        self, artifact_id: str, column: str, value: Any,
        where_clauses: list[dict[str, Any]], updated_by: str,
        expected_updated_at: Any = None,
    ) -> int:
        """批量赋值一条 SQL：data || '{"列":"值"}'（61号 §7.2）。返回命中行数。
        where_clauses=[{column, op, value}]，op 白名单见 _VALID_FILTER_OPS，
        列名与值全参数化。expected_updated_at 见 _assert_expected_updated_at。
        """
        conds, filter_params = self._build_row_filters(where_clauses)
        # 占位符顺序 = SQL 出现顺序：SET 的 jsonb / updated_by / WHERE 的 artifact_id
        params = [_json({column: value}), updated_by, artifact_id, *filter_params]
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._assert_expected_updated_at(
                    conn, artifact_id, expected_updated_at)
                cur = await conn.execute(
                    """UPDATE kb_table_rows
                       SET data = data || %s::jsonb, updated_at = now(),
                           updated_by = %s
                       WHERE artifact_id = %s AND revision = 0""" + conds,
                    params,
                )
                await self._touch_in_conn(conn, artifact_id)
                return int(cur.rowcount or 0)

    @staticmethod
    def _build_row_filters(where_clauses: list[dict[str, Any]]) -> tuple[str, list[Any]]:
        if not where_clauses:
            return "", []
        parts: list[str] = []
        params: list[Any] = []
        for clause in where_clauses:
            op = str(clause.get("op", "eq"))
            if op not in _VALID_FILTER_OPS:
                raise ArtifactError("invalid_filter_op")
            col = clause.get("column")
            if not col or not isinstance(col, str):
                raise ArtifactError("invalid_filter_column")
            val = clause.get("value")
            if op == "is_null":
                parts.append("NOT (data ? %s)")
                params.append(col)
            elif op == "not_null":
                parts.append("(data ? %s)")
                params.append(col)
            elif op == "in":
                if not isinstance(val, list):
                    raise ArtifactError("invalid_filter_value")
                parts.append("(data->>%s) = ANY(%s)")
                params.extend([col, [str(v) for v in val]])
            elif op == "contains":
                like = f"%{str(val).replace('%', chr(92) + '%').replace('_', chr(92) + '_')}%"
                parts.append("(data->>%s) ILIKE %s")
                params.extend([col, like])
            else:  # eq / ne
                parts.append(f"(data->>%s) {'=' if op == 'eq' else '<>'} %s")
                params.extend([col, str(val)])
        return " AND " + " AND ".join(parts), params

    async def delete_row(
        self, artifact_id: str, row_key: str, expected_updated_at: Any = None,
    ) -> bool:
        """expected_updated_at 见 _assert_expected_updated_at（原子校验+touch）。"""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await self._assert_expected_updated_at(
                    conn, artifact_id, expected_updated_at)
                cur = await conn.execute(
                    """DELETE FROM kb_table_rows
                       WHERE artifact_id = %s AND revision = 0 AND row_key = %s
                       RETURNING row_key""",
                    (artifact_id, row_key),
                )
                hit = await cur.fetchone() is not None
                if hit:
                    await self._touch_in_conn(conn, artifact_id)
                return hit

    async def list_rows(
        self, artifact_id: str, revision: int, page: int = 1, size: int = 50,
        sort_ordinal: bool = True,
    ) -> dict[str, Any]:
        """codex三审 P1-2b（读取投影兜底）：返回前对每行 data 做 alias 旧名→
        当前列名投影（当前名键缺失且旧名键存在时，仅在返回视图改名，不改
        存储）——discard 后 checkout 回来的行仍是旧键，draft 期编辑面板按
        当前 schema 显示才有值。对任意 revision 统一投影：编辑面板读
        current/历史版时同样需要对齐当前 schema（Java/serving 运行态读不走
        本方法，不受影响）。"""
        page = max(1, int(page))
        size = max(1, min(_MAX_PAGE_SIZE, int(size)))
        order = "ordinal, row_key" if sort_ordinal else "row_key"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"""SELECT row_key, data, provenance, conflicts, ordinal
                    FROM kb_table_rows
                    WHERE artifact_id = %s AND revision = %s
                    ORDER BY {order}
                    LIMIT %s OFFSET %s""",
                (artifact_id, revision, size, (page - 1) * size),
            )
            rows = [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                """SELECT COUNT(*) AS n FROM kb_table_rows
                   WHERE artifact_id = %s AND revision = %s""",
                (artifact_id, revision),
            )
            total = int((await cur.fetchone())["n"])  # type: ignore[index]
            cur = await conn.execute(
                """SELECT column_name, alias FROM kb_table_columns
                   WHERE artifact_id = %s AND status = 'active'""",
                (artifact_id,),
            )
            alias_map = {
                str(old): str(col["column_name"])
                for col in await cur.fetchall()
                for old in (col.get("alias") or [])
                if str(old) and str(old) != str(col["column_name"])
            }
        if alias_map:
            for row in rows:
                data = row.get("data")
                if not isinstance(data, dict) or not data:
                    continue
                projected = data
                for old_name, current_name in alias_map.items():
                    if old_name in projected and current_name not in projected:
                        if projected is data:
                            projected = dict(data)
                        projected[current_name] = projected.pop(old_name)
                if projected is not data:
                    row["data"] = projected
        return {"rows": rows, "total": total, "page": page, "size": size}

    async def count_rows(self, artifact_id: str, revision: int) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT COUNT(*) AS n FROM kb_table_rows
                   WHERE artifact_id = %s AND revision = %s""",
                (artifact_id, revision),
            )
            return int((await cur.fetchone())["n"])  # type: ignore[index]

    async def diff_draft_vs_current(self, artifact_id: str) -> dict[str, Any]:
        """draft(rev0) vs current 的行级+列级差异（61号 §八，「本次发布将产生
        的变化」口径）：added=只在 draft（发布将新增）；removed=只在 current
        （发布将删除）；changed 列级 old=current 值 → new=draft 值。
        双侧 CTE + FULL JOIN，过滤全在 CTE 内——WHERE 过滤会丢 removed 行。
        列级 changed 在 Python 对比 jsonb。从未发布（current=0）时 current
        侧取 -1（空集）→ 全部计为新增。

        干净态投影（对抗审查修复③）：draft_open=FALSE 且 current>0 = 与已发布
        一致（list_rows 同口径），直接返回零差异，不进 CTE。样例截断（修复⑦）：
        added/removed/changed 各只回前 100 条，counts 恒全量——防 10 万行
        GB 级响应。
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT current_revision, draft_open FROM kb_artifacts
                   WHERE artifact_id = %s""",
                (artifact_id,),
            )
            art = await cur.fetchone()
            if art is not None and not art["draft_open"] \
                    and int(art["current_revision"]) > 0:
                return self._empty_diff()
            current_rev = int(art["current_revision"]) if art else 0
            cur = await conn.execute(
                """WITH d AS (SELECT row_key, data FROM kb_table_rows
                              WHERE artifact_id = %s AND revision = 0),
                     c AS (SELECT row_key, data FROM kb_table_rows
                           WHERE artifact_id = %s AND revision = %s)
                SELECT COALESCE(d.row_key, c.row_key) AS row_key,
                       (c.row_key IS NULL) AS added,
                       (d.row_key IS NULL) AS removed,
                       c.data AS old_data, d.data AS new_data
                FROM d FULL JOIN c ON d.row_key = c.row_key
                WHERE d.row_key IS NULL OR c.row_key IS NULL
                   OR d.data IS DISTINCT FROM c.data""",
                (artifact_id, artifact_id, current_rev if current_rev > 0 else -1),
            )
            raw = [dict(r) for r in await cur.fetchall()]
        added: list[str] = []
        removed: list[str] = []
        changed: list[dict[str, Any]] = []
        for row in raw:
            key = str(row["row_key"])
            if row["added"]:
                added.append(key)
                continue
            if row["removed"]:
                removed.append(key)
                continue
            old = row["old_data"] or {}
            new = row["new_data"] or {}
            for col in sorted(set(old) | set(new)):
                if old.get(col) != new.get(col):
                    changed.append(
                        {"row_key": key, "column": col,
                         "old": old.get(col), "new": new.get(col)},
                    )
        return {
            "added": added[:_DIFF_SAMPLE_LIMIT], "removed": removed[:_DIFF_SAMPLE_LIMIT],
            "changed": changed[:_DIFF_SAMPLE_LIMIT],
            "added_count": len(added), "removed_count": len(removed),
            "changed_count": len(changed),
        }

    @staticmethod
    def _empty_diff() -> dict[str, Any]:
        return {
            "added": [], "removed": [], "changed": [],
            "added_count": 0, "removed_count": 0, "changed_count": 0,
        }

    # ---------------------------------------------------- 表达式索引（§4.5）

    async def create_expression_indexes(
        self, artifact_id: str, revision: int,
        indexed_columns: list[dict[str, Any]],
    ) -> list[str]:
        """按 is_indexed 列生成类型化表达式索引：int/float 加 ::numeric cast
        （空串先 NULLIF，防 cast 崩）；其余按文本。命名 idx_kbtr_{art}_{col}。
        61号：发布后按发布版建。返回创建的索引名列表。

        codex二审批号⑤：表达式必须限定 ``WHERE artifact_id = <本制品>``——
        否则 CREATE INDEX 对 kb_table_rows 全表行求 cast，其他制品同名列的
        脏值（如 text 列里的非数文本）会当场炸掉索引创建（进而炸发布）。

        codex三审 P1-3：WHERE 再限定 ``AND revision = <发布版>``——历史版本行
        可能含旧类型文本（列类型改过），不让它们参与 cast 求值；rev0 草稿
        写入也不维护索引（脏文本写入不炸）。代价：历史版本读无表达式索引
        （低频全扫，接受）。发布时 drop 全部 + 按新版重建的流程不变。
        """
        created: list[str] = []
        if not indexed_columns:
            return created
        async with self._pool.connection() as conn:
            for col in indexed_columns:
                name = f"idx_kbtr_{artifact_id}_{col['column_id']}"
                col_ref = sql.Literal(str(col["column_name"]))
                if col.get("column_type") in ("int", "float"):
                    expr = sql.SQL("((NULLIF(data->>{}, ''))::numeric)").format(col_ref)
                else:
                    expr = sql.SQL("(data->{})").format(col_ref)
                stmt = sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {} ON kb_table_rows"
                    " (revision, {}) WHERE artifact_id = {} AND revision = {}"
                ).format(
                    sql.Identifier(name), expr,
                    sql.Literal(artifact_id), sql.Literal(int(revision)),
                )
                await conn.execute(stmt)
                created.append(name)
        return created

    async def drop_draft_expression_indexes(self, artifact_id: str) -> None:
        """清理本制品全部动态索引（重发布前——列类型/名字变了旧定义会残留，
        CREATE INDEX IF NOT EXISTS 不会替换既有索引）。

        codex二审批号⑤：转义必须只作用于前缀——旧写法把末尾通配符 % 一并
        转义成 ``\\%``（字面 %），清理恒空匹配=旧索引永不回收。先转义前缀
        （artifact_id 内的 ``_`` 仍按字面匹配，正确）再追加未转义 ``%``。"""
        prefix = (
            f"idx_kbtr_{artifact_id}_"
            .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = prefix + "%"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT indexname FROM pg_indexes
                   WHERE tablename = 'kb_table_rows' AND indexname LIKE %s
                   ESCAPE '\\'""",
                (pattern,),
            )
            names = [str(r["indexname"]) for r in await cur.fetchall()]
            for name in names:
                await conn.execute(sql.SQL("DROP INDEX IF EXISTS {}").format(
                    sql.Identifier(name),
                ))

    # ------------------------------------------------------------ 导入批次

    async def create_batch(
        self, artifact_id: str, *, channel: str, file_name: str,
        sheet_name: str, row_count: int, created_by: str,
    ) -> dict[str, Any]:
        if channel not in ("upload", "kb_import", "wiki_compile"):
            raise ArtifactError("invalid_channel")
        batch_id = f"bat_{uuid.uuid4().hex}"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """INSERT INTO kb_artifact_batches
                   (batch_id, artifact_id, channel, file_name, sheet_name,
                    row_count, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING batch_id, artifact_id, channel, file_name,
                             sheet_name, row_count, created_by, created_at""",
                (batch_id, artifact_id, channel, file_name, sheet_name,
                 row_count, created_by),
            )
            return dict(await cur.fetchone())  # type: ignore[arg-type]

    async def list_batches(self, artifact_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """SELECT batch_id, artifact_id, channel, file_name, sheet_name,
                          row_count, created_by, created_at
                   FROM kb_artifact_batches
                   WHERE artifact_id = %s ORDER BY created_at DESC""",
                (artifact_id,),
            )
            return [dict(r) for r in await cur.fetchall()]
