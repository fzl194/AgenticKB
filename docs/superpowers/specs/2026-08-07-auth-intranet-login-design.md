# 鉴权模型重构 —— 工号登录 + SSO 口子 + KB 权限清理 — 设计规格

- **日期**:2026-08-07
- **分支**:`feat/user-permissions`(在已有 Phase 2 基础上重构)
- **状态**:设计中 → 待用户复核 → 实现
- **前置**:`docs/superpowers/specs/2026-08-06-user-permissions-design.md`(Phase 2 基础)

---

## 1. 目标

把现有"用户名+密码"单一登录改成**两类用户两套登录**,并清理 KB 权限:

1. **普通用户(工号)**:输工号 →(未来跳内网 SSO,现先留口子)→ 在系统库即放行,**无密码**。
2. **管理员**:用户名+密码(沿用现有)。
3. **KB 权限**:砍掉 private/shared 区分,只留"公开"开关;admin 默认通所有 KB。
4. **管理员联系方式可配置**(web 配置),登录失败提示用。

## 2. 决策(已与用户确认)

| 决策点 | 结论 |
|---|---|
| 登录界面 | **单表单智能判断**:输用户名 → 后端 `identify` 返回模式 → admin 弹密码框 / 工号直接进 |
| 白名单 | **复用 kb_users 表**:admin 预建 member 行(无密码)= 白名单 |
| 工号账号来源 | **admin 预建**(不自动建) |
| KB 权限 | **砍 private/shared**(只留 public 开关)+ **admin 全通所有 KB** |
| 管理员联系方式 | ui.yaml `site.admin_contact` 可配,LoginView 失败提示用 |
| SSO | 本期不实现,留两处明确口子(后端函数 + 前端函数) |

## 3. 登录流程(两步,密码框按需出现)

```
① 用户输用户名 → POST /api/v1/auth/identify {username}
     ↓ 返回 {mode, display_name?}
  ┌─ mode='password'(admin 账号,有 password_hash)
  │     → 前端显示密码框 → 输完
  │       POST /api/v1/auth/login {username, password} → 验密 → JWT → 进系统
  │
  ├─ mode='member'(工号账号,在库且无 password_hash)
  │     → 【现在】前端直接 POST /api/v1/auth/login {username} → JWT → 进系统
  │     → 【未来】前端跳内网 SSO → 回跳 /auth/callback → 校验 → JWT
  │
  └─ mode='not_found'(不在库)
        → 提示「用户未在系统，联系管理员：{admin_contact}」
```

### 后端 verify 新逻辑(mining `/api/kb/auth/verify`,内部端点)

```python
async def verify_credentials(username, password):
    user = get_user_by_username(username)
    if user is None or user.status == 'disabled':
        return None
    if user.password_hash:                       # admin(或有密码账号)
        if not verify_password(password, user.password_hash):
            return None
    else:                                         # 工号 member(无密码)
        if not await verify_intranet_auth(username):   # ← SSO 口子
            return None
    return user
```

### 新增 identify 端点(mining `/api/kb/auth/identify`,内部)

```python
async def identify(username):
    user = get_user_by_username(username)
    if user is None or user.status == 'disabled':
        return {"mode": "not_found"}
    if user.password_hash:
        return {"mode": "password"}
    return {"mode": "member", "display_name": user.display_name}
```

main_control 暴露:`POST /api/v1/auth/identify`(透传 mining,带 X-Internal-Auth)、`POST /api/v1/auth/login`(支持 password 可空)。

## 4. SSO 口子(两处,未来只换这两处)

**后端(mining)** —— 独立函数,member 分支只调它:
```python
async def verify_intranet_auth(username: str) -> bool:
    """【SSO 口子】当前内网鉴权未接入 → 恒 True(白名单 = kb_users 表有此行即信任)。
    未来:把这里换成「跳内网 SSO → 回跳带 ticket → 校验 ticket」。
    `/api/kb/auth/verify` 的 member 分支只调本函数,换 SSO 不动调用方。"""
    return True
```

**前端(LoginView)** —— member 识别后处理:
```ts
function onMemberIdentified(username: string) {
  // 【SSO 口子】现在直接登录;未来换成 window.location = intranetSSOUrl(username)
  return loginApi.login({ username })
}
```

> 未来接真 SSO:后端 `verify_intranet_auth` 改校验 ticket(加 `/api/v1/auth/sso-callback` 收回跳);前端 `onMemberIdentified` 改跳转。**JWT/KB 权限/路由零改。**

## 5. 账号管理(= 白名单):用户管理 Tab 改造

- admin 建账号两种:
  - **member(工号)**:用户名(工号)+ 显示名,**无密码** → 加白名单。
  - **admin**:用户名 + 密码(必填,≥8)。
- `UserService.create_user`:member 允许无密码;admin 必须有密码。
- **工号用户永远 member**:无密码 → 不可能升 admin(升 admin 必须同时设密码,在"改角色"时强制:site_role→admin 时 password 必填)。
- `update_user`:把某用户 site_role 改成 admin 时,要求该用户已有密码,否则报错"请先为该用户设置密码"。

## 6. KB 权限清理(砍区分 + admin 全通)

- **visibility**:UI 砍成「公开」开关(开=`public`,关=`private`)。DB 列保留(`shared` 不再产出)。既有 KB:public→开,private/shared→关(语义改,无数据迁移)。
- `is_visible` / `can_write` 加短路:**site admin 直接 True**。
  - 实现:`kb_service` 在 `_assert_read`/`_assert_write` 前,若 `actor.site_role == 'admin'` 直接放行。需把 site_role 传进 service(actor 当前只传 id,改成传 user dict 或加 actor_site_role 参数)。

## 7. 管理员联系方式(web 配置)

- `main_control_service/config/system/ui.yaml` 的 `site:` 块加:
  ```yaml
  site:
    title: ...
    ...
    admin_contact: "张三 / 工号 12345"   # 新增
  ```
- 前端 brand store 暴露 `adminContact`(读 `site.admin_contact`)。
- LoginView `not_found` 提示:`用户未在系统，联系管理员：${brand.adminContact}`(空则只提示前半句)。
- 编辑入口:「品牌外观」Tab 加「管理员联系方式」输入框(复用 site 配置表单 + ui.yaml 解析/序列化)。

## 8. 不变的

- 两层模型(site_role ↔ KB 角色)、JWT、X-Internal-Auth 网关链路、路由守卫、刷新不登出(刚修好的)。
- member 仍只看【概览/知识库/检索测试】,admin 全开。
- 内网 SSO 本期不实现。

## 9. 文件改动清单

**后端 mining**
- 改 `kb/services/user_service.py`:`verify_credentials` 新逻辑 + `identify` + `create_user`(member 无密码)+ `update_user`(升 admin 要求密码)+ `verify_intranet_auth` 口子。
- 改 `kb/routes/auth.py`:加 `/api/kb/auth/identify`;verify 改用新 verify_credentials。
- 改 `kb/services/kb_service.py`:`_assert_read/_assert_write` 加 admin 短路(传 site_role)。
- 改 `kb/routes/kbs.py` 等:把 actor user dict(含 site_role)传进 service。

**后端 main_control**
- 改 `main.py`:加 `POST /api/v1/auth/identify`;`login` 支持 password 可空。
- 改 `config/system/ui.yaml`:`site.admin_contact` 默认值。

**前端 kb-ui**
- 改 `api/auth.ts`:加 `identify(username)`;`login` password 改可选。
- 改 `views/LoginView.vue`:两步表单(用户名 → 按模式显密码框/直接进)+ not_found 提示 admin_contact + `onMemberIdentified` SSO 口子。
- 改 `stores/brand.ts`:加 `adminContact`。
- 改 `components/settings/BrandAppearanceTab.vue`:加「管理员联系方式」字段。
- 改 `utils/brandYaml.ts`:normalize/build 支持 `admin_contact`。
- KB 创建/设置 UI:`visibility` 三选 →「公开」开关(`KbListView`/`KbSettingsPanel`)。

**测试**
- mining:`test_user_service`(verify 新分支、create member 无密码、升 admin 要求密码、identify)、`test_auth_routes`(identify 端点、login member 无密码)。
- main_control:`test_auth_flow`(identify、login member 无密码)。
- kb-ui:LoginView 两步、brand store adminContact、KB 公开开关。

## 10. 非目标

- 真 SSO/OIDC 接入(只留口子)。
- 域级角色。
- viewer 第三档。
- 工号自动建号(admin 预建)。
