# -*- coding: utf-8 -*-
"""知识一张网 · 文档查询原型（独立系统，最小粒度）

定位：单独开发验证「多条件查询 → 文档级汇总 → 分页」这条链路，
满足需求后再迁移进主项目。刻意不做：数据库、挖掘衔接、前后端分离。

运行（项目环境已有全部依赖，无需安装）：
    cd onenet_query
    # 凭据二选一：本目录放 onenet.yaml（app_id/static_token 两行），
    #            或环境变量 ONENET_APP_ID / ONENET_STATIC_TOKEN
    python -m uvicorn app:app --host 0.0.0.0 --port 8899
    # 浏览器打开 http://<ip>:8899

查询语义（与一张网原生接口一致）：
- 多条件 AND；每条件三元组 {字段, 精确/模糊, 内容}
- 精确 = term 全等；模糊 = match 分词相关度（实测口径见 kone_connector 文档）
- 汇总：按 source_id 聚合切片 → 文档行（文档级字段取首条命中切片）
- 分页：底层一次拉满（上限 10000 切片/查询）→ 去重 → 文档列表分页
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S")
log = logging.getLogger("onenet")

import httpx
import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ---------------------------------------------------------------- 配置

TOKEN_URL = "http://oauth2.huawei.com/ApiCommonQuery/appToken/getRestAppDynamicToken"
SEARCH_URL = "http://apigw-cn-south02.huawei.com/api/search/source_type?source_type=0"

#: 允许的查询字段（用户定义的 12 个，顺序即下拉顺序）
FIELDS = [
    "source_id", "nid", "url", "title", "path", "content",
    "source_site", "file_name", "category_path", "doc_name",
    "doc_type", "part_id",
]

#: 数字字段：模糊（分词）无意义，强制精确
NUMERIC_FIELDS = {"part_id"}

_PAGE_SIZE = 1000          # 底层每页切片数
_MAX_SLICES = 10000        # 接口硬上限（from+size ≤ 10000）


def load_credentials() -> tuple[str, str]:
    """env 优先，其次本目录 onenet.yaml，再次仓库配置 onenet.yaml。"""
    app_id = os.environ.get("ONENET_APP_ID")
    token = os.environ.get("ONENET_STATIC_TOKEN")
    if app_id and token:
        return app_id, token
    here = Path(__file__).parent
    for cand in (here / "onenet.yaml",
                 here.parent / "main_control_service" / "config" / "system" / "onenet.yaml"):
        if cand.exists():
            data = yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
            return str(data.get("app_id") or ""), str(data.get("static_token") or "")
    raise RuntimeError(
        "未找到凭据：设 ONENET_APP_ID/ONENET_STATIC_TOKEN，或在 "
        f"{here / 'onenet.yaml'} 填 app_id / static_token")


# ---------------------------------------------------------------- 一张网客户端
# 调用逻辑对齐 kone_connector/src/onenet_client.py（内网实证 2026-09-07）：
# - 原生 searchQueryList 模式（与 DSL 语义一致——协议实测「两种方式都可作为
#   标准拉取入口」；三元组 {field, fuzzy, content} 即原生格式，零翻译透传）
# - _post：401→token 重取重试一次；请求失败重试 3 次（退避 1.5s*n）；
#   网关 list 包装解包


class Onenet:
    def __init__(self) -> None:
        self._app_id, self._static_token = load_credentials()
        self._token: str | None = None
        # trust_env=False：忽略 HTTP(S)_PROXY 环境变量——内网 407 修复。
        # 一张网端点是内网直连，企业代理（407 认证）不应介入。
        proxies = {k: v for k, v in os.environ.items()
                   if k.lower() in ("http_proxy", "https_proxy", "all_proxy")}
        if proxies:
            log.info("检测到代理环境变量（已忽略，内网直连）: %s",
                     ",".join(sorted(proxies.keys())))
        self._http = httpx.Client(verify=False, timeout=300, trust_env=False)  # 同 demo timeout=300

    def _get_token(self, force: bool = False) -> str:
        if self._token and not force:
            return self._token
        credential = base64.b64encode(self._static_token.encode()).decode()
        res = self._http.post(TOKEN_URL,
                              json={"appId": self._app_id, "credential": credential})
        res.raise_for_status()
        token = (res.json() or {}).get("result")
        if not token:
            raise RuntimeError("token 获取失败: " + res.text[:200])
        self._token = token
        log.info("token 获取成功 force=%s（值脱敏: %s...）", force, token[:10])
        return token

    def _post(self, body: dict, retry: int = 3) -> dict:
        """对齐 kone_connector._post。"""
        last_err: Exception | None = None
        for attempt in range(retry):
            t0 = time.monotonic()
            try:
                res = self._http.post(
                    SEARCH_URL, json=body,
                    headers={"Authorization": self._get_token(),
                             "Content-Type": "application/json"})
                elapsed = (time.monotonic() - t0) * 1000
                log.info("search page=%s size=%s -> HTTP %s (%.0fms) 条件=[%s]",
                         body.get("pageNum"), body.get("pageSize"),
                         res.status_code, elapsed, _conds_brief(body))
                if res.status_code == 401 and attempt == 0:
                    self._get_token(force=True)   # token 过期重取（同 demo）
                    continue
                if res.status_code >= 500:
                    raise RuntimeError(
                        f"网关错误 HTTP {res.status_code}（{elapsed:.0f}ms）: "
                        + res.text[:150])
                res.raise_for_status()
                data = res.json()
                if isinstance(data, dict) and "searchResults" in data:
                    return data
                # 网关 list 包装（同 demo）
                if (isinstance(data, list) and data and isinstance(data[0], dict)
                        and "searchResults" in data[0]):
                    return data[0]
                raise RuntimeError("异常响应: "
                                   + json.dumps(data, ensure_ascii=False)[:200])
            except Exception as e:  # noqa: BLE001 —— 同 demo：请求失败一律计退避
                last_err = e
                log.warning("第 %s 次尝试失败 (%.0fms): %s",
                            attempt + 1, (time.monotonic() - t0) * 1000, e)
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"请求失败({retry}次): {last_err}")

    def query_native(self, conditions: list[dict],
                     page_num: int = 1, page_size: int = 10) -> dict:
        """原生 searchQueryList（对齐 kone_connector.query_native 默认参）。"""
        body: dict = {"pageNum": page_num, "pageSize": page_size,
                      "sortField": "part_id"}
        if conditions:
            body["searchQueryList"] = conditions
        return self._post(body)


def _conds_brief(body: dict) -> str:
    """日志里打条件摘要。"""
    conds = body.get("searchQueryList") or []
    return ",".join(
        f"{c.get('field')}={c.get('content')!r}§{'模糊' if c.get('fuzzy') else '精确'}"
        for c in conds)[:200]


_client: Onenet | None = None


def get_client() -> Onenet:
    global _client
    if _client is None:
        _client = Onenet()
    return _client


# ---------------------------------------------------------------- 查询 → 汇总 → 分页

def pull_slices(conditions: list[dict], max_slices: int = _MAX_SLICES):
    """原生 searchQueryList 翻页（pageNum 路径，from+size ≤ 10000 硬上限）。

    原生模式单键 sortField 翻页理论上可能重复（同 part_id 跨文档撞键，
    协议 §5-#4 实测坑）——按 nid 去重兜底，重复数打日志。
    total 无 track_total_hits（原生模式不支持），≥10000 即封顶值。
    """
    client = get_client()
    slices: list[dict] = []
    seen_nid: set[str] = set()
    dup_dropped = 0
    total: int | None = None
    page = 1
    while (page - 1) * _PAGE_SIZE + _PAGE_SIZE <= _MAX_SLICES             and len(slices) < max_slices:
        res = client.query_native(conditions, page_num=page, page_size=_PAGE_SIZE)
        if total is None:
            total = int(res.get("total") or 0)
        rows = res.get("searchResults") or []
        if not rows:
            break
        for row in rows:
            nid = str(row.get("nid") or "")
            if nid and nid in seen_nid:
                dup_dropped += 1
                continue
            if nid:
                seen_nid.add(nid)
            slices.append(row)
        if len(rows) < _PAGE_SIZE:
            break
        page += 1
    if dup_dropped:
        log.warning("翻页稳定性守卫：按 nid 去重丢弃 %s 条重复切片", dup_dropped)
    return slices[:max_slices], total


def aggregate_documents(slices: list[dict]) -> list[dict]:
    """按 source_id 聚合 → 文档行（保持相关度顺序=首现顺序）。"""
    docs: dict[str, dict] = {}
    for row in slices:
        sid = str(row.get("source_id") or "").strip()
        if not sid:
            continue
        doc = docs.get(sid)
        if doc is None:
            doc = {
                "source_id": sid,
                "doc_name": row.get("doc_name"),
                "file_name": row.get("file_name"),
                "doc_type": row.get("doc_type"),
                "parsed_version": row.get("parsed_version"),
                "publish_time": row.get("publish_time"),
                "product_line": row.get("product_line"),
                "language": row.get("language"),
                "slice_hits": 0,
                "sample_titles": [],
            }
            docs[sid] = doc
        doc["slice_hits"] += 1
        title = row.get("title")
        if title and len(doc["sample_titles"]) < 3 and title not in doc["sample_titles"]:
            doc["sample_titles"].append(title)
    return list(docs.values())


# ---------------------------------------------------------------- API

app = FastAPI(title="知识一张网 · 文档查询原型")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE_HTML


@app.post("/api/search")
def api_search(body: dict[str, Any]) -> dict:
    raw = body.get("conditions") or []
    conditions = []
    for c in raw:
        field = str(c.get("field") or "").strip()
        content = str(c.get("content") or "").strip()
        if field not in FIELDS:
            raise _err(422, f"非法字段: {field}（允许: {', '.join(FIELDS)}）")
        if not content:
            continue
        fuzzy = bool(c.get("fuzzy"))
        if field in NUMERIC_FIELDS:
            fuzzy = False  # 数字字段分词无意义
        conditions.append({"field": field, "fuzzy": fuzzy, "content": content})
    if not conditions:
        raise _err(422, "至少一条有效查询条件")

    page = max(int(body.get("page") or 1), 1)
    page_size = min(max(int(body.get("page_size") or 20), 1), 100)
    max_slices = min(int(body.get("max_slices") or _MAX_SLICES), _MAX_SLICES)

    log.info("=== 查询开始 条件=%s page=%s ===",
             json.dumps(conditions, ensure_ascii=False), page)
    t0 = time.monotonic()
    try:
        slices, total = pull_slices(conditions, max_slices)
    except Exception as e:
        log.error("查询失败: %s", e)
        raise _err(502, f"一张网查询失败：{e}。若为网关超时，常见原因是模糊词"
                        "太宽（如单查 doc_type）——换精确匹配或加更具体的词。")
    log.info("=== 查询完成 拉取 %s 条 用时 %.1fs ===",
             len(slices), time.monotonic() - t0)
    documents = aggregate_documents(slices)
    start = (page - 1) * page_size
    rows = documents[start:start + page_size]
    return {
        "documents": rows,
        "total_documents": len(documents),
        "page": page,
        "page_size": page_size,
        "slice_total_reported": total,
        "capped": (total or 0) >= _MAX_SLICES,
        "slices_pulled": len(slices),
    }


def _err(status: int, message: str):
    from fastapi import HTTPException
    return HTTPException(status_code=status, detail=message)


# ---------------------------------------------------------------- 前端（内嵌单页，无外部 CDN，内网可用）

PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>知识一张网 · 文档查询原型</title>
<style>
  body { font-family: "Microsoft YaHei", sans-serif; margin: 24px auto; max-width: 1100px;
         color: #1f2329; background: #f6f7f9; }
  h2 { margin: 0 0 4px; }
  .sub { color: #6b7075; font-size: 13px; margin-bottom: 16px; }
  .card { background: #fff; border: 1px solid #e3e5e8; border-radius: 8px;
          padding: 16px; margin-bottom: 14px; }
  .cond { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
  .cond select, .cond input { padding: 6px 8px; border: 1px solid #d4d7db;
                              border-radius: 6px; font-size: 13px; }
  .cond input { flex: 1; }
  button { padding: 6px 14px; border: 1px solid #d4d7db; background: #fff;
           border-radius: 6px; cursor: pointer; font-size: 13px; }
  button.primary { background: #2563eb; color: #fff; border-color: #2563eb; }
  button.mini { padding: 2px 8px; font-size: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #eef0f2; }
  th { background: #f3f5f7; color: #4e5359; font-weight: 600; }
  tr:hover td { background: #f8fafc; }
  .meta { color: #6b7075; font-size: 12px; margin: 8px 0; }
  .pager { display: flex; gap: 8px; align-items: center; margin-top: 12px; font-size: 13px; }
  .warn { color: #b45309; }
  code { background: #eef0f2; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
  .hits { font-weight: 600; }
</style>
</head>
<body>
<h2>知识一张网 · 文档查询</h2>
<div class="sub">多条件 AND；精确=整值全等（term），模糊=分词相关度（match）。
汇总按 <code>source_id</code> 聚合为文档行，命中切片数≈相关度。</div>

<div class="card">
  <div id="conds"></div>
  <div style="display:flex; gap:8px; margin-top:10px;">
    <button onclick="addCond()">+ 添加条件</button>
    <button class="primary" onclick="search(1)">搜 索</button>
  </div>
</div>

<div class="card">
  <div id="meta" class="meta">等待查询…</div>
  <div id="tableWrap"></div>
  <div class="pager" id="pager" style="display:none">
    <button onclick="goPage(-1)">上一页</button>
    <span id="pageInfo"></span>
    <button onclick="goPage(1)">下一页</button>
    <span style="margin-left:12px">每页</span>
    <select id="pageSize" onchange="search(1)">
      <option>20</option><option>50</option><option>100</option>
    </select>
  </div>
</div>

<script>
const FIELDS = [
  ["source_id","文档ID"], ["nid","切片ID"], ["url","资源链接"], ["title","标题"],
  ["path","章节目录"], ["content","切片内容"], ["source_site","来源站点"],
  ["file_name","文件名"], ["category_path","定义标签"], ["doc_name","文档名称"],
  ["doc_type","文档类型"], ["part_id","文档顺序"]];
const NUMERIC = new Set(["part_id"]);
let curPage = 1;

function addCond(field, fuzzy, content) {
  const div = document.createElement("div");
  div.className = "cond";
  const opts = FIELDS.map(([f, cn]) =>
      `<option value="${f}" ${f===field?"selected":""}>${cn} ${f}</option>`).join("");
  div.innerHTML = `
    <select onchange="onField(this)">${opts}</select>
    <select class="mode">
      <option value="0" ${!fuzzy?"selected":""}>精确</option>
      <option value="1" ${fuzzy?"selected":""}>模糊</option>
    </select>
    <input placeholder="匹配内容" value="${content||""}">
    <button class="mini" onclick="this.parentNode.remove()">删除</button>`;
  document.getElementById("conds").appendChild(div);
  onField(div.querySelector("select"));
}
function onField(sel) {
  const mode = sel.parentNode.querySelector(".mode");
  if (NUMERIC.has(sel.value)) { mode.value = "0"; mode.disabled = true; }
  else mode.disabled = false;
}
function collect() {
  return [...document.querySelectorAll(".cond")].map(d => {
    const [field, mode, input] = d.querySelectorAll("select,select,input");
    return { field: field.value, fuzzy: mode.value === "1", content: input.value.trim() };
  });
}
async function search(page) {
  curPage = page || 1;
  const conditions = collect().filter(c => c.content);
  if (!conditions.length) { alert("至少填一条条件"); return; }
  document.getElementById("meta").textContent = "查询中…（底层最多拉 10000 条切片）";
  try {
    const res = await fetch("/api/search", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ conditions, page: curPage,
                             page_size: +document.getElementById("pageSize").value }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.detail || res.status);
    }
    render(await res.json());
  } catch (e) {
    document.getElementById("meta").innerHTML =
      `<span class="warn">查询失败：${e.message}</span>`;
    document.getElementById("tableWrap").innerHTML = "";
    document.getElementById("pager").style.display = "none";
  }
}
function goPage(d) { search(curPage + d); }

function render(data) {
  const capped = data.capped
    ? `<span class="warn">（命中切片 ≥10000 已封顶，仅基于前 ${data.slices_pulled} 条汇总）</span>` : "";
  document.getElementById("meta").innerHTML =
    `命中切片 ${data.slice_total_reported}${capped} · 实际拉取 ${data.slices_pulled} · ` +
    `去重文档 <b>${data.total_documents}</b> 篇（按 source_id 汇总，相关度排序）`;
  const rows = data.documents.map(d => `
    <tr>
      <td>${esc(d.doc_name) || "-"}</td>
      <td><code>${esc(d.source_id)}</code></td>
      <td>${esc(joinA(d.product_line))}</td>
      <td>${esc(d.parsed_version) || "-"}</td>
      <td>${esc(d.publish_time) || "-"}</td>
      <td class="hits">${d.slice_hits}</td>
      <td>${d.sample_titles.map(esc).join("；") || "-"}</td>
    </tr>`).join("");
  document.getElementById("tableWrap").innerHTML = `
    <table>
      <tr><th>文档名</th><th>source_id</th><th>产品线</th><th>版本</th>
          <th>发布</th><th>命中切片</th><th>命中章节样例</th></tr>
      ${rows || '<tr><td colspan="7">无命中</td></tr>'}
    </table>`;
  const pages = Math.max(1, Math.ceil(data.total_documents / data.page_size));
  document.getElementById("pager").style.display = "";
  document.getElementById("pageInfo").textContent = `第 ${data.page} / ${pages} 页`;
  document.getElementById("pager").querySelectorAll("button")[0].disabled = data.page <= 1;
  document.getElementById("pager").querySelectorAll("button")[1].disabled = data.page >= pages;
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function joinA(v) { return Array.isArray(v) ? v.join(" / ") : (v ?? ""); }

// 默认查询：来源站点 support（大部分产品文档在此）+ 文档名称精确（主路径）。
// 来源站点行也可删——想搜全站时去掉即可。
addCond("source_site", false, "support");
addCond("doc_name", false, "");
</script>
</body>
</html>
"""


if __name__ == "__main__":
    # 本地快速自检（不打内网）：离线聚合逻辑冒烟
    if "--selftest" in sys.argv:
        docs = aggregate_documents([
            {"source_id": "D1", "doc_name": "A 手册", "title": "t1", "part_id": 1},
            {"source_id": "D1", "doc_name": "A 手册", "title": "t2", "part_id": 2},
            {"source_id": "D2", "doc_name": "B 手册", "title": "t3", "part_id": 3},
        ])
        assert [d["slice_hits"] for d in docs] == [2, 1], docs
        print("selftest ok:", docs)
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8899)
