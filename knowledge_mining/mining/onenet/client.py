# -*- coding: utf-8 -*-
"""知识一张网搜索接口客户端（产品级封装）.

自 ``kone_connector/src/onenet_client.py`` 移植（内网实测 2026-09-07，
协议事实见 kone_connector/docs/01_api_protocol.md），改造：

- ``requests`` → ``httpx``（transport 可注入，测试离线跑）；
- 接口地址由 :class:`OnenetConfig` 提供（config.py）；
- 异常统一 :class:`OnenetQueryError`（含真实原因，不吞错）。

实测边界（务必遵守）：
- total 默认封顶 10000；``track_total_hits=true`` 才有真实总数；
- from+size ≤ 10000；无稳定排序 from 翻页会重复/遗漏——段拉取必带 sort；
- aggs/scroll/PIT 不支持；
- dsl term 必须 ``.keyword`` 后缀；
- 大文档 part_id 全局唯一连续 1..N（N==total）。
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx

from knowledge_mining.mining.onenet.config import OnenetConfig

logger = logging.getLogger(__name__)

#: 切片返回的全部字段（实测样例沉淀，供契约/入库 schema 参考）。
SLICE_FIELDS = [
    "nid", "id", "source_id", "doc_name", "file_name", "doc_type", "language",
    "title", "content", "path", "part_id", "status", "parsed_version",
    "url", "source_site", "publish_time", "publish_timestamp",
    "create_timestamp", "update_timestamp", "author", "public_level",
    "category", "sub_category", "catalogue", "scenario_tag", "doc_tag",
    "product", "product_family", "product_line", "product_series",
    "product_category", "product_version", "map_l1", "map_l2", "map_l3",
    "map_l4", "map_l5", "map_l6", "map_l7", "map_l8", "kos_path",
    "media", "table", "pbi", "attention",
    "reserved_feature", "reserved_management", "subtitle",
    "domain_scene", "category_path",
]


class OnenetQueryError(RuntimeError):
    """一张网查询失败（含 token/网络/响应形状/完整性错误）。"""


class OnenetClient:
    """知识一张网搜索接口客户端（token 缓存 + 401 重取 + 退避重试）."""

    def __init__(
        self, *,
        app_id: str, static_token: str,
        token_url: str, search_url: str,
        source_type: int = 0, timeout: int = 300,
        transport: httpx.BaseTransport | None = None,
        retry: int = 3, verify: bool = False,
    ):
        self.app_id = app_id
        self.static_token = static_token
        self.source_type = source_type
        self.timeout = timeout
        self._token_url = token_url
        self._search_url = search_url
        self._transport = transport
        self._retry = retry
        self._verify = verify
        self._token: str | None = None
        self._client: httpx.Client | None = None

    @classmethod
    def from_config(cls, cfg: OnenetConfig, **kw) -> "OnenetClient":
        return cls(
            app_id=cfg.app_id, static_token=cfg.static_token,
            token_url=cfg.token_url, search_url=cfg.search_url,
            source_type=cfg.source_type, timeout=cfg.timeout,
            verify=cfg.verify_tls, **kw,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                verify=self._verify, timeout=self.timeout,
                transport=self._transport,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ---------------------------------------------------------------- token

    def get_token(self, force: bool = False) -> str:
        """动态 token（result 直填 Authorization；过期由 _post 的 401 分支强制重取）."""
        if self._token and not force:
            return self._token
        credential = base64.b64encode(self.static_token.encode()).decode()
        res = self._http().post(
            self._token_url,
            json={"appId": self.app_id, "credential": credential},
        )
        res.raise_for_status()
        token = (res.json() or {}).get("result")
        if not token:
            raise OnenetQueryError("动态 token 获取失败: " + res.text[:300])
        self._token = token
        return self._token

    # ---------------------------------------------------------------- post

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(self._retry):
            try:
                r = self._http().post(
                    self._search_url, json=body,
                    headers={"Authorization": self.get_token(),
                             "Content-Type": "application/json"},
                )
                if r.status_code == 401 and attempt == 0:
                    self.get_token(force=True)  # token 过期重取后重试
                    continue
                r.raise_for_status()
                j = r.json()
                if isinstance(j, dict) and "searchResults" in j:
                    return j
                if (isinstance(j, list) and j and isinstance(j[0], dict)
                        and "searchResults" in j[0]):
                    return j[0]  # 网关 list 包装
                raise OnenetQueryError(
                    "异常响应: " + json.dumps(j, ensure_ascii=False)[:300])
            except (httpx.HTTPError, OnenetQueryError) as e:
                last_err = e
                if attempt + 1 < self._retry:
                    # 最后一次失败不再白等 4.5s（审查 LOW）
                    time.sleep(1.5 * (attempt + 1))
        raise OnenetQueryError(f"请求失败({self._retry}次): {last_err}")

    # ---------------------------------------------------------------- 查询

    def query_native(
        self,
        search_query_list: list[dict] | None = None,
        page_num: int = 1, page_size: int = 10,
        sort_field: str = "part_id",
        with_total_track: bool = False,
    ) -> dict[str, Any]:
        """原生模式（searchQueryList 多条件 AND）；with_total_track 切 DSL 补真实 total."""
        if with_total_track:
            must = []
            for c in search_query_list or []:
                if c.get("fuzzy"):
                    must.append({"match": {c["field"]: c["content"]}})
                else:
                    must.append({"term": {f"{c['field']}.keyword": {"value": c["content"]}}})
            dsl = {
                "query": {"bool": {"must": must or [{"match_all": {}}]}},
                "from": (page_num - 1) * page_size, "size": page_size,
                "track_total_hits": True,
            }
            return self.query_dsl(dsl)
        body: dict[str, Any] = {
            "pageNum": page_num, "pageSize": page_size, "sortField": sort_field,
        }
        if search_query_list:
            body["searchQueryList"] = search_query_list
        return self._post(body)

    def query_dsl(self, dsl: dict[str, Any] | str) -> dict[str, Any]:
        raw = dsl if isinstance(dsl, str) else json.dumps(dsl, ensure_ascii=False)
        return self._post({"dsl": raw})

    def count_source(self, source_id: str) -> int:
        """track_total_hits 模式真实切片总数（不受 10000 封顶）."""
        dsl = {
            "query": {"bool": {"must": [
                {"term": {"source_id.keyword": {"value": source_id}}}]}},
            "track_total_hits": True, "from": 0, "size": 1,
        }
        return int(self.query_dsl(dsl).get("total") or 0)

    def part_range(self, source_id: str) -> dict[str, int | None]:
        """part_id min/max（asc/desc 各取首条；无切片返回 None 值）."""
        out: dict[str, int | None] = {}
        for order, key in (("asc", "min"), ("desc", "max")):
            dsl = {
                "query": {"bool": {"must": [
                    {"term": {"source_id.keyword": {"value": source_id}}}]}},
                "from": 0, "size": 1,
                "sort": [{"part_id": order}, {"nid.keyword": order}],
            }
            res = self.query_dsl(dsl).get("searchResults") or []
            out[key] = int(res[0]["part_id"]) if res else None
        return out

    def probe_source(self, source_id: str, sample: int = 3) -> dict[str, Any]:
        """摸底：total / part 范围 / 元数据样例."""
        total = self.count_source(source_id)
        pr = self.part_range(source_id)
        dsl = {
            "query": {"bool": {"must": [
                {"term": {"source_id.keyword": {"value": source_id}}}]}},
            "from": 0, "size": sample,
            "sort": [{"part_id": "asc"}, {"nid.keyword": "asc"}],
        }
        res = self.query_dsl(dsl).get("searchResults") or []
        return {
            "source_id": source_id,
            "total_slices": total,
            "part_id": pr,
            "doc_name": res[0].get("doc_name") if res else None,
            "file_name": res[0].get("file_name") if res else None,
            "doc_type": res[0].get("doc_type") if res else None,
            "parsed_version": res[0].get("parsed_version") if res else None,
            "publish_time": res[0].get("publish_time") if res else None,
            "product_line": res[0].get("product_line") if res else None,
            "pbi": res[0].get("pbi") if res else None,
            "sample_slices": res[:sample],
        }

    # ---------------------------------------------------------------- 段拉取

    def fetch_source_chunk(
        self, source_id: str, lo: int, hi: int | None = None,
        page_size: int = 1000,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """拉取 part_id ∈ [lo, hi] 全部切片（段宽 ≤10000 一次拉满）.

        必须带稳定排序（part_id asc, nid asc），否则 from 翻页重复/遗漏（实测坑）。
        """
        must: list[dict] = [{"term": {"source_id.keyword": {"value": source_id}}}]
        if hi is None:
            must.append({"range": {"part_id": {"gte": lo}}})
        else:
            must.append({"range": {"part_id": {"gte": lo, "lte": hi}}})
        q: dict[str, Any] = {
            "query": {"bool": {"must": must}},
            "track_total_hits": True,
            "sort": [{"part_id": "asc"}, {"nid.keyword": "asc"}],
        }
        if fields:
            q["_source"] = fields
        out: list[dict[str, Any]] = []
        frm = 0
        total_in_window: int | None = None
        while True:
            q["from"] = frm
            q["size"] = page_size
            j = self.query_dsl(q)
            res = j.get("searchResults") or []
            if total_in_window is None:
                total_in_window = int(j.get("total") or 0)
            out.extend(res)
            frm += len(res)
            if len(res) < page_size:
                break
            # 窗口已拉满（满页恰好等于 total 时不再多发一次请求）
            if total_in_window is not None and len(out) >= total_in_window:
                break
            if frm >= 10000:
                raise OnenetQueryError(
                    f"part段[{lo},{hi}]窗口内数据>10000(实际{total_in_window})，请缩小段宽")
        if total_in_window is not None and len(out) != total_in_window:
            raise OnenetQueryError(
                f"part段[{lo},{hi}]拉取不完整: 期望{total_in_window}, 实得{len(out)}")
        return out


__all__ = ["OnenetClient", "OnenetQueryError", "SLICE_FIELDS"]
