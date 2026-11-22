<template>
  <div class="mcp-view">
    <div class="mcp-view__head">
      <p class="mcp-view__desc">
        {{ t('mcp.view.descEntry') }}
        {{ t('mcp.view.descIsolation') }}
      </p>
      <el-tooltip v-if="atLimit" :content="t('mcp.tip.atLimit')" placement="top">
        <span class="mcp-view__add-wrap">
          <el-button type="primary" disabled>{{ t('mcp.action.createKey') }}</el-button>
        </span>
      </el-tooltip>
      <el-button v-else type="primary" @click="openCreate">{{ t('mcp.action.createKey') }}</el-button>
    </div>

    <!-- 加载失败：显式错误态 -->
    <el-alert v-if="loadFailed" type="error" :closable="false" show-icon style="margin-bottom: 14px">
      <template #title>{{ t('mcp.view.loadFailedTitle') }}</template>
      <el-button size="small" @click="reload">{{ t('mcp.action.retryLoad') }}</el-button>
    </el-alert>

    <!-- 空态引导 -->
    <section v-if="!loading && !loadFailed && !keys.length" class="mcp-view__card mcp-view__card--hero">
      <el-empty :image-size="80" :description="t('mcp.view.emptyTitle')">
        <p class="mcp-view__empty-tip">{{ t('mcp.view.emptyTipDomain') }}<br>{{ t('mcp.view.emptyTipStart') }}</p>
        <el-button type="primary" :disabled="atLimit" @click="openCreate">{{ t('mcp.action.createKey') }}</el-button>
      </el-empty>
    </section>

    <!-- 钥匙列表 -->
    <el-table v-else v-loading="loading" :data="keys" class="mcp-view__table">
      <el-table-column :label="t('common.name')" min-width="140">
        <template #default="{ row }">
          <span class="mcp-view__name" :class="{ 'is-revoked': row.status === 'revoked' }">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('mcp.field.domain')" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="row.domain_bound ? 'info' : 'danger'" effect="plain">{{ row.domain }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('mcp.field.secret')" min-width="150">
        <template #default="{ row }">
          <code class="mcp-view__prefix">{{ row.key_prefix }}…</code>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.status')" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.status === 'revoked'" size="small" type="info">{{ t('mcp.status.revoked') }}</el-tag>
          <el-tooltip
            v-else-if="!row.domain_bound"
            :content="t('mcp.tip.domainUnbound')"
            placement="top"
          >
            <el-tag size="small" type="danger">{{ t('mcp.status.domainUnbound') }}</el-tag>
          </el-tooltip>
          <el-tag v-else size="small" type="success">{{ t('mcp.status.normal') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('mcp.field.lastUsed')" width="150">
        <template #default="{ row }">{{ fmtTime(row.last_used_at) || t('mcp.field.never') }}</template>
      </el-table-column>
      <el-table-column :label="t('mcp.field.createdAt')" width="150">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column :label="t('common.actions')" width="270" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="viewRecords(row)">{{ t('mcp.action.callRecords') }}</el-button>
          <template v-if="row.status === 'active'">
            <el-button link type="primary" size="small" @click="openConfig(row)">{{ t('mcp.action.configure') }}</el-button>
            <el-button link type="warning" size="small" @click="quickRotate(row)">{{ t('mcp.action.rotate') }}</el-button>
            <el-button link type="danger" size="small" @click="quickRevoke(row)">{{ t('mcp.action.revoke') }}</el-button>
          </template>
          <el-button v-else link type="danger" size="small" @click="deleteRevoked(row)">
            {{ t('mcp.action.deleteRecord') }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建钥匙 dialog -->
    <el-dialog v-model="createVisible" :title="t('mcp.dialog.createTitle')" width="440px">
      <el-form label-width="80px" @submit.prevent>
        <el-form-item :label="t('common.name')" required>
          <el-input v-model="createForm.name" maxlength="64" show-word-limit :placeholder="t('mcp.form.namePlaceholder')" data-test="mcp-key-name" />
        </el-form-item>
        <el-form-item :label="t('mcp.field.knowledgeDomain')" required>
          <el-select v-model="createForm.domain" :placeholder="t('mcp.form.domainPlaceholder')" style="width: 100%" data-test="mcp-key-domain">
            <el-option v-for="d in domainStore.enabledDomains" :key="d.domain_id" :value="d.domain_id" :label="d.domain_id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="creating" :disabled="!createForm.name.trim() || !createForm.domain" @click="submitCreate">{{ t('common.create') }}</el-button>
      </template>
    </el-dialog>

    <!-- 明文一次性展示 dialog（创建成功） -->
    <el-dialog v-model="freshVisible" :title="t('mcp.dialog.freshTitle')" width="640px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" style="margin-bottom: 12px">
        <template #title>{{ t('mcp.dialog.freshAlert') }}</template>
      </el-alert>
      <div class="mcp-view__fresh-row">
        <code class="mcp-view__fresh-key">{{ freshCreated?.key }}</code>
        <el-button size="small" type="primary" plain @click="copy(freshCreated?.key ?? '')">{{ t('mcp.action.copy') }}</el-button>
      </div>
      <div class="mcp-view__code-wrap">
        <button class="mcp-view__copy" :title="t('mcp.action.copy')" @click="copy(freshConfigJson)">{{ t('mcp.action.copy') }}</button>
        <pre class="mcp-view__code">{{ freshConfigJson }}</pre>
      </div>
      <p class="mcp-view__note">{{ t('mcp.view.freshNote', { endpoint: mcpEndpoint }) }}</p>
      <template #footer>
        <el-button type="primary" @click="freshVisible = false">{{ t('mcp.action.savedSecret') }}</el-button>
      </template>
    </el-dialog>

    <!-- 每把钥匙配置 drawer。destroy-on-close：关闭即卸载面板——轮换后的
         一次性明文随面板销毁，重开不再出现；会话内的列表刷新/保存不抹掉它。 -->
    <el-drawer v-model="configVisible" size="620px" destroy-on-close :title="configKey ? t('mcp.view.configTitleNamed', { name: configKey.name }) : t('mcp.view.configTitle')">
      <McpKeyConfigPanel
        v-if="configKey"
        :key-item="configKey"
        :domain-kbs="configDomainKbs"
        :domain-artifacts="configDomainArtifacts"
        @updated="onPanelUpdated"
        @rotated="reload"
        @revoked="onPanelRevoked"
      />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { useArtifactsApi } from '@/api/artifacts'
import { managementErrorMessage as apiErrorDetail } from '@/utils/managementError'
import { useDomainStore } from '@/stores/domain'
import { useAuthStore } from '@/stores/auth'
import type { McpKeyItem } from '@/types/kb'
import McpKeyConfigPanel from '@/components/mcp/McpKeyConfigPanel.vue'
import { t } from '@/locales/t'

/** 与后端上限同步改：mining/kb/services/mcp_key_service.py 的
 *  MAX_KEYS_PER_USER（前端仅提前禁用按钮，最终防线在后端 409）。 */
const MAX_KEYS = 10

const kbApi = useKbApi()
const artifactsApi = useArtifactsApi()
const domainStore = useDomainStore()
const auth = useAuthStore()
const router = useRouter()

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
/** 钥匙域内当前用户可见、已发布的制品（配置抽屉候选）。 */
const configDomainArtifacts = ref<
  { artifact_id: string; title: string; status: string; visibility?: string }[]
>([])

const activeCount = computed(() => keys.value.filter(k => k.status === 'active').length)
const atLimit = computed(() => activeCount.value >= MAX_KEYS)
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

function viewRecords(row: McpKeyItem) {
  if (row.domain_bound && row.domain !== domainStore.currentDomain) {
    domainStore.switchDomain(row.domain)
  }
  void router.push({ name: 'retrieval-records', query: { mcpKeyId: row.id } })
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

/** 建钥错误的定制文案：403 域未绑定的 detail 是 {code, message} 结构，其余走通用提取。
 *  409 分支的 includes('上限') 比对的是服务端返回的文案——属匹配数据，不接 t()
 *  （译成英文会在 EN 态失配，而中文断言照绿）。 */
async function createErrorText(e: unknown): Promise<string> {
  const resp = (e as { response?: { status?: number; data?: { detail?: { code?: string; message?: string } } } })
    ?.response
  const detail = resp?.data?.detail
  if (resp?.status === 403 && detail?.code === 'domain_not_bound') {
    return detail.message ?? t('mcp.error.domainNotBound')
  }
  if (resp?.status === 409) {
    const msg = await apiErrorDetail(e)
    return msg.includes('上限') ? t('mcp.error.limitReached') : msg
  }
  return apiErrorDetail(e)
}

async function openConfig(row: McpKeyItem) {
  configKey.value = row
  configDomainKbs.value = []
  configDomainArtifacts.value = []
  configVisible.value = true
  try {
    // 钥匙域即数据源——不 watch 页面当前域切换
    const [kbs, domainArtifacts] = await Promise.all([
      kbApi.listKbs(row.domain),
      artifactsApi.list({ domain_id: row.domain }),
    ])
    // 响应竞态防护：等待期间用户可能已切到另一把钥匙——落地前比对当前钥匙
    // id，不是发起时那把则丢弃，避免旧域库清单覆盖新面板（所见非所得）
    if (configKey.value?.id !== row.id) return
    configDomainKbs.value = kbs.map(k => ({ id: k.id, name: k.name, document_count: k.document_count }))
    // 候选=本域我能看到且已发布的制品；草稿和已停用制品不能开放给 Agent。
    configDomainArtifacts.value = domainArtifacts
      .filter(a => a.domain_id === row.domain && a.status === 'published')
      .map(a => ({
        artifact_id: a.artifact_id, title: a.title,
        status: a.status, visibility: a.visibility,
      }))
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
      t('mcp.confirm.rotateMessage', { name: row.name }),
      t('mcp.dialog.rotateTitle'), { type: 'warning', confirmButtonText: t('mcp.action.rotate'), cancelButtonText: t('common.cancel') },
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
      t('mcp.confirm.revokeMessage', { name: row.name }),
      t('mcp.dialog.revokeTitle'), { type: 'warning', confirmButtonText: t('mcp.action.revoke'), cancelButtonText: t('common.cancel') },
    )
  } catch { return }
  try {
    await kbApi.revokeMcpKey(row.id)
    ElMessage.success(t('mcp.msg.keyRevoked'))
    await reload()
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

async function deleteRevoked(row: McpKeyItem) {
  try {
    await ElMessageBox.confirm(
      t('mcp.confirm.deleteMessage', { name: row.name }),
      t('mcp.dialog.deleteTitle'),
      { type: 'warning', confirmButtonText: t('mcp.action.deleteRecord'), cancelButtonText: t('common.cancel') },
    )
  } catch { return }
  try {
    await kbApi.deleteMcpKey(row.id)
    await reload()
    ElMessage.success(t('mcp.msg.keyRecordDeleted'))
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(t('mcp.msg.copied'))
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
    if (ok) ElMessage.success(t('mcp.msg.copied'))
    else ElMessage.warning(t('mcp.msg.copyFailed'))
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

defineExpose({ keys, deleteRevoked, reload })
</script>

<style scoped>
.mcp-view {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 4px;
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
