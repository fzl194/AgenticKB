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
import re
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

    def query_dsl(self, dsl: dict) -> dict:
        """DSL 模式（仅第二步 TOC 用）：三字段投影 / part_id range 分段 /
        track_total_hits 是原生模式没有的能力（主项目 toc_scan 同款依赖）。"""
        raw = dsl if isinstance(dsl, str) else json.dumps(dsl, ensure_ascii=False)
        return self._post({"dsl": raw})

    # ---- 以下三个方法逐字对齐 kone_connector（count/part_range/fetch_chunk）----

    def count_source(self, source_id: str) -> int:
        dsl = {"query": {"bool": {"must": [
                   {"term": {"source_id.keyword": {"value": source_id}}}]}},
               "track_total_hits": True, "from": 0, "size": 1}
        return int(self.query_dsl(dsl).get("total") or 0)

    def part_range(self, source_id: str) -> dict:
        out = {}
        for order, key in (("asc", "min"), ("desc", "max")):
            dsl = {"query": {"bool": {"must": [
                       {"term": {"source_id.keyword": {"value": source_id}}}]}},
                   "from": 0, "size": 1,
                   "sort": [{"part_id": order}, {"nid.keyword": order}]}
            res = self.query_dsl(dsl).get("searchResults") or []
            out[key] = int(res[0]["part_id"]) if res else None
        return out

    def fetch_all(self, source_id: str,
                  fields: list[str] | None = None) -> list[dict]:
        """per_file 编号文档的全量拉取（无 range）：query term source_id +
        稳定双键排序，from/size 翻页——part_id 跨文件重复时 range 分段会漏。"""
        q: dict = {"query": {"bool": {"must": [
                     {"term": {"source_id.keyword": {"value": source_id}}}]}},
                   "track_total_hits": True,
                   "sort": [{"part_id": "asc"}, {"nid.keyword": "asc"}]}
        if fields:
            q["_source"] = fields
        out, frm, page_size = [], 0, 5000
        while True:
            q["from"], q["size"] = frm, page_size
            res = self.query_dsl(q).get("searchResults") or []
            out.extend(res)
            frm += len(res)
            if len(res) < page_size:
                break
        return out

    def fetch_chunk(self, source_id: str, lo: int, hi: int,
                    fields: list[str] | None = None,
                    page_size: int = 5000) -> list[dict]:
        """拉取 part_id ∈ [lo,hi] 切片（对齐 fetch_source_chunk：range 分段 +
        稳定双键排序，绕过 from+size≤10000 硬顶——大文档全量拉取的唯一路径）。
        fields=None 拉全字段（全量获取用）；投影列表用于轻量扫描。
        仅适用于 HWICS/CHM 全局编号文档；per_file 编号文档用 fetch_all。"""
        q = {"query": {"bool": {"must": [
                 {"term": {"source_id.keyword": {"value": source_id}}},
                 {"range": {"part_id": {"gte": lo, "lte": hi}}}]}},
             "track_total_hits": True,
             "sort": [{"part_id": "asc"}, {"nid.keyword": "asc"}]}
        if fields:
            q["_source"] = fields
        out, frm = [], 0
        while True:
            q["from"], q["size"] = frm, page_size
            res = self.query_dsl(q).get("searchResults") or []
            out.extend(res)
            frm += len(res)
            if len(res) < page_size:
                break
        return out


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


# ---------------------------------------------------------------- 章节目录（第二步）
# 自主项目 mining/onenet/restore.py#build_path_tree 移植：
# path 以 " > " 分段 → 树；切片挂在其完整 path 的叶子节点；剔除包名首段。


def split_path(path: str | None) -> list[str]:
    return [p.strip() for p in (path or "").split(">") if p.strip()]


def build_path_tree(slices: list[dict]) -> dict:
    root: dict = {"title": "__ROOT__", "path": "", "depth": 0,
                  "children": {}, "slice_count": 0}
    for srow in slices:
        segs = split_path(srow.get("path"))
        if len(segs) >= 2:
            segs = segs[1:]              # 剔除包名首段
        if not segs:
            continue
        node = root
        path_so_far = ""
        for i, seg in enumerate(segs, start=1):
            path_so_far = f"{path_so_far} > {seg}" if path_so_far else seg
            node["slice_count"] += 1     # 祖先累计后代切片数
            node = node["children"].setdefault(
                seg, {"title": seg, "path": path_so_far, "depth": i,
                      "children": {}, "slice_count": 0})
    return root


def tree_to_dicts(root: dict) -> list[dict]:
    def to_dict(node: dict) -> dict:
        return {"title": node["title"], "path": node["path"],
                "depth": node["depth"], "slice_count": node["slice_count"],
                "children": [to_dict(c) for c in node["children"].values()]}
    return [to_dict(c) for c in root["children"].values()]


# ---------------------------------------------------------------- 移植：主项目 restore.py
# β 规则章节重建（rule_version=beta-1）：path[-2]=文件、path[-1]=文件内标题、
# α 兜底；文件内 part_id 升序、文件间 min(part_id) 排序；目录=文件之上层级。
RULE_VERSION = "beta-1"
TBL_RE = re.compile(r"\[tbl_predict_(?:start|end)\]")


def clean_content(content: str | None) -> str:
    return TBL_RE.sub("", content or "").strip()


def render_file_markdown(slices: list[dict], title: str = "") -> str:
    """单文件切片 → markdown（带 nid 回源标记，标注逻辑文档）。"""
    lines = ["<!-- 由一张网切片重建的逻辑文档（非原始文件） -->", ""]
    heading = title or (slices[0].get("title") if slices else "") or ""
    if heading:
        lines.append(f"# {heading}")
    for sr in sorted(slices, key=lambda x: int(x.get("part_id") or 0)):
        lines.append(f"<!-- nid={sr.get('nid')} part_id={sr.get('part_id')} -->")
        lines.append(clean_content(sr.get("content")))
        lines.append("")
    return "\n".join(lines) + "\n"


def restore_files(slices: list[dict]) -> dict:
    """β 规则还原（对齐 mining/onenet/restore.py#restore_files）。"""
    by_file: dict[str, list[dict]] = {}
    headings: dict[str, str] = {}
    unassigned = 0
    for sr in sorted(slices, key=lambda x: int(x.get("part_id") or 0)):
        segs = split_path(sr.get("path"))
        if not segs:
            unassigned += 1
            continue
        if len(segs) >= 2:
            segs = segs[1:]                    # 剔包名
        if len(segs) >= 2:
            file_path, heading = " > ".join(segs[:-1]), segs[-1]
        else:
            file_path, heading = segs[0], segs[0]   # α 兜底
        by_file.setdefault(file_path, []).append(sr)
        headings.setdefault(file_path, heading)

    folders: set[str] = set()
    files = []
    for file_path, rows in by_file.items():
        fsegs = file_path.split(" > ")
        for i in range(1, len(fsegs)):
            folders.add("/".join(fsegs[:i]))
        parts = [int(r.get("part_id") or 0) for r in rows]
        files.append({
            "file_path": file_path,
            "file_title": fsegs[-1],
            "heading_title": headings[file_path],
            "folder_path": "/".join(fsegs[:-1]),
            "slice_count": len(rows),
            "part_min": min(parts), "part_max": max(parts),
        })
    files.sort(key=lambda f: f["part_min"])
    return {"rule_version": RULE_VERSION, "files": files,
            "folders": sorted(folders),
            "slice_count": len(slices) - unassigned, "unassigned": unassigned}


# ---------------------------------------------------------------- 移植：主项目 fetch.py 语义
# 段幂等全量获取（对齐 mining/onenet/fetch.py#fetch_selection）：
# parts/part_<lo>_<hi>.jsonl 存在即跳过（断点续传）→ 合并 → 校验 → manifest。


class Selection:
    """导入勾选范围（对齐主项目 fetch.Selection：subtrees 为 path 前缀段列表）。"""

    def __init__(self, subtrees: tuple[str, ...] = (),
                 max_part_id: int | None = None):
        self.subtrees = tuple(dict.fromkeys(subtrees))
        self.max_part_id = max_part_id

    def to_dict(self) -> dict:
        return {"subtrees": list(self.subtrees), "max_part_id": self.max_part_id}

    def matches_path(self, path: str | None) -> bool:
        if not self.subtrees:
            return True
        segs = split_path(path)
        if len(segs) >= 2:
            segs = segs[1:]
        for prefix in self.subtrees:
            psegs = split_path(prefix)
            if segs[:len(psegs)] == psegs:
                return True
        return False


def _ws(source_id: str) -> Path:
    return Path(__file__).parent / "workspace" / source_id


def verify_slices(slices: list[dict]) -> dict:
    """nid/part 去重 + 1..max 覆盖（对齐 fetch.verify_file 整包口径）。"""
    seen_nid: set[str] = set()
    seen_part: set[int] = set()
    dup_nid = dup_part = 0
    for sr in slices:
        nid = str(sr.get("nid") or "")
        pid = int(sr.get("part_id") or -1)
        if nid in seen_nid:
            dup_nid += 1
        seen_nid.add(nid)
        if pid in seen_part:
            dup_part += 1
        seen_part.add(pid)
    # part_id 语义（kone_connector 实测）：HWICS/CHM 大文档全局唯一连续；
    # 小文档（docx 解包，如 DOC1101700755）按文件内编号、跨文件重复——
    # dup_part 是合法形态（实测 dup_part=5183），此时覆盖检查无意义。
    # nid 是唯一硬主键；只有 dup_nid 与「全局编号下的缺口」是硬错误。
    got_max = max(seen_part) if seen_part else 0
    per_file = dup_part > 0
    missing = 0 if per_file else sum(
        1 for i in range(1, got_max + 1) if i not in seen_part)
    if dup_nid > 0 or missing > 0:
        raise RuntimeError(
            f"批次校验未通过: dup_nid={dup_nid} missing={missing}"
            + (f"（dup_part={dup_part} 属文件内编号形态，已容忍）" if per_file else ""))
    return {"ok": True, "slices": len(slices), "part_max": got_max,
            "part_id_numbering": "per_file" if per_file else "global",
            "dup_part_tolerated": dup_part}


# ---------------------------------------------------------------- 获取任务（线程 + 磁盘持久）

import threading

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _job(source_id: str) -> dict:
    with _jobs_lock:
        return _jobs.setdefault(source_id, {"status": "none", "progress": "",
                                            "total": 0, "error": None})


def _run_fetch(source_id: str) -> None:
    job = _job(source_id)
    try:
        job.update(status="running", error=None)
        client = get_client()
        total = client.count_source(source_id)
        if total <= 0:
            raise RuntimeError(f"source 无切片: {source_id}")
        pr = client.part_range(source_id)
        lo = int(pr.get("min") or 1)
        hi = int(pr.get("max") or total)
        job["total"] = total
        ws = _ws(source_id)
        parts_dir = ws / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)

        # 分段拉取（段文件幂等：存在即跳过——断点续传）
        seg_lo = lo
        while seg_lo <= hi:
            seg_hi = min(seg_lo + 10000 - 1, hi)
            part_path = parts_dir / f"part_{seg_lo}_{seg_hi}.jsonl"
            if not part_path.exists():
                rows = client.fetch_chunk(source_id, seg_lo, seg_hi)
                with part_path.open("w", encoding="utf-8") as fh:
                    for row in rows:
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            job["progress"] = f"{seg_hi}/{hi}"
            seg_lo = seg_hi + 1
            time.sleep(0.1)

        # 合并 → 校验 → slices.jsonl + manifest
        slices: list[dict] = []
        for pf in sorted(parts_dir.glob("part_*.jsonl")):
            with pf.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        slices.append(json.loads(line))
        verify = verify_slices(slices)
        data_path = ws / "slices.jsonl"
        with data_path.open("w", encoding="utf-8") as fh:
            for row in slices:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        (ws / "manifest.json").write_text(json.dumps({
            "source_id": source_id, "doc_total_slices": total,
            "part_id_range": pr, "lines_in_jsonl": len(slices),
            "verify": verify, "rule_version": RULE_VERSION,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        job.update(status="done", progress=f"{hi}/{hi}")
        log.info("文档全量获取完成 %s: %s 切片", source_id, len(slices))
    except Exception as e:  # noqa: BLE001
        job.update(status="failed", error=str(e))
        log.exception("文档全量获取失败 %s", source_id)


def _load_workspace_slices(source_id: str) -> list[dict]:
    data = _ws(source_id) / "slices.jsonl"
    if not data.exists():
        raise RuntimeError("尚未完成全量获取")
    out = []
    with data.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------- API

app = FastAPI(title="知识一张网 · 文档查询原型")


@app.post("/api/toc")
def api_toc(body: dict[str, Any]) -> dict:
    """第二步：按 source_id 构建章节目录树。"""
    source_id = str(body.get("source_id") or "").strip()
    if not source_id:
        raise _err(422, "source_id required")
    max_part_id = body.get("max_part_id")
    t0 = time.monotonic()
    log.info("=== TOC 扫描开始 source_id=%s max_part_id=%s ===", source_id, max_part_id)
    try:
        out = scan_toc(source_id,
                       int(max_part_id) if max_part_id else None)
    except Exception as e:
        log.error("TOC 扫描失败: %s", e)
        raise _err(502, f"章节目录构建失败：{e}")
    log.info("=== TOC 扫描完成 %s 节点 / %s 切片，用时 %.1fs ===",
             out["nodes"], out["scanned_slices"], time.monotonic() - t0)
    return out


# ---------------------------------------------------------------- 第二步 API

@app.post("/api/document/fetch")
def api_document_fetch(body: dict[str, Any]) -> dict:
    """瞄准一篇文档：全量获取（后台线程，段幂等断点续传）。"""
    source_id = str(body.get("source_id") or "").strip()
    if not source_id:
        raise _err(422, "source_id required")
    job = _job(source_id)
    if job["status"] == "running":
        return {"status": "running", "progress": job["progress"]}
    threading.Thread(target=_run_fetch, args=(source_id,),
                     daemon=True).start()
    return {"status": "started"}


@app.get("/api/document/status")
def api_document_status(source_id: str) -> dict:
    job = _job(source_id)
    return {"status": job["status"], "progress": job["progress"],
            "total": job["total"], "error": job["error"]}


@app.get("/api/document/result")
def api_document_result(source_id: str) -> dict:
    """β 章节重建结果：概要 + 完整章节树（勾选用）+ 文件清单（导入单位）。"""
    slices = _load_workspace_slices(source_id)
    restored = restore_files(slices)
    root = build_path_tree(slices)

    def count_nodes(node: dict) -> int:
        n = 1 if node["depth"] > 0 else 0
        return n + sum(count_nodes(c) for c in node["children"].values())

    parsed_version = next((str(r.get("parsed_version")) for r in slices
                           if r.get("parsed_version")), None)
    return {
        "source_id": source_id,
        "total_slices": len(slices),
        "parsed_version": parsed_version,
        "rule_version": restored["rule_version"],
        "file_count": len(restored["files"]),
        "folder_count": len(restored["folders"]),
        "unassigned": restored["unassigned"],
        "nodes": count_nodes(root),
        "tree": tree_to_dicts(root),
        "files": restored["files"],
    }


@app.post("/api/document/preview")
def api_document_preview(body: dict[str, Any]) -> dict:
    """单文件 markdown 预览（β 还原文件，带 nid 回源标记）。"""
    source_id = str(body.get("source_id") or "").strip()
    file_path = str(body.get("file_path") or "").strip()
    slices = _load_workspace_slices(source_id)
    restored = restore_files(slices)
    target = next((f for f in restored["files"]
                   if f["file_path"] == file_path), None)
    if target is None:
        raise _err(404, f"文件不存在: {file_path}")
    # 行过滤按 β 归属规则（与 restore_files 的分组逻辑逐字一致）：
    # 切片剔包名后 segs[:-1] 即其所属文件路径（α 兜底时 segs[0] 即文件）
    rows = []
    for r in slices:
        segs = split_path(r.get("path"))
        if not segs:
            continue
        if len(segs) >= 2:
            segs = segs[1:]
        fp = " > ".join(segs[:-1]) if len(segs) >= 2 else segs[0]
        if fp == file_path:
            rows.append(r)
    return {"file": target,
            "markdown": render_file_markdown(rows, target["file_title"])}


@app.post("/api/document/select")
def api_document_select(body: dict[str, Any]) -> dict:
    """章节勾选 → 主项目 pipeline 可消费的选择载荷（Selection 语义）。

    载荷即未来 import 的 selection 参数；匹配的 β 文件清单 = 导入单位预览。
    同时落盘 workspace/<source_id>/selection.json。
    """
    source_id = str(body.get("source_id") or "").strip()
    subtrees = [str(x).strip() for x in (body.get("subtrees") or [])
                if str(x).strip()]
    if not source_id:
        raise _err(422, "source_id required")
    selection = Selection(tuple(subtrees))
    slices = _load_workspace_slices(source_id)
    restored = restore_files(slices)

    # 匹配切片（Selection 前缀语义）→ 归并到文件
    matched_nids = {str(r.get("nid")) for r in slices
                    if selection.matches_path(r.get("path"))}
    matched_files = []
    for f in restored["files"]:
        fsegs = f["file_path"].split(" > ")
        file_prefix = " > ".join(fsegs)
        # 文件或其任一祖先目录被勾选，或文件本身在子树内
        hit = any(file_prefix == p or file_prefix.startswith(p + " > ")
                  for p in subtrees)
        if hit:
            matched_files.append(f)
    matched_slices = sum(f["slice_count"] for f in matched_files)
    payload = {
        "source_id": source_id,
        "selection": selection.to_dict(),
        "matched_file_count": len(matched_files),
        "matched_slice_count": matched_slices,
        "matched_nid_count": len(matched_nids),
        "matched_files": matched_files,
    }
    (_ws(source_id) / "selection.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


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
      <td><button class="mini" onclick="enterDoc('${esc(d.source_id)}')">进入文档</button></td>
    </tr>`).join("");
  document.getElementById("tableWrap").innerHTML = `
    <table>
      <tr><th>文档名</th><th>source_id</th><th>产品线</th><th>版本</th>
          <th>发布</th><th>命中切片</th><th>命中章节样例</th><th>第二步</th></tr>
      ${rows || '<tr><td colspan="8">无命中</td></tr>'}
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

<div class="card" id="docCard" style="display:none">
  <div style="display:flex; justify-content:space-between; align-items:center">
    <b id="docTitle">文档工作台</b>
    <button class="mini" onclick="document.getElementById('docCard').style.display='none'">关闭</button>
  </div>
  <div class="meta" id="docMeta"></div>
  <div id="docActions"></div>

  <div id="docBody" style="display:none">
    <div style="display:flex; gap:10px; margin-bottom:8px; align-items:center">
      <button class="mini" onclick="toggleAll(true)">展开全部</button>
      <button class="mini" onclick="toggleAll(false)">收起</button>
      <span class="meta" style="margin:0">勾选父节点 = 选中整个子树（Selection 前缀语义）</span>
      <button class="primary mini" id="selBtn" onclick="makeSelection()">生成导入选择（<span id="selCount">0</span>）</button>
    </div>
    <div style="display:flex; gap:14px">
      <div id="docTree" style="flex:1; max-height:55vh; overflow:auto; font-size:13px;
           border:1px solid #eef0f2; border-radius:6px; padding:8px"></div>
      <div style="flex:1; max-height:55vh; overflow:auto">
        <b style="font-size:13px">β 还原文件清单（导入单位）</b>
        <table id="docFiles" style="margin-top:6px"></table>
      </div>
    </div>
    <div id="selResult" class="meta" style="margin-top:10px"></div>
  </div>
</div>

<dialog id="previewDlg" style="border:1px solid #d4d7db; border-radius:8px;
        width:70%; max-height:80vh">
  <div style="display:flex; justify-content:space-between; align-items:center">
    <b id="previewTitle" style="font-size:14px"></b>
    <button class="mini" onclick="previewDlg.close()">关闭</button>
  </div>
  <pre id="previewBody" style="white-space:pre-wrap; max-height:65vh; overflow:auto;
       font-size:12px; line-height:1.6; background:#f6f7f9; padding:10px;
       border-radius:6px; margin-top:8px"></pre>
</dialog>

<script>
let curDoc = "", selPaths = new Set(), pollTimer = null, treePaths = [];

async function enterDoc(sourceId) {
  curDoc = sourceId;
  selPaths = new Set();
  clearInterval(pollTimer);
  document.getElementById("docCard").style.display = "";
  document.getElementById("docTitle").textContent = "文档工作台 · " + sourceId;
  document.getElementById("docBody").style.display = "none";
  await pollStatus();
}
async function pollStatus() {
  let st;
  try {
    st = await (await fetch("/api/document/status?source_id=" + curDoc)).json();
  } catch (e) { st = {status: "none"}; }
  const meta = document.getElementById("docMeta");
  const actions = document.getElementById("docActions");
  clearInterval(pollTimer);
  if (st.status === "running") {
    meta.innerHTML = '<span class="warn">全量获取中… 进度 part ' + (st.progress || "?") +
                     "（段幂等，可中断重跑）</span>";
    actions.innerHTML = "";
    pollTimer = setInterval(pollStatus, 2000);
    return;
  }
  if (st.status === "failed") {
    meta.innerHTML = '<span class="warn">获取失败：' + esc(st.error || "") + "</span>";
    actions.innerHTML = "";
    const btn = document.createElement("button");
    btn.className = "primary mini";
    btn.textContent = "重试";
    btn.onclick = startFetch;
    actions.appendChild(btn);
    return;
  }
  if (st.status === "none") {
    meta.innerHTML = "尚未获取——点击开始全量拉取（大文档约 1-3 分钟）";
    actions.innerHTML = "";
    const btn = document.createElement("button");
    btn.className = "primary mini";
    btn.textContent = "开始全量获取";
    btn.onclick = startFetch;
    actions.appendChild(btn);
    return;
  }
  meta.innerHTML = "已获取，正在重建章节…";
  actions.innerHTML = "";
  const r = await fetch("/api/document/result?source_id=" + curDoc);
  if (!r.ok) {
    meta.innerHTML = '<span class="warn">重建失败：' + esc(await r.text()) + "</span>";
    return;
  }
  renderDoc(await r.json());
}
async function startFetch() {
  await fetch("/api/document/fetch", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({source_id: curDoc})});
  document.getElementById("docMeta").textContent = "全量获取启动…";
  pollTimer = setInterval(pollStatus, 2000);
}
function renderDoc(t) {
  document.getElementById("docMeta").innerHTML =
    "切片 " + t.total_slices + " · 章节节点 " + t.nodes +
    " · β 还原文件 <b>" + t.file_count + "</b> · 目录 " + t.folder_count +
    " · 解析版本 " + (t.parsed_version || "-") +
    "（规则 " + t.rule_version + "）" +
    (t.unassigned ? '<span class="warn"> · 未归属切片 ' + t.unassigned + "</span>" : "");
  document.getElementById("docBody").style.display = "";
  // 章节树（勾选走事件绑定 + 索引表，路径不进 HTML 属性）
  treePaths = [];
  document.getElementById("docTree").innerHTML =
    t.tree.map(n => treeNode(n, n.depth <= 2)).join("");
  document.querySelectorAll("#docTree input.tcb").forEach(cb => {
    const path = treePaths[+cb.dataset.i];
    cb.checked = selPaths.has(path);
    cb.onchange = () => {
      if (cb.checked) selPaths.add(path); else selPaths.delete(path);
      updateSelCount();
    };
  });
  // 文件清单（预览走事件绑定，索引对齐 files 顺序）
  const ft = document.getElementById("docFiles");
  ft.innerHTML =
    "<tr><th>文件</th><th>目录</th><th>切片</th><th>part 范围</th><th></th></tr>" +
    t.files.map(f =>
      "<tr><td>" + esc(f.file_title) + "</td><td>" + esc(f.folder_path || "-") +
      "</td><td>" + f.slice_count + "</td><td>" + f.part_min + "~" + f.part_max +
      '</td><td><button class="mini pv">预览</button></td></tr>').join("");
  ft.querySelectorAll("button.pv").forEach((btn, i) => {
    btn.onclick = () => previewFile(t.files[i].file_path);
  });
  updateSelCount();
}
function treeNode(n, open) {
  const idx = treePaths.push(n.path) - 1;
  const kids = n.children || [];
  return '<details ' + (open ? "open" : "") + ' style="margin-left:' +
    ((n.depth - 1) * 12) + 'px">' +
    '<summary style="cursor:pointer"><input type="checkbox" class="tcb" data-i="' +
    idx + '"/> ' + esc(n.title) +
    ' <span style="color:#6b7075">' + n.slice_count + ' 片</span></summary>' +
    (kids.length ? kids.map(k => treeNode(k, open)).join("") : "") +
    "</details>";
}
function updateSelCount() {
  document.getElementById("selCount").textContent = selPaths.size;
}
async function makeSelection() {
  if (!selPaths.size) { alert("先在目录树勾选章节（不勾 = 整包）"); return; }
  const r = await fetch("/api/document/select", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({source_id: curDoc, subtrees: [...selPaths]})});
  const out = await r.json();
  if (!r.ok) { alert(out.detail || r.status); return; }
  document.getElementById("selResult").innerHTML =
    "<b>导入选择已生成</b>（落盘 workspace/" + esc(curDoc) + "/selection.json，" +
    "即主项目 pipeline 的 selection 参数）：匹配文件 <b>" + out.matched_file_count +
    "</b> 篇 / 切片 " + out.matched_slice_count + " 条<br>" +
    '<code style="font-size:12px">' + esc(JSON.stringify(out.selection)) + "</code>";
}
async function previewFile(filePath) {
  const dlg = document.getElementById("previewDlg");
  document.getElementById("previewTitle").textContent = filePath;
  document.getElementById("previewBody").textContent = "加载中…";
  dlg.showModal();
  const r = await fetch("/api/document/preview", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({source_id: curDoc, file_path: filePath})});
  const out = await r.json();
  document.getElementById("previewBody").textContent =
    r.ok ? out.markdown : "加载失败：" + (out.detail || r.status);
}
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
