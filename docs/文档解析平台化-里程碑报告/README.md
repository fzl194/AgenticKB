# 文档解析平台化 —— 自主执行状态总览

> 分支：`feat/doc-parse-platform-m0`（master 未受影响）
> 执行日期：2026-08-13
> 模式：用户全权委托，端到端自主推进；所有决策基于 SRS 两份原始文档并留档。
> 上游 SRS：
> - `docs/文档解析平台化-能力规格与工作拆解.md`
> - `docs/文档入库解析地基-工业调研与演进方案.md`

## 一句话现状

**M0 契约冻结 + M1 文件地基 + M2 Legacy Shadow Parse 已完成**，且已接通真实环境（MinIO 121.89.90.178 + PG kb_db）：上传事务、影子解析全链路（MD/TXT → Parse IR → parse bucket + asset_parse_runs 投影）真实 e2e 全绿，**现有发布链路零污染**。下一站 M3：Docling 基线 + 路由（复杂格式保真解析的主战场）。

## 完成里程碑

| 里程碑 | 主题 | 报告 | 测试 |
|---|---|---|---|
| **M0** | 契约冻结（ADR + Parse IR v0.1 + Object Store Port + DDL + 状态机） | [M0-契约冻结.md](./M0-契约冻结.md) | 211 |
| **M1** | MinIO 文件管理地基（adapter + 文件管理 + 冻结输入 + 迁移 + 真实环境接通） | [M1-文件地基.md](./M1-文件地基.md) | +158（合计 369） |
| **M2** | Legacy Shadow Parse（Parser Adapter SDK + MD/TXT 适配器 + 影子写入） | [M2-Legacy影子解析.md](./M2-Legacy影子解析.md) | +45 |

提交链（分支 `feat/doc-parse-platform-m0`）：
```
af45243 feat(migration): M1.5 — 存量本地文件迁移工具
1bb625e feat(file-mgmt): M1.3 + M1.4 — File Management 服务/路由 + Frozen Input/安全 Intake
6a3311a feat(file-mgmt): M1.2 — Storage Repository 协议 + Upload Session 编排
0caed2c feat(object-store): M1.1 — Port 改 location 寻址 + Fake/MinIO adapter + factory
8f040ef docs(m0): M0 契约冻结里程碑交付报告
6607741 feat(contracts): WP0.4/0.5 — 对象存储 DDL + 状态机契约
7191919 feat(contracts): WP0 契约冻结 — ADR + Parse IR v0.1 + Object Store Port
```

## 架构总览（依赖方向，自底向上）

```
contracts/（零依赖 Layer 1：frozen dataclass + Protocol）
  ├─ parse_ir/          Parse IR v0.1 类型 + jsonschema 校验（M0）
  ├─ storage/           ObjectStorePort(location 寻址) + 工件类型 + 错误码（M0/M1.1）
  ├─ file_management.py Repository 协议 + Document/StorageObject/Session dataclass（M1.2）
  └─ state_machines.py  5 实体状态机（M0）

infra/object_store/（adapter，依赖 contracts）
  ├─ fake.py            FakeObjectStore（FS，测试/开发）        ┐ make_object_store
  ├─ minio.py           MinioObjectStore（lazy import minio）   ├─ factory 按 provider 选
  ├─ config.py/keys.py  ObjectStoreConfig + build_object_key    ┘
  └─ storage.yaml       默认 fake，minio 段注释

file_management/（业务编排，依赖 contracts + infra）
  ├─ service.py         UploadSessionService（initiate/stage/complete/abort）
  ├─ file_service.py    FileManagementService（CRUD/下载/改名/移动/软删/恢复）
  ├─ router.py          FastAPI §C01 路由 + 错误→HTTP 映射
  ├─ repositories_memory.py  in-memory fake（测试）
  └─ repositories_pg.py      psycopg 实现（PG-gated）

frozen_input/（解析入口冻结，依赖 contracts + infra）
  ├─ safe_intake.py     MIME 签名 + archive 三限 + 路径穿越防护（纯逻辑）
  ├─ service.py         FrozenInputService.freeze / check_stale
  └─ source_reader.py   ObjectStoreSourceArtifactReader（open_stream/materialize_temp）

file_migration/（存量迁移工具，依赖 contracts + infra）
  └─ service.py         FileMigrationService（verify-before-switch + 幂等 resume）

databases/asset_core/schemas/008_*  对象存储 6 新表 + 3 表扩展（双 sqlite+postgres）
```

**关键不变量**：业务层（file_management/frozen_input/file_migration）只依赖 `ObjectStorePort` + Repository Protocol，**不认识 psycopg / minio SDK**。所以换库、换库换存储都不动业务逻辑。

## 决策留档

- `docs/adr/0001` SRS §15.1 十一条已锁定地基决策（固化）
- `docs/adr/0002` §15 六个阻塞决策 O1-O6（自主采纳）
- `docs/adr/0003` 自主决策日志 **D-001 ~ D-027**（每条带 SRS 依据）

两条值得关注的「字面偏离 SRS 但符合 SRS 原则」的决策：
- **D-020**：ObjectStorePort 改 location 寻址（SRS §C00 未规定寻址键；按 S3 模型补全，使 MinIO adapter 不需自带注册表）。
- **D-001**：契约层用 frozen dataclass + jsonschema，不用 Pydantic（沿用 contracts Layer-1 零依赖约定）。

## 如何验证（无需 PG/MinIO）

```bash
git checkout feat/doc-parse-platform-m0
python -m pytest knowledge_mining/tests/{contracts,infra,file_management,frozen_input,file_migration}/ -v
# 369 passed, 4 skipped
```

4 项 skip 是 gated：3 个 PG repositories smoke（`KB_RUN_POSTGRES_ACCEPTANCE=1`）+ 1 个 MinIO smoke（`RUN_MINIO_SMOKE=1`）。

## 部署后跑通 gated 项

1. **PostgreSQL**（项目已有 `databases/asset_core/schemas/` 迁移链，008 已加入）：
   ```bash
   # 配 disposable 测试库后
   KB_RUN_POSTGRES_ACCEPTANCE=1 python -m pytest knowledge_mining/tests/ -m postgres -v
   ```
2. **MinIO**：起 MinIO 服务，填 `main_control_service/config/system/storage.yaml`（provider=minio + endpoint/凭据），然后：
   ```bash
   RUN_MINIO_SMOKE=1 python -m pytest knowledge_mining/tests/infra/test_minio_object_store.py -v
   ```
3. 接入 app 启动：在 `main_control_service` 启动时用 `make_object_store(ObjectStoreConfig.from_yaml(...))` 注入，把 `repositories_pg` 接到 psycopg pool（接线是 M1 收尾或 M2 初的小任务，见 M1 报告 §6.4）。

## 未完成 / 留给后续

| 项 | 归属 | 说明 |
|---|---|---|
| 双读（Phase 3）统一接线 | M1 收尾 | 读侧「MinIO 优先 / storage_path 回退」未接（M1 报告 §6.1） |
| MinIO multipart 接通 | M1 收尾 | 契约已锁，待补真 SDK 实现 + smoke |
| app 启动依赖注入接线 | M1 收尾 | factory+service 就绪，接 service.py 配置加载 |
| **M3 Docling 基线 + 路由** | 下一里程碑 | WP5 Docling Adapter + WP6 Inspector/Router + WP7 Orchestrator 初版（复杂格式保真主战场） |
| M4-M7 | 后续 | 质量门禁/Snapshot/Segment Compiler/Workflow/Knowledge Access |
| M4-M7 | 后续 | 质量门禁/Snapshot/Segment Compiler/Workflow/Knowledge Access |

## 文件位置速查

- 决策：`docs/adr/`（0001/0002/0003）
- 报告：`docs/文档解析平台化-里程碑报告/`（本文件 + M0 + M1 + M2）
- 契约代码：`knowledge_mining/mining/contracts/{parse_ir,storage}/`、`contracts/{file_management,state_machines,parser_adapter}.py`
- 实现代码：`knowledge_mining/mining/{infra/object_store,file_management,frozen_input,file_migration,parse_adapters,shadow_parse}/`
- DDL：`databases/asset_core/schemas/008_object_storage_foundation{,_postgresql}.sql`、`009_shadow_parse_runs{,_postgresql}.sql`
- 配置：`main_control_service/config/system/storage.yaml`
- 测试：`knowledge_mining/tests/{contracts,infra,file_management,frozen_input,file_migration,parse_adapters,shadow_parse}/`
