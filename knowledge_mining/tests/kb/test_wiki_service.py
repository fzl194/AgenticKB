"""WikiService / WikiLifecycle 服务层测试 —— 纯内存 fake（本地可跑，不碰真库）。

覆盖（63号批次2 Task 3）：origin 恒 manual；page_key 缺省归一/非法/改名占用
冲突；专页 frontmatter 四类型校验；全量链接重算（删页落 broken + alias 归一
canonical）；revision 路由（draft/current/显式）；status 自愈；embedding 后置
（幂等键形态/max_attempts=3/终态失败→failed/瞬时异常与超时→pending 保持）；
ArtifactService kind=wiki 分支的 stale/viewer 两个门禁便宜用例（Task 2 质量
审查遗留）。
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.kb.db_wiki import (
    WikiError,
    _build_page_changes,
    chunk_page_for_units,
)
from knowledge_mining.mining.infra.llm_task_client import LlmTaskError
from knowledge_mining.mining.kb.services.artifact_service import (
    ArtifactForbidden,
    ArtifactService,
    InvalidOperation,
    StaleArtifact,
)
from knowledge_mining.mining.kb.services.wiki_service import (
    WikiLifecycle,
    WikiService,
    _EMBED_INFLIGHT,
    _EMBED_TASKS,
)
from knowledge_mining.tests.kb.test_artifacts_service import (
    FakeArtifactDB,
    _user,
)

pytestmark = pytest.mark.asyncio

WID = "art_wiki1"
DOMAIN = "plant-a"
NOW = datetime(2026, 10, 22, 9, 0, 0, 500000, tzinfo=timezone.utc)

# Fake 单调时钟（Task 5.2 双审 flake 项，随 Task 5.3 修复）：Windows 下
# datetime.now 精度不足以区分同刻连写——upsert_draft_toc 两次调用同微秒时
# 「令牌随写推进」（updated_at 严格大于旧值）假失败（5 连跑 3 败）。模块级
# 单调计数保证每次取值 strictly greater，保持「新令牌≠旧令牌」语义。
_fake_last_now = datetime.min.replace(tzinfo=timezone.utc)


def _fake_now() -> datetime:
    global _fake_last_now
    now = datetime.now(timezone.utc)
    if now <= _fake_last_now:
        now = _fake_last_now + timedelta(microseconds=1)
    _fake_last_now = now
    return now


# ---------------------------------------------------------------- fakes

class FakeWikiDB:
    """WikiDB 接口的内存实现。与 FakeArtifactDB 共享 artifacts dict——
    乐观锁 updated_at / current_revision / draft_open 单一真相源。"""

    def __init__(self, artifacts: dict[str, dict]) -> None:
        self.artifacts = artifacts
        # aid -> revision -> page_key -> 行
        self.pages: dict[str, dict[int, dict[str, dict]]] = {}
        # aid -> revision -> [edge]
        self.links: dict[str, dict[int, list[dict]]] = {}
        # (aid, revision) -> [unit 行]
        self.units: dict[tuple[str, int], list[dict]] = {}
        # aid -> revision -> revisions 行
        self.revisions: dict[str, dict[int, dict]] = {}
        # aid -> 目录草稿行（artifact_id/toc/updated_at/updated_by）
        self.draft_tocs: dict[str, dict] = {}

    def _touch(self, artifact_id: str) -> None:
        self.artifacts[artifact_id]["updated_at"] = _fake_now()

    def _assert_expected(self, artifact_id: str, expected_updated_at) -> None:
        if expected_updated_at is None:
            return
        art = self.artifacts.get(artifact_id)
        if art is None:
            raise WikiError("artifact_not_found")
        if art["updated_at"] != expected_updated_at:
            raise WikiError("stale_artifact")

    # ---- 页面 CRUD -------------------------------------------------------

    async def count_pages(self, artifact_id: str, revision: int = 0) -> int:
        return len(self.pages.get(artifact_id, {}).get(revision, {}))

    async def list_pages(self, artifact_id: str, *, revision: int) -> list[dict]:
        rows = []
        for row in self.pages.get(artifact_id, {}).get(revision, {}).values():
            fm = row.get("frontmatter") or {}
            aliases = fm.get("aliases") if isinstance(fm, dict) else None
            rows.append({
                "page_key": row["page_key"], "title": row["title"],
                "page_type": row["page_type"], "origin": row["origin"],
                "ordinal": row["ordinal"], "flags": dict(row.get("flags") or {}),
                "updated_at": row.get("updated_at"),
                "updated_by": row.get("updated_by"),
                "aliases": [str(a) for a in (aliases or [])],
            })
        rows.sort(key=lambda r: (r["ordinal"], r["page_key"]))
        return rows

    async def get_page(self, artifact_id: str, revision: int, page_key: str):
        row = self.pages.get(artifact_id, {}).get(revision, {}).get(page_key)
        return dict(row) if row else None

    async def upsert_page(
        self, artifact_id: str, *, page_key: str, title: str,
        page_type: str = "topic", frontmatter: dict | None = None,
        body_md: str = "", provenance: list | None = None,
        flags: dict | None = None, ordinal: int = 0, origin: str = "manual",
        updated_by: str = "", expected_updated_at=None,
    ) -> dict:
        self._assert_expected(artifact_id, expected_updated_at)
        row = {
            "page_key": page_key, "title": title, "page_type": page_type,
            "frontmatter": dict(frontmatter or {}), "body_md": body_md,
            "origin": origin, "provenance": list(provenance or []),
            "flags": dict(flags or {}), "ordinal": int(ordinal),
            "updated_at": _fake_now(), "updated_by": updated_by,
        }
        self.pages.setdefault(artifact_id, {}).setdefault(0, {})[page_key] = row
        self._touch(artifact_id)
        return dict(row)

    async def delete_page(
        self, artifact_id: str, page_key: str, *, expected_updated_at=None,
    ) -> bool:
        self._assert_expected(artifact_id, expected_updated_at)
        bucket = self.pages.get(artifact_id, {}).get(0, {})
        if page_key not in bucket:
            return False
        del bucket[page_key]
        self._touch(artifact_id)
        return True

    # ---- 目录草稿（66号批次1：镜像 WikiDB.upsert_draft_toc——归一走真
    # validate_toc 纯函数，锁语义同构：行存在且令牌失配 → stale_artifact，
    # 行不存在首写跳锁）----------------------------------------------------

    async def get_draft_toc(self, artifact_id: str) -> dict | None:
        row = self.draft_tocs.get(artifact_id)
        return dict(row) if row else None

    async def get_revision_snapshot(
        self, artifact_id: str, revision: int,
    ) -> dict | None:
        """镜像 WikiDB.get_revision_snapshot：返回 schema_json 或 None（行
        不存在）——toc 字段缺失/null 的回落判定在 service（单点逻辑）。"""
        row = self.revisions.get(artifact_id, {}).get(int(revision))
        return dict(row)["schema_json"] if row else None

    async def upsert_draft_toc(
        self, artifact_id: str, toc, updated_by: str = "",
        expected_updated_at=None, require_token_if_exists: bool = False,
    ) -> dict:
        from knowledge_mining.mining.kb.db_wiki_toc import validate_toc
        # 先形态校验再 stale 比对——对齐真实现次序（审查 I-2：畸形 422
        # 优先于 stale 409，校验失败不占行锁）。require_token_if_exists 判定
        # 次序同真实现锁内（缺令牌 409 先于令牌失配 409——I-1：fake 无真
        # FOR UPDATE，语义镜像供直调路径覆盖）。
        normalized = validate_toc(toc)
        row = self.draft_tocs.get(artifact_id)
        if row is not None:
            if require_token_if_exists and expected_updated_at is None:
                raise WikiError("stale_artifact")
            if expected_updated_at is not None \
                    and row["updated_at"] != expected_updated_at:
                raise WikiError("stale_artifact")
        self.draft_tocs[artifact_id] = {
            "artifact_id": artifact_id, "toc": normalized,
            "updated_at": _fake_now(),
            "updated_by": updated_by,
        }
        return dict(self.draft_tocs[artifact_id])

    # ---- 链接 ------------------------------------------------------------

    async def replace_page_links(
        self, artifact_id: str, revision: int, from_page: str, edges: list[dict],
    ) -> None:
        lst = self.links.setdefault(artifact_id, {}).setdefault(revision, [])
        lst[:] = [e for e in lst if e["from_page"] != from_page]
        lst.extend(
            dict(e) for e in edges if str(e.get("from_page")) == from_page
        )

    async def list_links(
        self, artifact_id: str, *, revision: int, page_key: str | None = None,
        direction: str = "out",
    ) -> list[dict]:
        if direction not in ("out", "in"):
            raise WikiError("invalid_direction")
        lst = self.links.get(artifact_id, {}).get(revision, [])
        if page_key is None:
            return [dict(e) for e in lst]
        field = "from_page" if direction == "out" else "to_page"
        return [dict(e) for e in lst if e[field] == page_key]

    # ---- diff / units ----------------------------------------------------

    async def diff_draft_vs_current(self, artifact_id: str) -> dict:
        art = self.artifacts.get(artifact_id)
        if art is not None and not art["draft_open"] \
                and int(art["current_revision"]) > 0:
            return {"added": [], "removed": [], "changed": [],
                    "added_count": 0, "removed_count": 0, "changed_count": 0}
        current_rev = int(art["current_revision"]) if art else 0
        draft = self.pages.get(artifact_id, {}).get(0, {})
        current = (self.pages.get(artifact_id, {}).get(current_rev, {})
                   if current_rev > 0 else {})
        added = sorted(k for k in draft if k not in current)
        removed = sorted(k for k in current if k not in draft)
        changed = []
        for key in sorted(draft):
            if key not in current:
                continue
            fields = [
                f for f in ("title", "body_md", "frontmatter", "page_type",
                            "origin")
                if draft[key].get(f) != current[key].get(f)
            ]
            if fields:
                # Task 1.5 审查 I-2：changed 元素复用真装配（changes 键），
                # 替身契约与真库锁定（照 chunk_page_for_units 导入先例）。
                changed.append({
                    "page_key": key, "fields": fields,
                    "changes": _build_page_changes(
                        draft[key], current[key], fields),
                })
        return {"added": added, "removed": removed, "changed": changed,
                "added_count": len(added), "removed_count": len(removed),
                "changed_count": len(changed)}

    async def project_units(self, artifact_id: str, revision: int) -> int:
        bucket: list[dict] = []
        ordered = sorted(
            self.pages.get(artifact_id, {}).get(revision, {}).values(),
            key=lambda r: (r["ordinal"], r["page_key"]),
        )
        for page in ordered:
            chunks = chunk_page_for_units(
                page["title"], page.get("body_md") or "", page["page_key"])
            for unit_key, text in chunks:
                bucket.append({
                    "unit_key": unit_key, "page_key": page["page_key"],
                    "content_text": text, "embed_status": "pending",
                    "embedding": None,
                })
        self.units[(artifact_id, revision)] = bucket
        return len(bucket)

    async def units_status(self, artifact_id: str, revision: int) -> dict:
        rows = self.units.get((artifact_id, revision), [])
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["embed_status"]] = counts.get(row["embed_status"], 0) + 1
        return {"total": len(rows), "done": counts.get("done", 0),
                "failed": counts.get("failed", 0),
                "pending": counts.get("pending", 0)}

    async def list_pending_units(
        self, artifact_id: str, revision: int, limit: int = 32,
    ) -> list[dict]:
        rows = [r for r in self.units.get((artifact_id, revision), [])
                if r["embed_status"] == "pending"]
        rows.sort(key=lambda r: r["unit_key"])
        return [dict(r) for r in rows[:max(1, int(limit))]]

    async def update_unit_embeddings(
        self, artifact_id: str, revision: int, unit_keys: list[str],
        vectors: list[list[float]],
    ) -> None:
        if len(unit_keys) != len(vectors):
            raise WikiError("invalid_embedding_batch")
        by_key = {r["unit_key"]: r
                  for r in self.units.get((artifact_id, revision), [])}
        for key, vector in zip(unit_keys, vectors):
            row = by_key.get(key)
            if row is not None:
                row["embedding"] = [float(v) for v in vector]
                row["embed_status"] = "done"

    async def mark_units_failed(
        self, artifact_id: str, revision: int, unit_keys: list[str],
    ) -> None:
        wanted = set(unit_keys)
        for row in self.units.get((artifact_id, revision), []):
            if row["unit_key"] in wanted:
                row["embed_status"] = "failed"

    # ---- 生命周期 --------------------------------------------------------

    async def publish(
        self, artifact_id: str, *, expected_updated_at=None,
        change_summary: str = "", published_by: str = "",
    ) -> dict:
        art = self.artifacts.get(artifact_id)
        if art is None:
            raise WikiError("artifact_not_found")
        self._assert_expected(artifact_id, expected_updated_at)
        if art["status"] not in ("draft", "published", "deprecated"):
            raise WikiError("invalid_status")
        if not (art.get("usage_note") or "").strip():
            raise WikiError("usage_note_required")
        if not art["draft_open"] and int(art["current_revision"]) > 0:
            raise WikiError("no_pending_changes")
        rev0 = self.pages.get(artifact_id, {}).get(0, {})
        if not rev0:
            raise WikiError("wiki_no_pages")
        page_revs = self.pages.get(artifact_id, {})
        new_rev = max(
            int(art["current_revision"]),
            max(self.revisions.get(artifact_id, {}) or {0: 0}),
            max((r for r in page_revs if r > 0), default=0),
        ) + 1
        self.pages[artifact_id][new_rev] = {
            k: {**v, "updated_at": _fake_now(),
                "updated_by": published_by}
            for k, v in rev0.items()
        }
        self.links.setdefault(artifact_id, {})[new_rev] = [
            dict(e) for e in self.links.get(artifact_id, {}).get(0, [])]
        manifest = [
            {"page_key": p["page_key"], "title": p["title"],
             "page_type": p["page_type"], "ordinal": p["ordinal"],
             "origin": p["origin"]}
            for p in sorted(rev0.values(),
                            key=lambda r: (r["ordinal"], r["page_key"]))
        ]
        # 66号批次1 Task 1.4：镜像真 publish 的目录定版——草稿行同事务解析
        # 冻结进快照 + warnings 随返回值（解析口径复用真纯函数，无双实现）。
        from knowledge_mining.mining.kb.db_wiki_toc import resolve_toc_payload
        resolved_toc, toc_warnings = resolve_toc_payload(
            (self.draft_tocs.get(artifact_id) or {}).get("toc"), manifest)
        self.revisions.setdefault(artifact_id, {})[new_rev] = {
            "revision": new_rev,
            "schema_json": {"pages": manifest, "toc": resolved_toc,
                            "glossary": []},
            "row_count": len(rev0), "change_summary": change_summary,
            "published_by": published_by,
        }
        self.pages[artifact_id][0] = {}
        self.links.setdefault(artifact_id, {})[0] = []
        art["current_revision"] = new_rev
        art["status"] = "published"
        art["draft_open"] = False
        self._touch(artifact_id)
        return {"revision": new_rev, "page_count": len(rev0),
                "warnings": toc_warnings}

    async def rollback_to(
        self, artifact_id: str, revision: int, *, expected_updated_at=None,
    ) -> dict:
        art = self.artifacts.get(artifact_id)
        if art is None:
            raise WikiError("artifact_not_found")
        self._assert_expected(artifact_id, expected_updated_at)
        revision = int(revision)
        if revision < 1 or revision not in self.revisions.get(artifact_id, {}):
            raise WikiError("invalid_revision")
        art["current_revision"] = revision
        art["status"] = "published"
        self._touch(artifact_id)
        return dict(art)

    async def discard_draft(
        self, artifact_id: str, *, expected_updated_at=None,
    ) -> dict:
        self._assert_expected(artifact_id, expected_updated_at)
        art = self.artifacts.get(artifact_id)
        if art is None:
            raise WikiError("artifact_not_found")
        self.pages.setdefault(artifact_id, {})[0] = {}
        self.links.setdefault(artifact_id, {})[0] = []
        art["draft_open"] = False
        self._touch(artifact_id)
        return dict(art)

    async def ensure_draft(self, artifact_id: str) -> dict:
        art = self.artifacts.get(artifact_id)
        if art is None:
            raise WikiError("artifact_not_found")
        if not art["draft_open"]:
            current = int(art["current_revision"])
            if current > 0:
                snapshot = self.pages.get(artifact_id, {}).get(current, {})
                self.pages.setdefault(artifact_id, {})[0] = {
                    k: {**v, "updated_by": ""}
                    for k, v in snapshot.items()
                }
                self.links.setdefault(artifact_id, {})[0] = [
                    dict(e)
                    for e in self.links.get(artifact_id, {}).get(current, [])
                ]
            # checkout 不推进 updated_at（真库同款裁定）
            art["draft_open"] = True
        return dict(art)


class FakeTaskClient:
    """LlmTaskClient 同步记录桩——真实客户端也是同步阻塞（asyncio.to_thread 调）。"""

    def __init__(self, *, status: str = "succeeded") -> None:
        self.submits: list[dict] = []
        self.status = status
        self.raise_on_submit: Exception | None = None
        self._counts: dict[str, int] = {}
        self._n = 0

    def submit_embedding(
        self, texts, *, idempotency_key=None, max_attempts=3,
        priority=100, knowledge_domain=None,
    ) -> str:
        if self.raise_on_submit is not None:
            raise self.raise_on_submit
        self._n += 1
        task_id = f"task-{self._n}"
        self._counts[task_id] = len(texts)
        self.submits.append({
            "texts": list(texts), "idempotency_key": idempotency_key,
            "max_attempts": max_attempts, "knowledge_domain": knowledge_domain,
        })
        return task_id

    def wait_for_tasks(self, task_ids, *, timeout=None, poll_interval=None):
        out = {}
        for task_id in task_ids:
            if self.status == "succeeded":
                entry = {
                    "task_id": task_id, "status": "succeeded",
                    "result": {"parsed_output": {"data": [
                        {"index": i, "embedding": [0.1 * (i + 1), 0.2]}
                        for i in range(self._counts.get(task_id, 0))
                    ]}},
                }
            else:
                entry = {
                    "task_id": task_id, "status": self.status,
                    "error": {"error_type": "boom", "error_message": None},
                }
            out[task_id] = entry
        return out


# ---------------------------------------------------------------- helpers

def _wiki_env(*, task_client: FakeTaskClient | None = None,
              draft_open: bool = False) -> SimpleNamespace:
    adb = FakeArtifactDB()
    adb.artifacts[WID] = {
        "artifact_id": WID, "domain_id": DOMAIN, "kind": "wiki",
        "title": "云核 Wiki", "description": "", "usage_note": "团队词条库",
        "owner_user_id": "u-owner", "visibility": "private",
        "status": "draft", "current_revision": 0, "draft_open": draft_open,
        "created_at": NOW, "updated_at": NOW,
    }
    adb.members[WID] = {
        "u-owner": "owner", "u-editor": "editor", "u-viewer": "viewer",
    }
    adb.domain_bindings.update(
        (uid, DOMAIN) for uid in ("u-owner", "u-editor", "u-viewer"))
    wdb = FakeWikiDB(adb.artifacts)
    tc = task_client or FakeTaskClient()
    lifecycle = WikiLifecycle(wdb, tc)
    svc = WikiService(ArtifactService(adb, wiki=lifecycle), wdb, lifecycle)
    return SimpleNamespace(adb=adb, wdb=wdb, tc=tc, svc=svc,
                           lifecycle=lifecycle)


async def _drain_background() -> None:
    """等掉所有后台任务（_spawn_embed 的 fire-and-forget 协程）。"""
    others = [t for t in asyncio.all_tasks()
              if t is not asyncio.current_task()]
    if others:
        await asyncio.gather(*others)


async def _put_page(env, *, title: str, body: str = "",
                    page_type: str = "topic", frontmatter: dict | None = None,
                    page_key: str | None = None, address_key: str | None = None,
                    user: dict | None = None) -> dict:
    return await env.svc.upsert_page(
        user or _user("u-owner"), WID, page_key=page_key,
        address_key=address_key, title=title, page_type=page_type,
        frontmatter=frontmatter or {}, body_md=body,
    )


# ------------------------------------------------- upsert：origin / page_key

async def test_upsert_origin_always_manual():
    """63号 §3.2：人工写入口恒写 origin='manual'——新建页与更新已有 machine
    页都翻 manual（machine 只能由批次3编译管线产生）。"""
    env = _wiki_env()
    page = await _put_page(env, title="核心网术语", body="词条正文")
    assert page["origin"] == "manual"

    # 库里塞一页 machine（模拟批次3编译产物）→ 人工再编辑 → 翻 manual
    await env.wdb.upsert_page(
        WID, page_key="编译页", title="编译页", origin="machine",
        updated_by="compiler",
    )
    updated = await env.svc.upsert_page(
        _user("u-editor"), WID, page_key="编译页", title="编译页",
        page_type="topic", frontmatter={}, body_md="人工修订",
    )
    assert updated["origin"] == "manual"
    assert updated["body_md"] == "人工修订"


async def test_upsert_page_key_default_normalize_and_invalid():
    """page_key 缺省 = normalize_page_key(title)（空白折叠 '-'、剔非法字符、
    中文保留）；归一后为空 → WikiError(invalid_page_key)。"""
    env = _wiki_env()
    page = await _put_page(env, title="核心网 术语 表!")
    assert page["page_key"] == "核心网-术语-表"

    with pytest.raises(WikiError, match="invalid_page_key"):
        await _put_page(env, title="???")   # 全剔空

    # 显式空串同样非法
    with pytest.raises(WikiError, match="invalid_page_key"):
        await _put_page(env, title="合法标题", page_key="")


async def test_upsert_rename_to_occupied_key_conflicts():
    """改名流（63号 §3.1：改名=新页+旧页删，服务不自动迁移）：显式 page_key
    与被寻址键不同且新键已被其他页占用 → page_key_conflict；新键空闲 → 落新
    页、旧页原样保留（调用方自己删）。"""
    env = _wiki_env()
    await _put_page(env, title="旧页")
    await _put_page(env, title="占用页")

    with pytest.raises(WikiError, match="page_key_conflict"):
        await _put_page(env, title="新标题", page_key="占用页",
                        address_key="旧页")

    # 改名到空闲键：新页落库、旧页不动
    page = await _put_page(env, title="新名页", page_key="新键",
                           address_key="旧页")
    assert page["page_key"] == "新键"
    assert await env.wdb.get_page(WID, 0, "旧页") is not None

    # 未换键（body page_key == 寻址键或与既有页同键）→ 正常更新不冲突
    page = await _put_page(env, title="占用页改", page_key="占用页",
                           address_key="占用页")
    assert page["title"] == "占用页改"


async def test_upsert_preserves_provenance_flags_ordinal_when_unset():
    """更新未传 provenance/flags/ordinal → 保留库中现值（frontmatter 必传
    覆盖）；新建缺省 ordinal=页数+1（追加序）。"""
    env = _wiki_env()
    first = await _put_page(env, title="第一页")
    assert first["ordinal"] == 1
    seeded = await env.wdb.upsert_page(
        WID, page_key="带痕迹页", title="带痕迹页",
        provenance=[{"channel": "compile", "ref": "doc-1"}],
        flags={"reviewed": True}, ordinal=7, updated_by="compiler",
    )
    await env.svc.upsert_page(
        _user("u-owner"), WID, page_key="带痕迹页", title="带痕迹页",
        page_type="topic", frontmatter={"aliases": ["痕迹"]},
        body_md="人工改写",
    )
    refreshed = await env.wdb.get_page(WID, 0, "带痕迹页")
    assert refreshed["provenance"] == seeded["provenance"]
    assert refreshed["flags"] == {"reviewed": True}
    assert refreshed["ordinal"] == 7
    assert refreshed["frontmatter"] == {"aliases": ["痕迹"]}
    # 新建页缺省 ordinal=页数+1（追加序，对齐表格 insert_single_row 口径）
    tail = await _put_page(env, title="尾页")
    assert tail["ordinal"] == 3   # 第一页 + 带痕迹页 = 2 页 → 追加为 3


async def test_upsert_ensure_draft_opens_and_checkout():
    """干净态（已发布）首个编辑动作自动 ensure_draft：current→rev0 检出
    pages+links；checkout 不推进 updated_at。"""
    env = _wiki_env()
    await _put_page(env, title="首页", body="正文")
    result = await env.lifecycle.publish(
        _user("u-owner"), env.adb.artifacts[WID], change_summary="rev1",
        expected_updated_at=None,
    )
    assert result["revision"] == 1
    await _drain_background()
    assert env.adb.artifacts[WID]["draft_open"] is False

    # checkout 不推进 updated_at（rev0=current 逐字节副本，内容未变）
    before = env.adb.artifacts[WID]["updated_at"]
    await env.wdb.ensure_draft(WID)
    assert env.adb.artifacts[WID]["updated_at"] == before
    assert (await env.wdb.get_page(WID, 0, "首页"))["body_md"] == "正文"   # 检出副本

    await _put_page(env, title="首页", body="编辑后正文")   # 已在草稿态，直写 rev0
    assert env.adb.artifacts[WID]["draft_open"] is True
    assert (await env.wdb.get_page(WID, 0, "首页"))["body_md"] == "编辑后正文"


# ------------------------------------------------------- 专页 frontmatter

async def test_frontmatter_validation_four_types():
    """63号 §4.3 专页校验：contradiction（claim_a/claim_b/severity 枚举）/
    open_question（question 以？或?结尾 + priority）/ glossary（term）；
    topic 无强制键；page_type 非法 → invalid_page_type。"""
    env = _wiki_env()

    # contradiction 正反例
    with pytest.raises(WikiError, match="invalid_frontmatter:claim_a"):
        await _put_page(env, title="矛盾1", page_type="contradiction",
                        frontmatter={"claim_b": "B", "severity": "low"})
    with pytest.raises(WikiError, match="invalid_frontmatter:severity"):
        await _put_page(env, title="矛盾2", page_type="contradiction",
                        frontmatter={"claim_a": "A", "claim_b": "B",
                                     "severity": "critical"})
    ok = await _put_page(env, title="矛盾3", page_type="contradiction",
                         frontmatter={"claim_a": "A", "claim_b": "B",
                                      "severity": "high"})
    assert ok["page_type"] == "contradiction"

    # open_question：question 结尾 / priority 必填
    with pytest.raises(WikiError, match="invalid_frontmatter:question"):
        await _put_page(env, title="问题1", page_type="open_question",
                        frontmatter={"question": "怎么切流", "priority": "high"})
    with pytest.raises(WikiError, match="invalid_frontmatter:priority"):
        await _put_page(env, title="问题2", page_type="open_question",
                        frontmatter={"question": "怎么切流？"})
    await _put_page(env, title="问题3", page_type="open_question",
                    frontmatter={"question": "怎么切流?", "priority": "high"})
    await _put_page(env, title="问题4", page_type="open_question",
                    frontmatter={"question": "夜间割接窗口？", "priority": "low"})

    # glossary：term 必填
    with pytest.raises(WikiError, match="invalid_frontmatter:term"):
        await _put_page(env, title="词条1", page_type="glossary",
                        frontmatter={"definition": "..."})
    await _put_page(env, title="词条2", page_type="glossary",
                    frontmatter={"term": "MOP", "definition": "作业程序"})

    # topic：无强制键
    await _put_page(env, title="普通页", page_type="topic", frontmatter={})

    with pytest.raises(WikiError, match="invalid_page_type"):
        await _put_page(env, title="坏类型", page_type="dashboard")


# ------------------------------------------------------- 链接全量重算

async def test_full_link_recompute_delete_breaks_and_alias_normalizes():
    """全量重算（Task 1 质量审查定稿）：A→B 有效边在删 B 后落 broken；
    alias 目标边归一 canonical page_key（反向链接/阅读器跳转正确性前提）；
    被删页自身出边清空。"""
    env = _wiki_env()
    await _put_page(env, title="A页", body="参见 [[B页]] 与 [[别名X|C页别名]]")
    await _put_page(env, title="B页", body="B 正文")
    await _put_page(env, title="C页", body="C 正文",
                    frontmatter={"aliases": ["别名X"]})

    links = await env.wdb.list_links(WID, revision=0)
    by_target = {(e["from_page"], e["to_page"]): e for e in links}
    # A→B 直接命中
    assert by_target[("A页", "B页")]["status"] == "valid"
    # A→别名X 归一到 C 页 canonical；anchor 保留管道右侧书写原文
    assert by_target[("A页", "C页")]["status"] == "valid"
    assert by_target[("A页", "C页")]["anchor_text"] == "C页别名"

    # 删 B → A→B 边落 broken；被删页出边（B 无出边）清空
    await env.svc.delete_page(_user("u-owner"), WID, "B页")
    links = await env.wdb.list_links(WID, revision=0)
    by_target = {(e["from_page"], e["to_page"]): e for e in links}
    assert by_target[("A页", "B页")]["status"] == "broken"
    assert by_target[("A页", "C页")]["status"] == "valid"
    # 入边视角：指向 C 的边（归一后）
    incoming = await env.wdb.list_links(
        WID, revision=0, page_key="C页", direction="in")
    assert {(e["from_page"], e["anchor_text"]) for e in incoming} \
        == {("A页", "C页别名")}


async def test_delete_page_not_found_and_revisions_incoming():
    """删不存在页 → page_not_found；反向链接（direction=in）查目标页入边。"""
    env = _wiki_env()
    await _put_page(env, title="A页", body="[[B页]]")
    await _put_page(env, title="B页")
    with pytest.raises(WikiError, match="page_not_found"):
        await env.svc.delete_page(_user("u-owner"), WID, "幽灵页")
    incoming = await env.wdb.list_links(
        WID, revision=0, page_key="B页", direction="in")
    assert [e["from_page"] for e in incoming] == ["A页"]


# ------------------------------------------------------- revision 路由

async def test_revision_routing_draft_current_explicit():
    """draft=True → rev0（design 角色）；缺省 → current_revision（runtime，
    未发布=空列表）；显式 revision → 历史版；viewer 传 draft → 403。"""
    env = _wiki_env()
    await _put_page(env, title="首页", body="v1")
    result = await env.lifecycle.publish(
        _user("u-owner"), env.adb.artifacts[WID], change_summary="rev1")
    await _drain_background()
    assert result["revision"] == 1

    # runtime 缺省 → current（rev1）
    pages = await env.svc.list_pages(_user("u-viewer"), WID)
    assert [p["page_key"] for p in pages] == ["首页"]

    # 设计态编辑（ensure_draft 检出）加第二页 → rev0 与 current 分叉
    await _put_page(env, title="草稿页", body="仅草稿")
    draft_pages = await env.svc.list_pages(_user("u-editor"), WID, draft=True)
    assert {p["page_key"] for p in draft_pages} == {"首页", "草稿页"}
    runtime_pages = await env.svc.list_pages(_user("u-viewer"), WID)
    assert [p["page_key"] for p in runtime_pages] == ["首页"]   # 草稿页不漏

    # 显式 revision=1（design 角色）
    explicit = await env.svc.list_pages(_user("u-editor"), WID, revision=1)
    assert [p["page_key"] for p in explicit] == ["首页"]

    # 单页同口径
    page = await env.svc.get_page(_user("u-viewer"), WID, "首页")
    assert page["body_md"] == "v1"
    draft_page = await env.svc.get_page(
        _user("u-editor"), WID, "草稿页", draft=True)
    assert draft_page["body_md"] == "仅草稿"

    # viewer 不见设计态：draft=True / 显式 revision 一律 Forbidden
    with pytest.raises(ArtifactForbidden):
        await env.svc.list_pages(_user("u-viewer"), WID, draft=True)
    with pytest.raises(ArtifactForbidden):
        await env.svc.get_page(_user("u-viewer"), WID, "首页", draft=True)
    with pytest.raises(ArtifactForbidden):
        await env.svc.get_page(_user("u-viewer"), WID, "首页", revision=1)

    # 未发布制品的运行态 = 空集
    env2 = _wiki_env()
    await _put_page(env2, title="未发布页")
    assert await env2.svc.list_pages(_user("u-viewer"), WID) == []
    with pytest.raises(WikiError, match="page_not_found"):
        await env2.svc.get_page(_user("u-viewer"), WID, "未发布页")


async def test_list_links_revision_routing():
    """links 读口径同 pages：runtime 读 current；design+draft 读 rev0。"""
    env = _wiki_env()
    await _put_page(env, title="A页", body="[[B页]]")
    await _put_page(env, title="B页")
    await env.lifecycle.publish(
        _user("u-owner"), env.adb.artifacts[WID], change_summary="rev1")
    await _drain_background()

    runtime_links = await env.svc.list_links(_user("u-viewer"), WID)
    assert [(e["from_page"], e["to_page"], e["status"]) for e in runtime_links] \
        == [("A页", "B页", "valid")]

    # 草稿里删 B → rev0 的 A→B 边落 broken；current（rev1）不动
    await env.svc.delete_page(_user("u-editor"), WID, "B页")
    draft_links = await env.svc.list_links(
        _user("u-editor"), WID, draft=True)
    assert [(e["from_page"], e["to_page"], e["status"])
            for e in draft_links] == [("A页", "B页", "broken")]
    runtime_links = await env.svc.list_links(_user("u-viewer"), WID)
    assert [(e["to_page"], e["status"]) for e in runtime_links] \
        == [("B页", "valid")]


# ------------------------------------------------------- status / 自愈

async def test_status_units_and_self_heal():
    """status：{revision, units 四计数}；pending>0 且不在 in-flight → 顺手
    _spawn_embed（FakeTaskClient 收到 submit）；in-flight 防重入不重复提交。"""
    env = _wiki_env()
    await _put_page(env, title="首页", body="正文")
    await env.lifecycle.publish(
        _user("u-owner"), env.adb.artifacts[WID], change_summary="rev1")
    await _drain_background()   # 发布后置 embed 跑完 → units 全 done

    status = await env.svc.status(_user("u-viewer"), WID)
    assert status["revision"] == 1
    assert status["units"]["total"] == 1 and status["units"]["done"] == 1

    # 人为把单元打回 pending（模拟重启丢向量/瞬时失败残留）→ status 自愈
    for unit in env.wdb.units[(WID, 1)]:
        unit["embed_status"] = "pending"
        unit["embedding"] = None
    _EMBED_INFLIGHT.clear()
    submits_before = len(env.tc.submits)
    status = await env.svc.status(_user("u-viewer"), WID)
    assert status["units"]["pending"] == 1
    await _drain_background()
    assert len(env.tc.submits) == submits_before + 1
    assert env.tc.submits[-1]["knowledge_domain"] == DOMAIN
    assert (await env.wdb.units_status(WID, 1))["done"] == 1   # 自愈回填

    # 未发布 → revision 0 / units 全 0
    env2 = _wiki_env()
    assert await env2.svc.status(_user("u-viewer"), WID) == {
        "revision": 0,
        "units": {"total": 0, "done": 0, "failed": 0, "pending": 0},
    }


# ------------------------------------------------- embedding 后置语义

async def test_embed_idempotency_key_and_attempts():
    """幂等键 = wiki-unit:v1:{artifact_id}:{revision}:内容哈希前 24 hex；
    max_attempts=3；成功后 units 全 done（向量回填）。"""
    env = _wiki_env()
    await _put_page(env, title="首页", body="短正文")
    result = await env.lifecycle.publish(
        _user("u-owner"), env.adb.artifacts[WID], change_summary="rev1")
    await _drain_background()

    assert result["row_count"] == 1
    assert len(env.tc.submits) == 1
    submit = env.tc.submits[0]
    assert submit["max_attempts"] == 3
    assert submit["knowledge_domain"] == DOMAIN
    units = env.wdb.units[(WID, 1)]
    expected_digest = hashlib.sha256(
        "\x1f".join(u["content_text"] for u in units).encode("utf-8"),
    ).hexdigest()[:24]
    assert submit["idempotency_key"] \
        == f"wiki-unit:v1:{WID}:1:{expected_digest}"
    status = await env.wdb.units_status(WID, 1)
    assert status == {"total": 1, "done": 1, "failed": 0, "pending": 0}
    assert units[0]["embedding"] is not None


async def test_embed_terminal_failure_marks_failed():
    """llm_service 终态失败（failed/dead_letter）→ mark_units_failed（failed
    不自动重试）；发布本身不受影响。"""
    env = _wiki_env(task_client=FakeTaskClient(status="failed"))
    await _put_page(env, title="首页", body="正文")
    result = await env.lifecycle.publish(
        _user("u-owner"), env.adb.artifacts[WID], change_summary="rev1")
    await _drain_background()
    assert result["revision"] == 1   # 发布不被 embedding 失败拖垮
    assert await env.wdb.units_status(WID, 1) == {
        "total": 1, "done": 0, "failed": 1, "pending": 0,
    }


async def test_embed_transient_and_timeout_keep_pending():
    """提交瞬时异常 / 等待超时 → 保持 pending（可重试态，下次发布或 status
    自愈重试）；异常永不向上抛。"""
    # 提交抛 LlmTaskError（网络抖动口径）
    env = _wiki_env()
    env.tc.raise_on_submit = LlmTaskError("connection refused")
    await _put_page(env, title="首页", body="正文")
    await env.lifecycle.publish(
        _user("u-owner"), env.adb.artifacts[WID], change_summary="rev1")
    await _drain_background()
    assert (await env.wdb.units_status(WID, 1))["pending"] == 1

    # wait 超时（status=timeout 占位）
    env2 = _wiki_env(task_client=FakeTaskClient(status="timeout"))
    await _put_page(env2, title="首页", body="正文")
    await env2.lifecycle.publish(
        _user("u-owner"), env2.adb.artifacts[WID], change_summary="rev1")
    await _drain_background()
    assert (await env2.wdb.units_status(WID, 1))["pending"] == 1

    # pending 保持可自愈：status 拉起 + 正常客户端 → 回填 done
    env2.tc.status = "succeeded"
    env2.tc.raise_on_submit = None
    _EMBED_INFLIGHT.clear()
    await env2.svc.status(_user("u-viewer"), WID)
    await _drain_background()
    assert (await env2.wdb.units_status(WID, 1))["done"] == 1


async def test_spawn_embed_inflight_guard():
    """_spawn_embed 防重入：同 (artifact_id, revision) 在飞时二次 spawn 为
    no-op；批次 >32 条分多批提交。"""
    env = _wiki_env()
    # 两页各 1 单元 + 手工塞 pending 到 (WID, 1)
    env.wdb.units[(WID, 1)] = [
        {"unit_key": f"k{i}", "page_key": "首页", "content_text": f"正文{i}",
         "embed_status": "pending", "embedding": None}
        for i in range(40)
    ]
    env.adb.artifacts[WID]["current_revision"] = 1

    task = env.lifecycle._spawn_embed(WID, 1, DOMAIN)
    assert task is not None
    assert (WID, 1) in _EMBED_INFLIGHT
    # 强引用集在飞期间持有任务（事件循环只持弱引用，防 GC）；完成后释放
    assert task in _EMBED_TASKS
    assert env.lifecycle._spawn_embed(WID, 1, DOMAIN) is None   # 防重入
    await task
    assert (WID, 1) not in _EMBED_INFLIGHT
    assert task not in _EMBED_TASKS
    # 40 条按 32/批分两批（unit_key 排序稳定）
    assert len(env.tc.submits) == 2
    assert [len(s["texts"]) for s in env.tc.submits] == [32, 8]
    assert await env.wdb.units_status(WID, 1) == {
        "total": 40, "done": 40, "failed": 0, "pending": 0,
    }


# ------------------------------------- ArtifactService kind=wiki 分支门禁

async def test_wiki_publish_via_artifact_service_gates():
    """Task 2 审查遗留两便宜用例：kind=wiki + viewer → 403（ArtifactForbidden，
    分支点之前）；+ stale 令牌 → StaleArtifact；owner 正常发布返回
    {revision, row_count, diff}（diff=发布前 rev0 vs current 差异）。"""
    env = _wiki_env()
    owner, viewer = _user("u-owner"), _user("u-viewer")
    art_svc = env.svc._artifacts

    with pytest.raises(ArtifactForbidden):
        await art_svc.publish(user=viewer, artifact_id=WID,
                              change_summary="x")

    stale = "2000-01-01T00:00:00Z"
    with pytest.raises(StaleArtifact):
        await art_svc.publish(user=owner, artifact_id=WID,
                              change_summary="x", expected_updated_at=stale)

    await _put_page(env, title="首页", body="v1")
    result = await art_svc.publish(
        user=owner, artifact_id=WID, change_summary="rev1")
    await _drain_background()
    assert result["revision"] == 1
    assert result["row_count"] == 1
    assert result["diff"] == {"added_count": 1, "removed_count": 0,
                              "changed_count": 0}

    # 二次干净态发布 → NoPendingChanges 已在 kind 分支内由 wdb 门禁翻译
    from knowledge_mining.mining.kb.services.artifact_service import (
        NoPendingChanges,
    )
    with pytest.raises(NoPendingChanges):
        await art_svc.publish(user=owner, artifact_id=WID,
                              change_summary="rev2")

    # 空页 wiki 发布门禁：草稿开着但 rev0 无页 → wiki_no_pages → InvalidOperation
    await art_svc.discard_draft(user=owner, artifact_id=WID)
    env.adb.artifacts[WID]["draft_open"] = True   # 空草稿（不经 checkout）
    with pytest.raises(InvalidOperation, match="wiki_no_pages"):
        await art_svc.publish(user=owner, artifact_id=WID,
                              change_summary="rev2")


async def test_lifecycle_rollback_and_discard_and_diff():
    """rollback 纯指针 + units 按版本保留；discard 清 rev0；diff 六平键
  （draft_open=False 且 current>0 → 零差异投影）。"""
    env = _wiki_env()
    await _put_page(env, title="首页", body="v1")
    art = env.adb.artifacts[WID]
    await env.lifecycle.publish(_user("u-owner"), art, change_summary="rev1")
    await _drain_background()

    # 编辑新页再发布 → rev2
    await _put_page(env, title="二页", body="v2")
    diff = await env.lifecycle.diff(WID)
    assert diff["added"] == ["二页"] and diff["added_count"] == 1
    await env.lifecycle.publish(_user("u-owner"), art, change_summary="rev2")
    await _drain_background()

    rolled = await env.lifecycle.rollback_to(_user("u-owner"), art, 1)
    assert rolled["current_revision"] == 1
    # 回滚后 units 按版本保留：rev1 units 仍在，检索直接用目标版
    assert (await env.wdb.units_status(WID, 1))["total"] == 1

    # 干净态 diff → 零差异投影
    empty = await env.lifecycle.diff(WID)
    assert empty["added_count"] == 0 and empty["changed_count"] == 0

    # discard：开草稿改乱 → discard 回干净态
    await env.wdb.ensure_draft(WID)
    await _put_page(env, title="乱页", body="乱")
    discarded = await env.lifecycle.discard(_user("u-owner"), art)
    assert discarded["draft_open"] is False
    assert await env.wdb.count_pages(WID, 0) == 0

    # 非法 revision 回滚 → InvalidOperation（WikiError 翻译）
    with pytest.raises(InvalidOperation, match="invalid_revision"):
        await env.lifecycle.rollback_to(_user("u-owner"), art, 99)


# ------------------- _build_page_changes 纯函数直测（Task 1.5 审查 M-3）

async def test_build_page_changes_preview_and_line_clip():
    """直测（毫秒级无 PG，落位本文件=纯 fake 文件永跑不被 PG 门控跳过）：
    preview 200 字符精确边界；超长行截前 500 字符加可辨识后缀（审查 I-1：
    body_md 无写入侧封顶，2MB 单行正文只产 5 行 hunks 却带 4MB 字符量，
    行数与 preview 两上限全被穿透）。"""
    old_body = "甲" * 200 + "甲尾"
    new_body = "乙" * 200 + "乙尾"
    changes = _build_page_changes(          # (draft, current)——old 取自 current
        {"body_md": new_body}, {"body_md": old_body}, ["body_md"])
    body = changes["body_md"]
    assert body["old_preview"] == "甲" * 200   # 恰 200：第 201 字符起丢
    assert body["new_preview"] == "乙" * 200
    assert len(body["old_preview"]) == len(body["new_preview"]) == 200
    # 单行替换 → 5 行 hunks（---/+++/@@/-行/+行）；203 字符行 < 500 不截
    assert body["diff_hunks"] == [
        "--- ", "+++ ", "@@ -1 +1 @@",
        "-" + old_body, "+" + new_body,
    ]
    assert "truncated" not in body

    # 审查 I-1：600 字符超长行 → 整行（含 +/- 前缀）截前 500 + 后缀标记
    # （行长上界恰 500+后缀；行级自含可辨识）
    clipped = _build_page_changes(
        {"body_md": "y" * 600}, {"body_md": "x" * 600}, ["body_md"])
    hunks = clipped["body_md"]["diff_hunks"]
    assert hunks[3] == "-" + "x" * 499 + "…[行超长已截断]"
    assert hunks[4] == "+" + "y" * 499 + "…[行超长已截断]"
    suffix_len = len("…[行超长已截断]")
    assert all(len(line) <= 500 + suffix_len for line in hunks)


async def test_build_page_changes_hunks_boundary():
    """直测（审查 M-3）：hunks 100 行边界——恰 100 行不带 truncated 键；
    超过（103 行）截前 100 行 + truncated=true（islice 早停取
    LIMIT+1 行，len>100 即截断，语义与全量物化等价）。"""
    # 恰 100：2 文件头 + 1 @@ + 48 删 + 48 增 + 1 相同尾行（空格前缀上下文）
    old_body = "\n".join(["a"] * 48 + ["tail"])
    new_body = "\n".join(["b"] * 48 + ["tail"])
    exact = _build_page_changes(
        {"body_md": old_body}, {"body_md": new_body}, ["body_md"])["body_md"]
    assert len(exact["diff_hunks"]) == 100
    assert "truncated" not in exact
    assert exact["diff_hunks"][-1] == " tail"

    # 50 删 + 50 增 + 3 头 = 103 行 → 截前 100 + truncated
    old_big = "\n".join(f"旧{i}" for i in range(50))
    new_big = "\n".join(f"新{i}" for i in range(50))
    over = _build_page_changes(
        {"body_md": old_big}, {"body_md": new_big}, ["body_md"])["body_md"]
    assert len(over["diff_hunks"]) == 100
    assert over["truncated"] is True
    assert over["diff_hunks"][0] == "--- " and over["diff_hunks"][1] == "+++ "


async def test_fake_diff_changed_matches_real_shape():
    """审查 I-2：FakeWikiDB.diff 的 changed 元素与真库同构（changes 键由
    _build_page_changes 装配——fake 复用真实现，契约漂移即红）。"""
    env = _wiki_env()
    await _put_page(env, title="首页", body="v1")
    art = env.adb.artifacts[WID]
    await env.lifecycle.publish(_user("u-owner"), art, change_summary="rev1")
    await _drain_background()
    await _put_page(env, title="首页", body="v1 改")

    diff = await env.wdb.diff_draft_vs_current(WID)
    assert diff["changed_count"] == 1
    [elem] = diff["changed"]
    assert set(elem) == {"page_key", "fields", "changes"}
    assert elem["fields"] == ["body_md"]
    assert elem["changes"]["body_md"]["old_preview"] == "v1"
    assert elem["changes"]["body_md"]["new_preview"] == "v1 改"
    assert "truncated" not in elem["changes"]["body_md"]
