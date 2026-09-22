<template>
  <div class="mcp-view">
    <div class="mcp-view__head">
      <p class="mcp-view__desc">
        你的 Agent（dify / 扣子 / Claude 等）连平台知识库的入口。每个 Agent 一把钥匙、一把钥匙一个知识域：
        各钥匙的工具、开放库、提示词互不影响。
      </p>
      <el-tooltip v-if="atLimit" content="已达上限" placement="top">
        <span class="mcp-view__add-wrap">
          <el-button type="primary" disabled>新建钥匙</el-button>
        </span>
      </el-tooltip>
      <el-button v-else type="primary" @click="openCreate">新建钥匙</el-button>
    </div>

    <!-- 加载失败：显式错误态 -->
    <el-alert v-if="loadFailed" type="error" :closable="false" show-icon style="margin-bottom: 14px">
      <template #title>钥匙列表加载失败。你的现役钥匙不受影响，Agent 连接不会中断。</template>
      <el-button size="small" @click="reload">重试加载</el-button>
    </el-alert>

    <!-- 空态引导 -->
    <section v-if="!loading && !loadFailed && !keys.length" class="mcp-view__card mcp-view__card--hero">
      <el-empty :image-size="80" description="还没有 MCP 钥匙">
        <p class="mcp-view__empty-tip">每个 Agent 一把钥匙，一把钥匙一个知识域。<br>为你的第一个 Agent 创建一把钥匙开始接入。</p>
        <el-button type="primary" :disabled="atLimit" @click="openCreate">新建钥匙</el-button>
      </el-empty>
    </section>

    <!-- 钥匙列表 -->
    <el-table v-else v-loading="loading" :data="keys" class="mcp-view__table">
      <el-table-column label="名称" min-width="140">
        <template #default="{ row }">
          <span class="mcp-view__name" :class="{ 'is-revoked': row.status === 'revoked' }">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column label="域" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.domain_bound ? 'info' : 'danger'" effect="plain">{{ row.domain }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="密钥" min-width="150">
        <template #default="{ row }">
          <code class="mcp-view__prefix">{{ row.key_prefix }}…</code>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.status === 'revoked'" size="small" type="info">已吊销</el-tag>
          <el-tooltip
            v-else-if="!row.domain_bound"
            content="该钥匙绑定的知识域已被解绑，请联系管理员重新分配域后重建钥匙"
            placement="top"
          >
            <el-tag size="small" type="danger">域已解绑</el-tag>
          </el-tooltip>
          <el-tag v-else size="small" type="success">正常</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="最近使用" width="150">
        <template #default="{ row }">{{ fmtTime(row.last_used_at) || '从未' }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column v-if="hasActiveKeys" label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'active'">
            <el-button link type="primary" size="small" @click="openConfig(row)">配置</el-button>
            <el-button link type="warning" size="small" @click="quickRotate(row)">轮换</el-button>
            <el-button link type="danger" size="small" @click="quickRevoke(row)">吊销</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建钥匙 dialog -->
    <el-dialog v-model="createVisible" title="新建 MCP 钥匙" width="440px">
      <el-form label-width="80px" @submit.prevent>
        <el-form-item label="名称" required>
          <el-input v-model="createForm.name" maxlength="64" show-word-limit placeholder="例如：客服机器人" data-test="mcp-key-name" />
        </el-form-item>
        <el-form-item label="知识域" required>
          <el-select v-model="createForm.domain" placeholder="选择该钥匙服务的知识域" style="width: 100%" data-test="mcp-key-domain">
            <el-option v-for="d in domainStore.enabledDomains" :key="d.domain_id" :value="d.domain_id" :label="d.domain_id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" :disabled="!createForm.name.trim() || !createForm.domain" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 明文一次性展示 dialog（创建成功） -->
    <el-dialog v-model="freshVisible" title="钥匙已创建" width="640px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" style="margin-bottom: 12px">
        <template #title>密钥明文仅此一次显示，关闭后无法再查看。请立即复制并填入你的 Agent 配置。</template>
      </el-alert>
      <div class="mcp-view__fresh-row">
        <code class="mcp-view__fresh-key">{{ freshCreated?.key }}</code>
        <el-button size="small" type="primary" plain @click="copy(freshCreated?.key ?? '')">复制</el-button>
      </div>
      <div class="mcp-view__code-wrap">
        <button class="mcp-view__copy" title="复制" @click="copy(freshConfigJson)">复制</button>
        <pre class="mcp-view__code">{{ freshConfigJson }}</pre>
      </div>
      <p class="mcp-view__note">连接地址由当前部署地址推导（{{ mcpEndpoint }}）；JSON 已预填本次明文。</p>
      <template #footer>
        <el-button type="primary" @click="freshVisible = false">我已保存好密钥</el-button>
      </template>
    </el-dialog>

    <!-- 每把钥匙配置 drawer -->
    <el-drawer v-model="configVisible" size="620px" :title="configKey ? `钥匙配置 · ${configKey.name}` : '钥匙配置'">
      <McpKeyConfigPanel
        v-if="configKey"
        :key-item="configKey"
        :domain-kbs="configDomainKbs"
        @updated="onPanelUpdated"
        @rotated="reload"
        @revoked="onPanelRevoked"
      />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import { useAuthStore } from '@/stores/auth'
import type { McpKeyItem } from '@/types/kb'
import McpKeyConfigPanel from '@/components/mcp/McpKeyConfigPanel.vue'

/** 与后端上限同步改：mining/kb/services/mcp_key_service.py 的
 *  MAX_KEYS_PER_USER（前端仅提前禁用按钮，最终防线在后端 409）。 */
const MAX_KEYS = 10

const kbApi = useKbApi()
const domainStore = useDomainStore()
const auth = useAuthStore()

const keys = ref<McpKeyItem[]>([])
const loading = ref(false)
const loadFailed = ref(false)

const createVisible = ref(false)
const creating = ref(false)
const createForm = ref({ name: '', domain: '' })

/** 创建响应的明文——仅创建后短暂持有，用于一次性展示弹窗。 */
const freshCreated = ref<{ name: string; domain: string; key: string } | null>(null)
const freshVisible = ref(false)

const configVisible = ref(false)
const configKey = ref<McpKeyItem | null>(null)
const configDomainKbs = ref<{ id: string; name: string; document_count: number }[]>([])

const activeCount = computed(() => keys.value.filter(k => k.status === 'active').length)
const atLimit = computed(() => activeCount.value >= MAX_KEYS)
const hasActiveKeys = computed(() => activeCount.value > 0)
const mcpEndpoint = computed(() => `${window.location.hostname}:9000/mcp`)

const freshConfigJson = computed(() => {
  const key = freshCreated.value?.key
  if (!key) return ''
  const url = `http://${mcpEndpoint.value}`
  return JSON.stringify({
    mcpServers: {
      knowledge: {
        type: 'http',
        url,
        headers: { Authorization: `Bearer ${key}` },
      },
    },
  }, null, 2)
})

async function reload() {
  loading.value = true
  loadFailed.value = false
  try {
    const r = await kbApi.listMcpKeys()
    keys.value = r.keys
    // 抽屉开着时同步面板的钥匙行（轮换/保存后后端状态已变）
    if (configKey.value) {
      configKey.value = keys.value.find(k => k.id === configKey.value?.id) ?? configKey.value
    }
  } catch (e) {
    loadFailed.value = true
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.value = { name: '', domain: domainStore.currentDomain || '' }
  createVisible.value = true
}

async function submitCreate() {
  creating.value = true
  try {
    const r = await kbApi.createMcpKey({
      name: createForm.value.name.trim(),
      domain: createForm.value.domain,
    })
    createVisible.value = false
    freshCreated.value = r
    freshVisible.value = true
    await reload()
  } catch (e) {
    ElMessage.error(await createErrorText(e))
  } finally {
    creating.value = false
  }
}

/** 建钥错误的定制文案：403 域未绑定的 detail 是 {code, message} 结构，其余走通用提取。 */
async function createErrorText(e: unknown): Promise<string> {
  const resp = (e as { response?: { status?: number; data?: { detail?: { code?: string; message?: string } } } })
    ?.response
  const detail = resp?.data?.detail
  if (resp?.status === 403 && detail?.code === 'domain_not_bound') {
    return detail.message ?? '你未绑定该知识域，无法创建钥匙'
  }
  if (resp?.status === 409) {
    const msg = await apiErrorDetail(e)
    return msg.includes('上限') ? '已达 10 把上限，请先吊销不需要的钥匙' : msg
  }
  return apiErrorDetail(e)
}

async function openConfig(row: McpKeyItem) {
  configKey.value = row
  configDomainKbs.value = []
  configVisible.value = true
  try {
    // 钥匙域即数据源——不 watch 页面当前域切换
    const kbs = await kbApi.listKbs(row.domain)
    // 响应竞态防护：等待期间用户可能已切到另一把钥匙——落地前比对当前钥匙
    // id，不是发起时那把则丢弃，避免旧域库清单覆盖新面板（所见非所得）
    if (configKey.value?.id !== row.id) return
    configDomainKbs.value = kbs.map(k => ({ id: k.id, name: k.name, document_count: k.document_count }))
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

function onPanelUpdated(item: McpKeyItem) {
  const idx = keys.value.findIndex(k => k.id === item.id)
  if (idx >= 0) keys.value[idx] = item
  configKey.value = item
}

async function onPanelRevoked(keyId: string) {
  await reload()
  configVisible.value = false
  void keyId
}

/** 列表快捷轮换：不开抽屉，直接确认框 + 明文弹窗。 */
async function quickRotate(row: McpKeyItem) {
  try {
    await ElMessageBox.confirm(
      `轮换后「${row.name}」的旧密钥立即失效，正在使用它的 Agent 会断连。确定继续？`,
      '轮换密钥', { type: 'warning', confirmButtonText: '轮换', cancelButtonText: '取消' },
    )
  } catch { return }
  try {
    const r = await kbApi.rotateMcpKey(row.id)
    freshCreated.value = { name: row.name, domain: row.domain, key: r.key }
    freshVisible.value = true
    await reload()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

/** 列表快捷吊销。 */
async function quickRevoke(row: McpKeyItem) {
  try {
    await ElMessageBox.confirm(
      `吊销后「${row.name}」立即失效且不可恢复，正在使用它的 Agent 会断连。确定吊销？`,
      '吊销钥匙', { type: 'warning', confirmButtonText: '吊销', cancelButtonText: '取消' },
    )
  } catch { return }
  try {
    await kbApi.revokeMcpKey(row.id)
    ElMessage.success('钥匙已吊销')
    await reload()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    // http://公网IP 等非安全上下文没有 navigator.clipboard，走 execCommand 兜底
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    if (ok) ElMessage.success('已复制')
    else ElMessage.warning('复制失败，请手动选择复制')
  }
}

function fmtTime(v?: string | null): string {
  if (!v) return ''
  return v.replace('T', ' ').slice(0, 19)
}

onMounted(() => {
  if (auth.user?.username) void domainStore.fetchDomains(auth.user.username)
  reload()
})
</script>

<style scoped>
.mcp-view {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 4px;
  max-width: 1060px;
}

.mcp-view__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.mcp-view__desc { margin: 4px 0 0; font-size: 13px; color: var(--kb-text-secondary); line-height: 1.6; }
.mcp-view__add-wrap { display: inline-block; margin-left: 12px; }
.mcp-view__empty-tip { margin: 0 0 12px; font-size: 13px; color: var(--kb-text-secondary); line-height: 1.8; }

.mcp-view__card {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
  padding: 18px 22px;
}
.mcp-view__card--hero { border-color: var(--kb-accent, #3b82f6); }

.mcp-view__table { width: 100%; }
.mcp-view__name { font-weight: 600; }
.mcp-view__name.is-revoked { color: var(--kb-text-tertiary); text-decoration: line-through; }
.mcp-view__prefix { font-family: var(--el-font-family-mono, monospace); font-size: 12.5px; }

.mcp-view__fresh-row { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.mcp-view__fresh-key {
  flex: 1;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--el-fill-color);
  font-family: var(--el-font-family-mono, monospace);
  font-size: 13px;
  word-break: break-all;
}

.mcp-view__code-wrap { position: relative; }
.mcp-view__code {
  margin: 0;
  padding: 14px 16px;
  border-radius: 8px;
  background: var(--el-fill-color-darker, #1e1e1e);
  color: var(--el-color-success-light-3, #67c23a);
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12.5px;
  line-height: 1.7;
  overflow-x: auto;
}
.mcp-view__copy {
  position: absolute;
  top: 8px;
  right: 8px;
  padding: 4px 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  font-size: 12px;
  cursor: pointer;
}
.mcp-view__copy:hover { color: var(--kb-accent, #3b82f6); border-color: var(--kb-accent, #3b82f6); }
.mcp-view__note { margin: 8px 0 0; font-size: 12px; color: var(--kb-text-tertiary); line-height: 1.6; }
</style>
