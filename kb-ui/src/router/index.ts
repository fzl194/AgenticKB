import { createRouter, createWebHistory, type RouteLocationNormalized } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useDomainStore } from '@/stores/domain'
import { canManageDomainUsers } from '@/utils/domainPermissions'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/components/layout/AppLayout.vue'),
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
        },
        {
          path: 'kb',
          name: 'kb',
          component: () => import('@/views/kb/KbListView.vue'),
        },
        {
          path: 'mcp',
          name: 'mcp-access',
          component: () => import('@/views/McpAccessView.vue'),
        },
        {
          path: 'onenet',
          name: 'onenet-admin',
          component: () => import('@/views/onenet/OnenetAdminView.vue'),
        },
        {
          path: 'kb/:kbId',
          name: 'kb-detail',
          component: () => import('@/views/kb/KbDetailView.vue'),
          props: true,
        },
        {
          path: 'kb/:kbId/doc/:docId',
          name: 'kb-doc-preview',
          component: () => import('@/views/kb/KbDocPreviewView.vue'),
          props: true,
        },
        {
          path: 'kb/:kbId/run/:runId',
          name: 'kb-run-detail',
          component: () => import('@/views/kb/KbRunDetailView.vue'),
          props: true,
        },
        {
          path: 'kb/:kbId/run/:runId/doc/:docId',
          name: 'kb-run-doc-detail',
          component: () => import('@/views/kb/KbRunDocDetailView.vue'),
          props: true,
        },
        {
          path: 'mining/workflows',
          name: 'mining-workflows',
          component: () => import('@/views/mining/WorkflowListView.vue'),
        },
        {
          path: 'mining/workflows/:id',
          name: 'mining-workflow-editor',
          component: () => import('@/views/mining/WorkflowEditorView.vue'),
          props: true,
        },
        {
          path: 'llm',
          name: 'llm',
          component: () => import('@/views/LlmView.vue'),
        },
        {
          path: 'llm/:taskId',
          name: 'llm-task-detail',
          component: () => import('@/views/llm/LlmTaskDetailView.vue'),
          props: true,
        },
        {
          path: 'paradigm',
          name: 'paradigm',
          component: () => import('@/views/paradigm/ParadigmListView.vue'),
        },
        {
          path: 'paradigm/:id',
          name: 'paradigm-edit',
          component: () => import('@/views/paradigm/ParadigmEditorView.vue'),
          props: true,
        },
        {
          path: 'users',
          name: 'users',
          component: () => import('@/views/UserManagementView.vue'),
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/SettingsView.vue'),
        },
      ],
    },
  ],
})

// admin-only 路由名（member 命中 → 挡回 /）。KB 详情类（kb-*）对 member 开放——那是
// 普通用户的主战场；知识资产/图谱/范式/本体/LLM/设置 属管理类。
const ADMIN_ROUTES = new Set([
  'mining-workflows', 'mining-workflow-editor',
  'paradigm', 'paradigm-edit',
  'llm', 'llm-task-detail', 'settings',
  'onenet-admin',
])

export async function authAndDomainGuard(to: RouteLocationNormalized) {
  const auth = useAuthStore()
  // 等 auth 启动完成（restore + fetchMe）。初始导航在 app.use(router) 时触发，早于 fetchMe，
  // 不等的话 user 还没拿到 → isAuthenticated 假 → 误判未登录跳 /login（刷新即登出的根因）。
  await auth.ready
  // 未登录且非 public → 登录页（带 redirect 回来）
  if (!to.meta.public && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  // 已登录又访问 /login → 回首页
  if (to.meta.public && auth.isAuthenticated) {
    return { name: 'dashboard' }
  }
  // 域缓存必须属于当前登录用户。store 会对同一用户复用成功结果；换用户或上次失败
  // 都会重新请求，避免把旧账号的 currentDomain/raw id 带进新会话。
  let domainStore: ReturnType<typeof useDomainStore> | undefined
  if (auth.isAuthenticated && auth.user?.username) {
    domainStore = useDomainStore()
    await domainStore.fetchDomains(auth.user.username)
  }
  // 域级权限依赖刚加载的 domain_role/capabilities，必须在域加载完成后判断。
  if (to.name === 'users' && !canManageDomainUsers(auth.siteRole, domainStore?.currentDomainInfo)) {
    return { name: 'dashboard' }
  }
  // 全局管理面仍只允许系统管理员。
  if (ADMIN_ROUTES.has(to.name as string) && auth.siteRole !== 'admin') {
    return { name: 'dashboard' }
  }
}

router.beforeEach(authAndDomainGuard)

export default router
