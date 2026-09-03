<template>
  <!-- 仅 site-admin 可见：重启会短暂中断全部后台服务，是危险操作。 -->
  <div v-if="isAdmin" class="svc-restart" data-testid="service-restart">
    <div class="svc-restart__main">
      <div class="svc-restart__title">重启后台服务</div>
      <p class="svc-restart__desc">
        修改宿主机配置 / Python 代码 / serving jar 后点这里生效：按依赖顺序重启
        control → llm → mining → serving → mcp，nginx 不动，本页面不掉线。
        进行中的挖掘会被中断，重启后由队列自动续挖。
        docker-compose / .env（端口、密钥）变更不适用——仍需服务器执行
        bash deploy-server.sh --apply-config。
      </p>
    </div>

    <div class="svc-restart__action">
      <template v-if="phase === 'idle'">
        <el-button type="danger" plain data-testid="restart-btn" @click="confirmRestart">
          重启后台服务
        </el-button>
        <span v-if="lastLine" class="svc-restart__last">{{ lastLine }}</span>
      </template>

      <div v-else-if="phase === 'running'" class="svc-restart__running" data-testid="restart-running">
        <span class="svc-restart__spin" aria-hidden="true">⟳</span>
        <span>
          正在重启 {{ currentLabel }}（{{ completedCount }}/{{ planCount }}）
          <span class="svc-restart__muted">服务短暂不可用属正常，预计 30–60 秒</span>
        </span>
      </div>

      <template v-else-if="phase === 'failed'">
        <el-alert type="error" :closable="false" show-icon data-testid="restart-failed">
          重启失败：{{ status?.error ?? (timedOut ? '等待超时' : '未知原因') }}。
          到「服务日志」查看对应服务的日志，修复后可重新点击重启。
        </el-alert>
        <el-button type="danger" plain data-testid="restart-btn" @click="confirmRestart">
          重试重启
        </el-button>
      </template>

      <template v-else>
        <div class="svc-restart__done" data-testid="restart-done">
          <span class="svc-restart__ok">✓ 重启完成</span>
          <span
            v-for="svc in status?.services ?? []"
            :key="svc.name"
            class="svc-restart__chip"
            :class="{ 'svc-restart__chip--bad': svc.status !== 'RUNNING' }"
          >
            {{ serviceLabel(svc.name) }} {{ svc.status }}
          </span>
        </div>
        <el-button type="danger" plain data-testid="restart-btn" @click="confirmRestart">
          再次重启
        </el-button>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useControlPlaneApi } from '@/api/controlPlane'
import type { RestartStatus } from '@/api/controlPlane'

const POLL_INTERVAL_MS = 2000
/** 与后端 STALE_AFTER_SECONDS 对齐：超过即放弃观测，提示去服务器看编排日志。 */
const POLL_DEADLINE_MS = 10 * 60 * 1000

const SERVICE_LABELS: Record<string, string> = {
  control: '主控服务',
  llm_service: 'LLM服务',
  mining: '挖掘服务',
  serving: '检索服务',
  mcp: 'MCP服务',
  nginx: '前端网关',
}

const emit = defineEmits<{ restarted: [] }>()

const authStore = useAuthStore()
const api = useControlPlaneApi()

const status = ref<RestartStatus | null>(null)
const polling = ref(false)
const timedOut = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null
let pollStartedAt = 0
let disposed = false

const isAdmin = computed(() => authStore.siteRole === 'admin')
const planCount = computed(() => status.value?.plan?.length ?? 5)
const completedCount = computed(() => status.value?.completed?.length ?? 0)
const currentLabel = computed(
  () => serviceLabel(status.value?.current ?? '') || '后台服务',
)

/**
 * running 的两个来源：本页发起的轮询进行中（polling），或管理员在重启中途
 * 打开页面（文件里 state=running 且未超时）。后者同样接管轮询（onMounted）。
 */
const phase = computed<'idle' | 'running' | 'failed' | 'done'>(() => {
  if (polling.value) return 'running'
  const st = status.value?.state
  if (st === 'running') return 'running'
  if (st === 'failed' || timedOut.value) return 'failed'
  if (st === 'done') return 'done'
  return 'idle'
})

const lastLine = computed(() => {
  const st = status.value
  if (!st || st.state === 'idle' || st.state === 'running') return ''
  const t = st.finished_at ? new Date(st.finished_at).toLocaleString('zh-CN') : ''
  const who = st.triggered_by ? `（${st.triggered_by} 触发）` : ''
  return `上次重启：${t} ${st.state === 'done' ? '成功' : '失败'}${who}`
})

function serviceLabel(name: string): string {
  return SERVICE_LABELS[name] ?? name
}

async function confirmRestart(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '将按依赖顺序重启全部后台服务（约 30–60 秒，期间检索/挖掘短暂不可用；'
        + '进行中的挖掘会在重启后自动续挖）。确定继续？',
      '重启后台服务',
      { type: 'warning', confirmButtonText: '重启', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  try {
    const res = await api.triggerRestart()
    status.value = {
      ...(status.value ?? {}),
      state: 'running',
      triggered_by: res.triggered_by,
      completed: [],
    }
    startPolling()
  } catch (e) {
    if ((e as { response?: { status?: number } })?.response?.status === 409) {
      startPolling() // 已在进行中（比如另一个窗口触发的），直接进入观测
    } else {
      ElMessage.error('触发重启失败，请稍后重试')
    }
  }
}

function startPolling(): void {
  polling.value = true
  timedOut.value = false
  pollStartedAt = Date.now()
  schedulePoll(0)
}

function schedulePoll(delay: number): void {
  clearTimer()
  timer = setTimeout(pollOnce, delay)
}

async function pollOnce(): Promise<void> {
  if (disposed) return
  try {
    const st = await api.getRestartStatus()
    status.value = st
    if (!st.active && st.state !== 'running') {
      finishPolling(st.state === 'done')
      return
    }
  } catch {
    // control 正在重启：502 / 超时都是"仍在重启"的正常表现，继续轮询。
  }
  if (Date.now() - pollStartedAt > POLL_DEADLINE_MS) {
    polling.value = false
    timedOut.value = true
    ElMessage.error('等待重启超时：请到服务器查看 ./logs/restart-orchestrator.log')
    return
  }
  schedulePoll(POLL_INTERVAL_MS)
}

function finishPolling(ok: boolean): void {
  polling.value = false
  if (ok) {
    ElMessage.success('后台服务重启完成')
    emit('restarted')
  }
}

function clearTimer(): void {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

onMounted(async () => {
  if (!isAdmin.value) return
  try {
    const st = await api.getRestartStatus()
    status.value = st
    if (st.active) startPolling() // 重启中途打开页面：接管观测
  } catch {
    // control 暂不可达时保持 idle 态，不影响页面其它内容
  }
})

onUnmounted(() => {
  disposed = true
  clearTimer()
})
</script>

<style scoped>
.svc-restart {
  margin-top: 14px;
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 12px 14px;
  border: 1px dashed rgba(239, 68, 68, 0.35);
  border-radius: var(--kb-radius-sm);
  background: rgba(239, 68, 68, 0.03);
}

.svc-restart__main {
  min-width: 0;
}

.svc-restart__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-primary);
}

.svc-restart__desc {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--kb-text-tertiary);
  max-width: 640px;
}

.svc-restart__action {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.svc-restart__running {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--kb-text-secondary);
}

.svc-restart__spin {
  display: inline-block;
  animation: svc-restart-rotate 1s linear infinite;
  color: var(--kb-danger, #ef4444);
  font-size: 16px;
}

@keyframes svc-restart-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.svc-restart__muted {
  display: block;
  font-size: 11px;
  color: var(--kb-text-tertiary);
}

.svc-restart__last {
  font-size: 11px;
  color: var(--kb-text-tertiary);
}

.svc-restart__done {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.svc-restart__ok {
  color: var(--kb-success, #16a34a);
  font-weight: 600;
  font-size: 13px;
}

.svc-restart__chip {
  font-size: 11px;
  padding: 1px 8px;
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
  color: var(--kb-text-tertiary);
  font-variant-numeric: tabular-nums;
}

.svc-restart__chip--bad {
  border-color: rgba(239, 68, 68, 0.4);
  color: var(--kb-danger, #ef4444);
}
</style>
