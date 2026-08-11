from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from knowledge_mining.mining.api.routes import runs
from knowledge_mining.tests.test_mining_run_trace import Cursor, workflow_run


class Connection:
    def __init__(self, run):
        self.run = run

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        if "SELECT id, domain, status FROM mining_runs" in normalized:
            return Cursor(row={
                "id": self.run["id"],
                "domain": self.run["domain"],
                "status": self.run["status"],
            })
        if "COUNT(*) as c" in normalized:
            return Cursor(row={"c": 1})
        if "ORDER BY started_at DESC" in normalized:
            return Cursor(rows=[dict(self.run)])
        if "FROM mining_runs WHERE id = %s" in normalized:
            return Cursor(row=dict(self.run))
        raise AssertionError(normalized)


class Pool:
    def __init__(self, run):
        self.conn = Connection(run)

    @asynccontextmanager
    async def connection(self):
        yield self.conn


def request_for(run):
    pool = Pool(run)

    async def get_pool(domain):
        return pool

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                domain_pools=SimpleNamespace(async_pool=get_pool)
            )
        )
    )


@pytest.mark.asyncio
async def test_list_and_detail_expose_engine_and_frozen_workflow_summary(monkeypatch):
    monkeypatch.setattr(runs, "require_domain", lambda value: value)
    run = workflow_run()
    request = request_for(run)

    listing = await runs.list_runs(request, domain="plant-a")
    detail = await runs.get_run(run["id"], request, domain="plant-a")

    for item in (listing["items"][0], detail):
        assert item["execution_engine"] == "workflow"
        assert item["workflow"]["id"] == "workflow-a"
        assert item["workflow"]["version"] == 4
        assert item["workflow"]["graph_hash"] == "graph-hash"


def test_expand_preprocess_metadata_is_backward_compatible():
    result = runs._expand_preprocess_metadata({
        "preprocess_status": "partial",
        "preprocess_error_code": None,
        "preprocess_warnings": [{
            "code": "excel_formula_cache_missing",
            "message": "公式没有已保存的计算结果",
            "sheet_name": "汇总",
            "cell_range": "F18",
        }],
        "excel_summary": {"sheet_count": 2, "table_region_count": 3},
    })

    assert result["preprocess_status"] == "partial"
    assert result["error_code"] is None
    assert result["warnings"][0]["sheet_name"] == "汇总"
    assert result["excel_summary"]["table_region_count"] == 3

    legacy = runs._expand_preprocess_metadata({
        "preprocess_error": "legacy failure"
    })
    assert legacy["preprocess_status"] == "failed"
    assert legacy["error_detail"] == "legacy failure"
