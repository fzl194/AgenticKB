import { createRouter, createWebHistory } from 'vue-router'
import { useDomainStore } from '@/stores/domain'

const router = createRouter({
  history: createWebHistory(),
  routes: [
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
          path: 'search',
          name: 'search',
          component: () => import('@/views/SearchView.vue'),
        },
        {
          path: 'knowledge',
          name: 'knowledge',
          component: () => import('@/views/knowledge/DocumentsView.vue'),
        },
        {
          path: 'knowledge/:docId',
          name: 'knowledge-detail',
          component: () => import('@/views/knowledge/DocumentDetailView.vue'),
          props: true,
        },
        {
          path: 'graph',
          name: 'graph',
          component: () => import('@/views/knowledge/GraphView.vue'),
        },
        {
          path: 'entities',
          name: 'entities',
          component: () => import('@/views/knowledge/EntityGraphView.vue'),
        },
        {
          path: 'ontology',
          name: 'ontology',
          component: () => import('@/views/knowledge/OntologyView.vue'),
        },
        {
          path: 'ontology/graph',
          name: 'ontology-graph',
          component: () => import('@/views/knowledge/OntologyGraphView.vue'),
        },
        {
          // 本体确认评审：挖掘流程的临时子页面（仿实体确认的 mentions/review）。
          // 刻意不放在 /ontology/ 下，避免左侧"本体版本"导航被点亮、误以为离开了挖掘流程。
          path: 'candidates/review',
          name: 'ontology-review',
          component: () => import('@/views/knowledge/OntologyReviewView.vue'),
        },
        {
          path: 'mentions/review',
          name: 'mentions-review',
          component: () => import('@/views/knowledge/MentionReviewView.vue'),
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
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/SettingsView.vue'),
        },
      ],
    },
  ],
})

// Load domain list from main_control_service before first navigation
let domainsInitialized = false
router.beforeEach(async () => {
  if (!domainsInitialized) {
    domainsInitialized = true
    const domainStore = useDomainStore()
    await domainStore.fetchDomains()
  }
})

export default router
