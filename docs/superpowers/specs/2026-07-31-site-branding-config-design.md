# 站点品牌可配置（网站名字 + 网站图标）— 设计规格

> 日期：2026-07-31 · 分支：`feat/site-branding` · 状态：已与用户确认

## 目标

让「网站名字」和「网站图标」可在部署期由配置决定，无需改代码、无需重建前端镜像即可改名/换标。

## 范围（已确认）

- **品牌粒度**：全局 / 部署级一份配置（**不**按 domain 各自一套）。domain 维持原样（`cloud_core_network / generic / civil_engineering / odn`，不改）。
- **覆盖位置**（当前全部硬编码，本次全部改为读配置）：
  - 浏览器标签 `<title>`（`index.html`，默认 `CoreMasterKB`）
  - favicon（`index.html` `<link rel="icon">`，默认 `/favicon.svg`）
  - 侧边栏 logo 主名 `CoreMaster`、副标题 `Knowledge Base`、字母标记 `KB`（`Sidebar.vue`）
  - 页头兜底名 `CoreMasterKB`（`Header.vue`，路由名未命中时）
- **配置入口**：系统设置新增「品牌外观」可视化 Tab（表单 + 图标选择 + 实时预览），底层读写 `ui.yaml`。

## 非目标

- per-domain 品牌、白标多租户。
- 域（domain）列表/语义的任何改动。
- 登录页/邮件模板等其它品牌触点（当前产品无）。

## 配置模型 — `main_control_service/config/system/ui.yaml`

新增 `site:` 块，默认值 = 当前硬编码值（首装零感知）：

```yaml
site:
  title: CoreMasterKB      # <title> + 页头兜底名
  name: CoreMaster         # 侧边栏 logo 主名
  badge: Knowledge Base    # 侧边栏 logo 副标题
  logo_text: KB            # 无图标时渐变方框里的字母
  icon: ""                 # data URI 或 http(s) URL；空 → 回落 /favicon.svg + logo_text
```

`icon` 单字符串，前端解析：空→默认；`data:`→直用；`http`→直用。

## 后端 — 零代码改动

通用 system config CRUD 已就绪：
- `GET /api/v1/system/ui` → JSON（brand store 启动拉取）
- `GET/PUT /api/v1/system/ui/raw` → YAML 文本（品牌 Tab 读/写）

`main_control` 每次 `GET` 重读文件，改后即生效、重启不丢（config 目录 volume 挂载）。

## 前端架构

1. **`stores/brand.ts`**（Pinia）：`title/name/badge/logoText/icon/loaded`；`fetchBrand()` 拉 `GET /api/v1/system/ui` 解析 `site`（缺字段用默认）；`applyBrand()` 设 `document.title` 并切换 `<link rel=icon>` 的 `href`；`resolveIcon()` 纯函数（空→`/favicon.svg`，`data:`/`http`→原值）。失败兜底默认，不阻塞挂载。
2. **`main.ts`**：`app.use(pinia)` 后、`mount()` 前 `await brand.fetchBrand(); brand.applyBrand()`，确保首屏前 title/favicon 就位（无 CoreMasterKB 闪烁）。
3. **`api/controlPlane.ts`**：新增 `getSystemConfig(name)`（JSON 变体，现有只有 raw）。
4. **`Sidebar.vue`**：logo mark → `brand.icon` 则 `<img>` 否则渐变块 `{{ brand.logoText }}`；name/badge → `{{ brand.name/badge }}`。
5. **`Header.vue`**：兜底 `'CoreMasterKB'` → `brand.title`。
6. **`index.html`**：保留现 `<title>` 与 `<link icon>` 作静态默认（预水合），brand store 立即覆盖。
7. **`components/settings/BrandAppearanceTab.vue`**（新）+ 接入 `SettingsView.vue` 新 Tab：
   - 表单：title / name / badge / logo_text。
   - 图标：文件选择（读文件→base64 data URI→预览）或 URL 输入；实时预览（favicon 框 + mini sidebar logo）。
   - 保存：`getSystemConfigRaw('ui')` → `js-yaml` load → 仅替换 `site` 块 → dump → `updateSystemConfigRaw('ui', text)` → 重新 `fetchBrand()+applyBrand()`（即时生效，不必整页刷新）。
   - 新增依赖 `js-yaml` + `@types/js-yaml`（前端目前无 YAML 解析库；现有 `@codemirror/lang-yaml` 仅编辑器高亮）。

## 错误处理 / 边界

- 启动时 `main_control` 不可达 → 用默认品牌、照常挂载。
- `site:` 块缺失 → 全字段默认。
- `icon` 空 → `/favicon.svg` + `logo_text` 渐变块。
- 保存后 `applyBrand()` 重新生效；data URI 不走外部缓存，无需 cache-bust。
- 提交前 `git restore kb-ui/components.d.ts` 清 IDE 噪声（见开发流程文档）。

## 测试

- **Python**：`main_control` 侧断言 `GET /api/v1/system/ui` 返回 `site` 块（用现有 `YamlConfigService` + FastAPI 路由的轻量测试；无需 PG）。
- **前端 Vitest**：
  - `resolveIcon` 纯函数：空/`data:`/`http`/其它。
  - brand store：`fetchBrand` 解析 + 缺字段默认 + 失败兜底；`applyBrand` 正确设 `document.title` 与 favicon link。
  - `BrandAppearanceTab`：渲染字段、保存调用 `updateSystemConfigRaw('ui', ...)` 且 YAML 仅替换 site 块、保留其余键。
- **`npm run build`**（vue-tsc）必须全绿。

## 部署注意（将补入 docs/开发与发布流程.md）

- 这是**前端代码上线**，需重建 kb-ui 镜像（`npm run build`）让「读配置」的代码生效。
- 此后改名/换标在「品牌外观」Tab **运行时可配**，无需重建（值在 volume 挂载的 `ui.yaml`）。
- `main_control`（Python）改 `ui.yaml` 重启即生效。
