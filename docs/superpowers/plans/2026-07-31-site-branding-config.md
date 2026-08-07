# 站点品牌可配置 — 实现计划

> 规格：`docs/superpowers/specs/2026-07-31-site-branding-config-design.md`
> 分支：`feat/site-branding`（已从 master `305401a` 切出）

## 提交批次（按工作面）

1. `feat(ui): site 品牌配置块 + main_control 返回测试` — ui.yaml + python 测试
2. `feat(kb-ui): brand store 与启动期品牌注入` — brand store + controlPlane.getSystemConfig + main.ts + 单测
3. `feat(kb-ui): 侧边栏/页头读取品牌` — Sidebar.vue + Header.vue
4. `feat(kb-ui): 品牌外观配置 Tab` — BrandAppearanceTab + SettingsView + js-yaml 依赖 + 单测
5. `docs: 品牌可配补入开发流程` — docs/开发与发布流程.md

每批：写测试→跑测试见红→实现→跑测试转绿→`npm run build`/pytest 验证→commit。
提交前 `git restore kb-ui/components.d.ts`（IDE 自动生成噪声）。

---

## 批次 1：ui.yaml site 块 + 后端测试

- [ ] **改** `main_control_service/config/system/ui.yaml`：在现有 api-base 键之上加 `site:` 块（5 字段，默认值见 spec）。
- [ ] **写测试** `main_control_service/tests/test_system_config.py`（若无 tests 目录则新建，参考既有 FastAPI app 测试风格）：
  - 用 `create_app(config_dir=tmp)` 构造临时 config 目录，写一份 `system/ui.yaml`，`GET /api/v1/system/ui` 返回 dict 含 `site.title` 等。
  - `PUT /api/v1/system/ui/raw` 写回后 `GET` 反映新值。
- [ ] **跑**：`python -m pytest main_control_service/tests/test_system_config.py -q`（用 anaconda python；无 PG 依赖）。
- [ ] commit。

> 预期：零后端代码改动，路由已存在；测试只验文件读写通路。

## 批次 2：brand store + bootstrap

- [ ] **加** `kb-ui/src/api/controlPlane.ts` → `getSystemConfig(name): Promise<Record<string, unknown>>`（`GET /api/v1/system/${name}`）。
- [ ] **写测试** `kb-ui/src/stores/__tests__/brand.spec.ts`：
  - `resolveIcon('')` === `/favicon.svg`；`resolveIcon('data:...')` 原样；`resolveIcon('https://...')` 原样。
  - `fetchBrand`：mock `getSystemConfig('ui')` 返回 `{site:{title,...}}` → state 正确；缺 site → 默认；抛错 → 不抛、保持默认、`loaded=true`。
  - `applyBrand`：设 `document.title`；增/改 `<link rel=icon>` 的 href（jsdom）。
- [ ] **实现** `kb-ui/src/stores/brand.ts`：默认常量 + `resolveIcon` + store（fetchBrand/applyBrand）+ `BrandConfig` 类型（放 `types/brand.ts`）。
- [ ] **改** `kb-ui/src/main.ts`：`const pinia = createPinia(); app.use(pinia); const brand = useBrandStore(pinia); await brand.fetchBrand(); brand.applyBrand(); app.mount('#app')`，try/catch 兜底。
- [ ] **跑**：`cd kb-ui && npx vitest run src/stores/__tests__/brand.spec.ts`；`npm run build`。
- [ ] commit。

## 批次 3：Sidebar / Header 接线

- [ ] **改** `kb-ui/src/components/layout/Sidebar.vue`：
  - logo mark：`<img v-if="brand.icon" :src="resolveIcon(brand.icon)">`（限定尺寸/圆角），否则保留渐变块 `{{ brand.logoText }}`。
  - name → `{{ brand.name }}`；badge → `{{ brand.badge }}`。
  - 引入 `useBrandStore` + `resolveIcon`。
- [ ] **改** `kb-ui/src/components/layout/Header.vue`：`pageTitle` 兜底 `'CoreMasterKB'` → `brand.title`（引入 store）。
- [ ] **改/扩** `Sidebar.spec.ts`：断言默认品牌渲染；注入 store 提供自定义 brand 后渲染对应 name/logoText。
- [ ] **跑**：`npx vitest run src/components/layout/__tests__/Sidebar.spec.ts`；`npm run build`。
- [ ] commit。

## 批次 4：品牌外观 Tab

- [ ] **加依赖**：`kb-ui` 装 `js-yaml` + `@types/js-yaml`（dev）。
- [ ] **写测试** `kb-ui/src/components/settings/__tests__/BrandAppearanceTab.spec.ts`：
  - mount 后字段回显当前 ui.yaml 的 site 值；
  - 改字段 + 点保存 → 调用 `updateSystemConfigRaw('ui', text)`；断言写入的 YAML 仅 site 块变化、其余 api-base 键保留。
  - 文件选择：mock `FileReader` 生成 data URI，预览 `<img>` 出现。
- [ ] **实现** `BrandAppearanceTab.vue`：load raw → `yaml.load` → 表单回显 → 保存时 `yaml.dump({...rest, site: {...}})` → PUT → 成功后 `brand.fetchBrand()+applyBrand()`。
- [ ] **接入** `SettingsView.vue`：`<el-tab-pane label="品牌外观" name="brand"><BrandAppearanceTab/></el-tab-pane>`。
- [ ] **跑**：`npx vitest run src/components/settings/__tests__/BrandAppearanceTab.spec.ts`；`npm run build`。
- [ ] commit。

## 批次 5：文档

- [ ] **改** `docs/开发与发布流程.md` §部署/§挖掘运维附近：补一条「品牌（站名/图标）在 系统设置→品牌外观 配置；改后即时生效；前端代码变更才需重建镜像」。
- [ ] commit。

---

## 全量验证（收尾）

- [ ] `cd kb-ui && npm run build`（vue-tsc + vite 全绿）。
- [ ] `cd kb-ui && npx vitest run --reporter=dot`（全量前端单测，确保无回归）。
- [ ] python：`python -m pytest main_control_service/tests/ -q`。
- [ ] `git status` 确认无 `components.d.ts` 等噪声。

## 推送与 PR（见开发流程文档 §3-4）

- [ ] `git push -u origin feat/site-branding`
- [ ] GitHub PR `base: master ← feat/site-branding`，「Create a merge commit」（不 squash）。
- [ ] 合并后：`checkout master + pull + 删分支`。
- [ ] 部署：重建 kb-ui 镜像；main_control 改 ui.yaml 重启即生效。
