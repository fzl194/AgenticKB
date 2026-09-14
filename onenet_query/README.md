# 知识一张网 · 文档查询原型（独立系统）

> 定位：**单独开发验证**「多条件查询 → 文档级汇总 → 分页」链路，满足需求后再迁移进主项目。
> 刻意不做：数据库、挖掘衔接、前后端分离。**零新增依赖**（fastapi/uvicorn/httpx/PyYAML 项目环境已有）。

## 运行

```bash
cd onenet_query

# 凭据（二选一）：
#   1) 环境变量：ONENET_APP_ID / ONENET_STATIC_TOKEN
#   2) 本目录放 onenet.yaml：
#        app_id: 'xxx'
#        static_token: 'xxx'
#      （也会自动回落读 ../main_control_service/config/system/onenet.yaml）

python app.py                # 或 python -m uvicorn app:app --host 0.0.0.0 --port 8899
# 浏览器打开 http://<ip>:8899
```

离线自检（不打内网，只测聚合逻辑）：`python app.py --selftest`

## 查询语义

- 多条件 **AND**；每条条件是三元组：`字段 + 精确/模糊 + 内容`
- **精确** = 整值全等（term）；**模糊** = 分词相关度（match，结果按相关度排序）
- `part_id` 是数字字段：模糊无意义，界面自动锁定为精确
- 底层一次查询最多拉 **10000 条切片**（接口硬上限 from+size ≤ 10000），
  翻页拉满后**按 source_id 聚合**为文档行 → 文档列表分页（每页 20/50/100）
- 汇总字段（文档名/版本/发布/产品线）取该文档首条命中切片（文档级字段在同源切片上恒同）

## 结果列

| 列 | 含义 |
|---|---|
| 文档名 / source_id / 产品线 / 版本 / 发布 | 文档级元数据 |
| 命中切片 | 该文档本次命中的切片条数（相关度指示，非文档总量） |
| 命中章节样例 | 该文档命中的前 3 个切片标题 |

## 已知边界（一张网接口限制，非本原型缺陷）

- 命中切片 ≥10000 时封顶，仅基于实际拉取量汇总（页面有黄色提示）
- `path` 的精确=整串全等，没有"路径前缀"语义（ES 限制）
- `url` 是包级字段（同源切片相同），精确搜 url ≈ 搜 source_id
- `nid / path / category_path / source_site / part_id` 未经 kone_connector 回归实测，机制同源、预期可用
