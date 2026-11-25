"""62号：LLM 配置按域隔离（chat/rerank/embedding）的解析矩阵测试。

覆盖：
- DomainConfigs：回落语义 / 缓存键 / reload 原子换 / status 摘要
- embedding_dims_locked：维度锁全局（62号二期）判定矩阵
- ProviderResolver：(domain, model) 解析、缓存不串域、未知模型报错
- LLMService.submit / submit_rerank / submit_embedding：域默认模型提交侧解析落库
- ModelService.rerank / embed：按域选 provider 与默认模型
- Worker._execute_embedding：按任务域选 embedding provider
- /admin/config-status、/admin/reload-config：闸门与热加载语义

兼容性铁律（62号 §2.3）：无覆盖域的行为与单配置时代一致——由
test_*_no_override_* 系列锁定。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from llm_service.config import DomainConfigs, GLOBAL_DOMAIN_KEY, embedding_dims_locked
from llm_service.runtime.provider_resolver import ProviderResolver
from llm_service.runtime.service import LLMService
from llm_service.tests.conftest import TEST_CFG

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 测试配置
# ---------------------------------------------------------------------------

def _base_cfg() -> dict:
    return {
        **TEST_CFG,
        "provider": {
            "active_model": "g-active",
            "timeout": 30,
            "bypass_proxy": False,
            "headers": {},
            "models": {
                # 键名 == API id
                "g-active": {"model": "g-active", "base_url": "u1", "api_key": "gk1"},
                # 键名 != API id（解析需按 entry.model 反查）
                "g-vlm": {"model": "g-vlm-id", "base_url": "u1", "api_key": "gk1"},
            },
        },
        "rerank": {
            "base_url": "ru", "api_key": "rk-global",
            "model": "rerank-global", "timeout": 60,
            "bypass_proxy": False, "headers": {},
        },
    }


def _domain_cfg() -> dict:
    """odn 的 effective 配置（控制面 merge 后的成品：全局基底+域差异）。"""
    cfg = _base_cfg()
    # merge 语义：models 按键合并——全局模型仍可用，同名条目被域覆盖。
    models = {
        **cfg["provider"]["models"],
        # 同名条目换 key（部分覆盖，其余字段继承全局）
        "g-active": {**cfg["provider"]["models"]["g-active"], "api_key": "odn-key"},
        # 域自有模型
        "odn-chat": {"model": "odn-chat", "base_url": "u2", "api_key": "odn-k2"},
    }
    cfg["provider"] = {**cfg["provider"], "active_model": "odn-chat", "models": models}
    cfg["rerank"] = {**cfg["rerank"], "model": "rerank-odn", "api_key": "rk-odn"}
    # 62号二期：embedding 模型/端点/密钥按域覆盖；dimensions 继承全局（锁全局）。
    cfg["embedding"] = {**cfg["embedding"], "model": "emb-odn", "api_key": "ek-odn"}
    return cfg


def _dc() -> DomainConfigs:
    return DomainConfigs(_base_cfg(), {"odn": _domain_cfg()})


# ---------------------------------------------------------------------------
# DomainConfigs
# ---------------------------------------------------------------------------

async def test_domain_configs_fallback_semantics():
    base = _base_cfg()
    dc = DomainConfigs(base, {"odn": _domain_cfg()})
    assert dc.for_domain(None) is base               # 全局配置原对象
    assert dc.for_domain("unknown") is base          # 未知域 → 全局
    assert dc.for_domain("odn") == _domain_cfg()     # 覆盖域 → 域配置
    assert dc.has_override("odn") is True
    assert dc.has_override("unknown") is False
    assert dc.has_override(None) is False
    assert dc.domain_key("odn") == "odn"
    assert dc.domain_key("unknown") == GLOBAL_DOMAIN_KEY
    assert dc.domain_key(None) == GLOBAL_DOMAIN_KEY


async def test_domain_configs_reload_and_replace_with():
    dc = _dc()
    other = DomainConfigs.single({"provider": {"active_model": "x"}})
    other_invalid = DomainConfigs({}, {"a": {}}, {"bad": "reason"})
    dc.replace_with(other_invalid)
    assert dc.for_domain("odn") == {}                      # 覆盖被换掉
    assert dc.has_override("a") is True
    assert dc.status()["invalid_domains"] == {"bad": "reason"}


async def test_domain_configs_status_summary():
    status = _dc().status()
    assert status["global"] == {
        "active_model": "g-active",
        "vision_model": None,
        "embedding_model": "embedding-3",
        "embedding_dimensions": 1024,
    }
    assert status["domains"]["odn"] == {
        "active_model": "odn-chat", "rerank_model": "rerank-odn",
        "vision_model": None,
        "embedding_model": "emb-odn", "embedding_dimensions": 1024,
    }
    assert status["invalid_domains"] == {}
    # 摘要不含密钥
    assert "api_key" not in json.dumps(status)


# ---------------------------------------------------------------------------
# embedding_dims_locked（维度锁全局，62号二期）
# ---------------------------------------------------------------------------

async def test_embedding_dims_locked_matrix():
    g = {"embedding": {"dimensions": 1024}}
    assert embedding_dims_locked({"embedding": {"dimensions": 1024}}, g) is True
    assert embedding_dims_locked({"embedding": {"dimensions": 2048}}, g) is False
    # fail-closed：域 effective 缺维度而全局有 → 视为漂移。控制面 deep-merge
    # 后正常不会缺（全局有即继承），缺 = 异常数据，兜底闸从严作废。
    assert embedding_dims_locked({}, g) is False
    assert embedding_dims_locked({"embedding": {}}, g) is False
    # 两边都无维度（老配置）→ None == None，一致
    assert embedding_dims_locked({}, {}) is True
    # 只有域声明了维度而全局没有 → 不一致（不能借覆盖引入新维度）
    assert embedding_dims_locked({"embedding": {"dimensions": 1024}}, {}) is False


# ---------------------------------------------------------------------------
# ProviderResolver
# ---------------------------------------------------------------------------

class _FakeProvider:
    provider_name = "fake"

    def __init__(self, tag: str, default_model: str = ""):
        self.tag = tag
        self.default_model = default_model or tag


@pytest.fixture
def fake_builder(monkeypatch):
    """拦截 provider 构建：按 resolved 配置的 api_key 打标并记录调用。"""
    calls: list[dict] = []

    def _build(resolved: dict):
        calls.append(dict(resolved))
        return _FakeProvider(str(resolved.get("api_key")), str(resolved.get("model", "")))

    monkeypatch.setattr(
        "llm_service.runtime.provider_resolver.build_chat_provider",
        lambda resolved: (_build(resolved), "fake"),
    )
    return calls


async def test_resolver_no_override_uses_seeded_startup_provider(fake_builder):
    dc = _dc()
    resolver = ProviderResolver(dc)
    startup = _FakeProvider("startup", "g-active")
    resolver.seed_chat(startup)
    assert resolver.chat_provider(None, None) is startup
    assert resolver.chat_provider(None, "unknown") is startup   # 无覆盖域同源
    assert resolver.chat_provider("g-active", None) is startup  # 显式 active 键命中种子
    assert fake_builder == []                                    # 零构建——现状路径


async def test_resolver_override_domain_default_model(fake_builder):
    resolver = ProviderResolver(_dc())
    p = resolver.chat_provider(None, "odn")
    assert p.tag == "odn-k2"                     # 域 active_model（odn-chat 的 key）
    assert p.default_model == "odn-chat"
    resolver.chat_provider(None, "odn")
    assert len(fake_builder) == 1                # 缓存命中，不重复构建


async def test_resolver_explicit_global_model_on_override_domain(fake_builder):
    resolver = ProviderResolver(_dc())
    p = resolver.chat_provider("g-active", "odn")
    # merge 语义：同名条目部分覆盖——域密钥 + 全局端点/模型
    assert p.tag == "odn-key"
    assert p.default_model == "g-active"


async def test_resolver_model_id_lookup(fake_builder):
    resolver = ProviderResolver(_dc())
    p = resolver.chat_provider("g-vlm-id", None)   # API id 反查键名
    assert p.tag == "gk1"


async def test_resolver_unknown_explicit_model_raises(fake_builder):
    resolver = ProviderResolver(_dc())
    with pytest.raises(ValueError, match="Unknown chat model"):
        resolver.chat_provider("no-such-model", "odn")


async def test_resolver_cache_keyed_by_domain(fake_builder):
    # b 域：active 同为 g-active 但密钥不同 → 同模型名必须解析出两个 provider
    b_cfg = _base_cfg()
    b_cfg["provider"] = {
        **b_cfg["provider"], "active_model": "g-active",
        "models": {"g-active": {**b_cfg["provider"]["models"]["g-active"], "api_key": "b-key"}},
    }
    resolver = ProviderResolver(DomainConfigs(_base_cfg(), {"odn": _domain_cfg(), "b": b_cfg}))
    p_odn = resolver.chat_provider("g-active", "odn")
    p_b = resolver.chat_provider("g-active", "b")
    assert p_odn is not p_b                       # 同模型名不同域 → 不同 provider
    assert p_odn.tag == "odn-key" and p_b.tag == "b-key"


async def test_resolver_rerank_default_and_model_provider(fake_builder):
    dc = _dc()
    resolver = ProviderResolver(dc)
    seeded = _FakeProvider("global-model-provider")
    resolver.seed_model_provider(seeded)
    assert resolver.model_provider(None) is seeded      # 全局 = 播种实例
    assert resolver.model_provider("unknown") is seeded
    assert resolver.rerank_default_model("odn") == "rerank-odn"
    assert resolver.rerank_default_model(None) == "rerank-global"


async def test_resolver_embedding_defaults(fake_builder):
    """62号二期：embedding 默认模型按域解析；维度锁全局（域=全局值）。"""
    resolver = ProviderResolver(_dc())
    assert resolver.embedding_default_model("odn") == "emb-odn"
    assert resolver.embedding_default_model(None) == "embedding-3"
    assert resolver.embedding_default_model("unknown") == "embedding-3"
    # 覆盖域维度继承全局（装载闸保证一致），无覆盖域直读全局
    assert resolver.embedding_default_dimensions("odn") == 1024
    assert resolver.embedding_default_dimensions(None) == 1024


async def test_resolver_clear_cache_rebuilds(fake_builder):
    resolver = ProviderResolver(_dc())
    resolver.chat_provider(None, "odn")
    n = len(fake_builder)
    resolver.clear_cache()
    resolver.chat_provider(None, "odn")
    assert len(fake_builder) == n + 1            # 清缓存后按当前配置重建


# ---------------------------------------------------------------------------
# LLMService submit / submit_rerank（提交侧域默认解析落库）
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []

    async def execute(self, sql, params):
        self.executes.append((sql, params))

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    def __init__(self):
        self.pool = SimpleNamespace(connection=lambda: self)
        self.conn = _FakeConn()

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False

    async def fetchone(self, sql, params):
        return None                              # 幂等查询：无同键任务

    async def execute(self, sql, params):
        pass


def _request_row(db: _FakeDB) -> tuple:
    for sql, params in db.conn.executes:
        if "INSERT INTO agent_llm_requests" in sql:
            return params
    raise AssertionError("agent_llm_requests 未插入")


def _svc(dc: DomainConfigs | None = None) -> tuple[LLMService, _FakeDB]:
    db = _FakeDB()
    cfg = dc.global_config if dc else _base_cfg()
    svc = LLMService(
        db=db, provider=None, config=cfg, templates=object(),
        domain_configs=dc or DomainConfigs.single(cfg),
    )
    return svc, db


async def test_submit_domain_default_model_persisted():
    svc, db = _svc(_dc())
    await svc.submit("mining", "odn", "stage_x", idempotency_key="k1")
    row = _request_row(db)
    # request_params = (provider_name, model_id, ...) → 展开 INSERT 后在索引 3
    assert row[3] == "odn-chat"


async def test_submit_no_override_domain_keeps_global_active():
    svc, db = _svc(_dc())
    await svc.submit("mining", "other", "stage_x", idempotency_key="k2")
    assert _request_row(db)[3] == "g-active"


async def test_submit_explicit_model_wins_over_domain_default():
    svc, db = _svc(_dc())
    await svc.submit("mining", "odn", "stage_x", model="g-vlm", idempotency_key="k3")
    assert _request_row(db)[3] == "g-vlm-id"     # 落库的是 API model id


async def test_submit_rerank_domain_default_model():
    svc, db = _svc(_dc())
    await svc.submit_rerank("q", ["d"], knowledge_domain="odn", idempotency_key="k4")
    row = _request_row(db)
    # (request_id, task_id, provider, model, ...) → model 在索引 3
    assert row[3] == "rerank-odn"
    assert json.loads(row[6])["model"] == "rerank-odn"


async def test_submit_rerank_no_override_keeps_global():
    svc, db = _svc(_dc())
    await svc.submit_rerank("q", ["d"], knowledge_domain="other", idempotency_key="k5")
    assert _request_row(db)[3] == "rerank-global"


async def test_submit_embedding_domain_default_model_persisted():
    """62号二期：覆盖域提交 embedding → 域模型落 request.model + input_json。"""
    svc, db = _svc(_dc())
    await svc.submit_embedding(["a"], knowledge_domain="odn", idempotency_key="k6")
    row = _request_row(db)
    assert row[3] == "emb-odn"
    assert json.loads(row[6]) == {
        "texts": ["a"], "model": "emb-odn", "dimensions": 1024,
    }


async def test_submit_embedding_no_override_keeps_global():
    svc, db = _svc(_dc())
    await svc.submit_embedding(["a"], knowledge_domain="other", idempotency_key="k7")
    row = _request_row(db)
    assert row[3] == "embedding-3"
    assert json.loads(row[6])["dimensions"] == 1024


async def test_submit_embedding_explicit_model_wins_over_domain_default():
    svc, db = _svc(_dc())
    await svc.submit_embedding(
        ["a"], model="embedding-3", knowledge_domain="odn", idempotency_key="k8",
    )
    assert _request_row(db)[3] == "embedding-3"


# ---------------------------------------------------------------------------
# ModelService.rerank（同步端点按域路由）
# ---------------------------------------------------------------------------

class _FakeModelProvider:
    def __init__(self, tag: str):
        self.tag = tag
        self.calls: list[dict] = []

    async def rerank(self, query, documents, model=None, top_n=None):
        self.calls.append({"query": query, "model": model, "top_n": top_n})
        return {"model": model, "results": [], "usage": {"total_tokens": 0}}


async def test_model_service_rerank_routes_by_domain(monkeypatch):
    from llm_service.models import RerankRequest
    from llm_service.runtime.model_service import ModelService

    global_provider = _FakeModelProvider("global")
    domain_provider = _FakeModelProvider("odn")
    # 拦截 BigModelProvider 构建，避免真发 HTTP；域 provider 可被断言。
    monkeypatch.setattr(
        "llm_service.runtime.provider_resolver.build_model_provider",
        lambda cfg: domain_provider,
    )
    resolver = ProviderResolver(_dc())
    resolver.seed_model_provider(global_provider)
    svc = ModelService(
        global_provider,
        default_rerank_model="rerank-global",
        provider_resolver=resolver,
    )

    await svc.rerank(RerankRequest(query="q", documents=["d"], knowledge_domain="odn"))
    assert global_provider.calls == []                       # 未走全局
    assert domain_provider.calls == [{"query": "q", "model": "rerank-odn", "top_n": None}]


async def test_model_service_rerank_global_domain_uses_seeded_provider():
    from llm_service.models import RerankRequest
    from llm_service.runtime.model_service import ModelService

    global_provider = _FakeModelProvider("global")
    resolver = ProviderResolver(_dc())
    resolver.seed_model_provider(global_provider)
    svc = ModelService(
        global_provider, default_rerank_model="rerank-global",
        provider_resolver=resolver,
    )
    await svc.rerank(RerankRequest(query="q", documents=["d"]))
    assert global_provider.calls == [{"query": "q", "model": "rerank-global", "top_n": None}]


# ---------------------------------------------------------------------------
# ModelService.embed（同步端点按域路由，62号二期）
# ---------------------------------------------------------------------------

class _FakeEmbedProvider:
    def __init__(self, tag: str):
        self.tag = tag
        self.calls: list[dict] = []

    async def embed(self, texts, model=None, dimensions=None):
        self.calls.append({"texts": list(texts), "model": model, "dimensions": dimensions})
        return {
            "model": model or "fake",
            "data": [
                {"index": i, "embedding": [0.1] * (dimensions or 2)}
                for i in range(len(texts))
            ],
            "usage": {"total_tokens": 1},
        }

    async def rerank(self, *a, **k):
        return {"results": []}


async def test_model_service_embed_routes_by_domain(monkeypatch):
    from llm_service.models import EmbeddingRequest
    from llm_service.runtime.model_service import ModelService

    global_provider = _FakeEmbedProvider("global")
    domain_provider = _FakeEmbedProvider("odn")
    monkeypatch.setattr(
        "llm_service.runtime.provider_resolver.build_model_provider",
        lambda cfg: domain_provider,
    )
    resolver = ProviderResolver(_dc())
    resolver.seed_model_provider(global_provider)
    svc = ModelService(
        global_provider,
        default_embedding_model="emb-global",
        default_embedding_dimensions=1024,
        provider_resolver=resolver,
    )

    out = await svc.embed(EmbeddingRequest(input=["t"], knowledge_domain="odn"))
    assert global_provider.calls == []                       # 未走全局
    assert domain_provider.calls == [
        {"texts": ["t"], "model": "emb-odn", "dimensions": 1024},
    ]
    assert out.model == "emb-odn"


async def test_model_service_embed_global_domain_uses_seeded_provider():
    from llm_service.models import EmbeddingRequest
    from llm_service.runtime.model_service import ModelService

    global_provider = _FakeEmbedProvider("global")
    resolver = ProviderResolver(_dc())
    resolver.seed_model_provider(global_provider)
    svc = ModelService(
        global_provider,
        default_embedding_model="emb-global",
        default_embedding_dimensions=1024,
        provider_resolver=resolver,
    )
    await svc.embed(EmbeddingRequest(input=["t"]))
    # resolver 全局 effective（embedding-3）优先于构造期默认 emb-global
    assert global_provider.calls == [
        {"texts": ["t"], "model": "embedding-3", "dimensions": 1024},
    ]


async def test_model_service_embed_explicit_model_and_dimensions_win():
    from llm_service.models import EmbeddingRequest
    from llm_service.runtime.model_service import ModelService

    global_provider = _FakeEmbedProvider("global")
    resolver = ProviderResolver(_dc())
    resolver.seed_model_provider(global_provider)
    svc = ModelService(
        global_provider, default_embedding_model="emb-global",
        provider_resolver=resolver,
    )
    await svc.embed(EmbeddingRequest(input=["t"], model="explicit-emb", dimensions=1024))
    assert global_provider.calls == [
        {"texts": ["t"], "model": "explicit-emb", "dimensions": 1024},
    ]


# ---------------------------------------------------------------------------
# Worker._execute_embedding（执行侧按任务域选 provider，62号二期）
# ---------------------------------------------------------------------------

class _FakeWorkerDB:
    def __init__(self, *, knowledge_domain: str | None, input_json: str):
        self.executes: list[tuple[str, tuple]] = []
        self._request = {"id": "req-1", "task_id": "task-1", "input_json": input_json}
        self._task = {
            "knowledge_domain": knowledge_domain,
            "attempt_count": 0, "max_attempts": 3,
        }

    async def fetchone(self, sql, params):
        if "FROM agent_llm_requests" in sql:
            return self._request
        if "FROM agent_llm_tasks" in sql:
            return self._task
        raise AssertionError(f"unexpected fetchone: {sql}")

    async def execute(self, sql, params):
        self.executes.append((sql, params))


class _FakeWorkerMgr:
    def __init__(self):
        self.completed: list[str] = []
        self.failed: list[tuple] = []

    async def complete(self, task_id):
        self.completed.append(task_id)

    async def fail(self, task_id, error_type, error_message):
        self.failed.append((task_id, error_type, error_message))


async def test_worker_embedding_selects_provider_by_domain():
    from llm_service.runtime.worker import Worker

    domain_provider = _FakeEmbedProvider("odn")
    global_provider = _FakeEmbedProvider("global")
    svc = SimpleNamespace(model_provider_for=lambda d: domain_provider)
    worker = Worker(
        db=_FakeWorkerDB(
            knowledge_domain="odn",
            input_json=json.dumps({"texts": ["a"], "model": "emb-odn", "dimensions": 1024}),
        ),
        task_manager=_FakeWorkerMgr(), event_bus=None, provider=None,
        model_provider=global_provider, llm_service=svc,
    )
    await worker._execute_embedding("task-1")
    assert domain_provider.calls == [
        {"texts": ["a"], "model": "emb-odn", "dimensions": 1024},
    ]
    assert global_provider.calls == []               # 覆盖域：不走全局实例


async def test_worker_embedding_without_svc_falls_back_to_global():
    # 测试构造（无 llm_service）回退构造期 model_provider——与 _execute_rerank 同款。
    from llm_service.runtime.worker import Worker

    global_provider = _FakeEmbedProvider("global")
    worker = Worker(
        db=_FakeWorkerDB(
            knowledge_domain="odn",
            input_json=json.dumps({"texts": ["a"], "model": "emb-odn", "dimensions": 1024}),
        ),
        task_manager=_FakeWorkerMgr(), event_bus=None, provider=None,
        model_provider=global_provider,
    )
    await worker._execute_embedding("task-1")
    assert global_provider.calls == [
        {"texts": ["a"], "model": "emb-odn", "dimensions": 1024},
    ]


# ---------------------------------------------------------------------------
# /admin/config-status 与 /admin/reload-config
# ---------------------------------------------------------------------------

def _admin_request(secret="ivs", role=None, dc=None, resolver=None):
    headers = {"x-internal-auth": secret}
    if role is not None:
        headers["x-kb-role"] = role
    state = SimpleNamespace(internal_verify_secret="ivs")
    if dc is not None:
        state.domain_configs = dc
    if resolver is not None:
        state.provider_resolver = resolver
    return SimpleNamespace(headers=headers, app=SimpleNamespace(state=state))


class _FakeResolver:
    def __init__(self):
        self.cleared = 0

    def clear_cache(self):
        self.cleared += 1


async def test_config_status_gates_and_payload():
    from llm_service.api import admin as admin_api

    with pytest.raises(HTTPException) as e:
        await admin_api.config_status(_admin_request(secret="wrong"))
    assert e.value.status_code == 403

    dc = _dc()
    out = await admin_api.config_status(_admin_request(dc=dc))
    assert out["data"]["domains"]["odn"]["active_model"] == "odn-chat"


async def test_reload_config_requires_internal_and_admin(monkeypatch):
    from llm_service.api import admin as admin_api

    async def _boom():
        raise AssertionError("load_domain_configs must not run on rejected auth")

    monkeypatch.setattr("llm_service.config.load_domain_configs", _boom)
    for req in (
        _admin_request(secret="wrong", role="admin"),   # 伪造内部密钥
        _admin_request(role="admin"),                   # 无内部密钥
        _admin_request(dc=_dc(), resolver=_FakeResolver()),  # 无 admin 角色
    ):
        with pytest.raises(HTTPException):
            await admin_api.reload_config(req)


async def test_reload_config_swaps_and_clears(monkeypatch):
    from llm_service.api import admin as admin_api

    new_dc = DomainConfigs.single({"provider": {"active_model": "fresh"}})
    monkeypatch.setattr(
        "llm_service.config.load_domain_configs", lambda: new_dc,
    )
    dc = _dc()
    resolver = _FakeResolver()
    out = await admin_api.reload_config(_admin_request(dc=dc, resolver=resolver, role="admin"))
    assert out["ok"] is True
    assert dc.for_domain("odn") == new_dc.global_config      # 原地替换，引用保住
    assert resolver.cleared == 1


async def test_reload_config_fetch_failure_keeps_old(monkeypatch):
    from llm_service.api import admin as admin_api

    def _fail():
        raise RuntimeError("control plane down")

    monkeypatch.setattr("llm_service.config.load_domain_configs", _fail)
    dc = _dc()
    out = await admin_api.reload_config(_admin_request(dc=dc, resolver=_FakeResolver(), role="admin"))
    assert out.status_code == 502
    assert dc.has_override("odn") is True                    # 旧配置原样保留
