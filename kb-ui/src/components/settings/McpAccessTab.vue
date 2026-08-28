<template>
  <div class="mcp-access">
    <!-- 密钥 -->
    <section class="mcp-access__section">
      <header class="mcp-access__section-head">
        <div>
          <h3 class="mcp-access__title">MCP 接入密钥</h3>
          <p class="mcp-access__desc">
            你的 Agent（dify / 扣子 / 自研应用）连 MCP 时用的身份凭证。
            把它填进 Agent 的 MCP 连接配置（Authorization: Bearer &lt;密钥&gt;），
            Agent 就以你的身份检索——你能看哪些库，它就能搜哪些库。
          </p>
        </div>
        <el-button type="primary" plain :loading="rotating" @click="rotate">
          {{ status?.configured ? '重新生成（旧钥立即失效）' : '生成密钥' }}
        </el-button>
      </header>

      <el-descriptions v-if="status?.configured" :column="2" border size="small" class="mcp-access__meta">
        <el-descriptions-item label="密钥标识">{{ status.key_prefix }}…</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="status.status === 'active' ? 'success' : 'info'" size="small">
            {{ status.status === 'active' ? '生效中' : status.status }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="生成时间">{{ fmtTime(status.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="最近使用">{{ fmtTime(status.last_used_at) || '从未使用' }}</el-descriptions-item>
      </el-descriptions>
      <el-empty
        v-else
        description="还没有密钥。生成后把它配到你的 Agent 里。"
        :image-size="60"
      />

      <!-- 明文只展示一次 -->
      <el-alert v-if="freshKey" type="success" :closable="false" class="mcp-access__fresh">
        <template #title>
          新密钥（仅此一次可见，请立即复制保存）：
          <code class="mcp-access__key">{{ freshKey }}</code>
          <el-button size="small" text type="primary" @click="copyKey">复制</el-button>
        </template>
      </el-alert>
    </section>

    <!-- 开放库 -->
    <section class="mcp-access__section">
      <header class="mcp-access__section-head">
        <div>
          <h3 class="mcp-access__title">开放的知识库</h3>
          <p class="mcp-access__desc">
            你的 Agent 只能检索这里勾选的库。只开放一个库时，Agent 搜索连库名都不用传；
            开放多个库时，Agent 可指定库名缩小范围。权限被收走后勾选自动失效。
          </p>
        </div>
        <el-button :loading="savingKbs" :disabled="!dirty" @click="saveOpenKbs">保存勾选</el-button>
      </header>

      <el-checkbox-group v-model="selected" class="mcp-access__kbs">
        <el-checkbox v-for="kb in myKbs" :key="kb.id" :value="kb.id" :label="kb.id">
          {{ kb.name }}
          <span class="mcp-access__kb-meta">{{ kb.document_count }} 文档</span>
        </el-checkbox>
      </el-checkbox-group>
      <el-empty v-if="!myKbs.length" description="当前域没有可见的知识库" :image-size="60" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import type { McpAccessStatus } from '@/types/kb'

const kbApi = useKbApi()
const domainStore = useDomainStore()

const status = ref<McpAccessStatus | null>(null)
const freshKey = ref('')
const rotating = ref(false)
const savingKbs = ref(false)
const myKbs = ref<{ id: string; name: string; document_count: number }[]>([])
const selected = ref<string[]>([])

const dirty = computed(() => {
  const current = new Set(status.value?.open_kb_ids ?? [])
  const next = new Set(selected.value)
  if (current.size !== next.size) return true
  for (const id of current) if (!next.has(id)) return true
  return false
})

async function reload() {
  if (!domainStore.currentDomain) return
  try {
    const [access, kbs] = await Promise.all([
      kbApi.getMcpAccess(domainStore.currentDomain),
      kbApi.listKbs(domainStore.currentDomain),
    ])
    status.value = access
    myKbs.value = kbs.map((k) => ({ id: k.id, name: k.name, document_count: k.document_count }))
    selected.value = [...access.open_kb_ids]
    freshKey.value = ''
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}

async function rotate() {
  rotating.value = true
  try {
    const r = await kbApi.rotateMcpKey()
    freshKey.value = r.key
    ElMessage.success('密钥已生成；旧密钥（如有）立即失效')
    await reload()
    // reload 会清 freshKey——重新赋值
    freshKey.value = r.key
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    rotating.value = false
  }
}

async function saveOpenKbs() {
  savingKbs.value = true
  try {
    const r = await kbApi.putMcpOpenKbs(selected.value)
    status.value = { ...(status.value ?? { configured: true, open_kb_ids: [] }), open_kb_ids: r.open_kb_ids }
    ElMessage.success('开放库已更新')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    savingKbs.value = false
  }
}

async function copyKey() {
  try {
    await navigator.clipboard.writeText(freshKey.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择复制')
  }
}

function fmtTime(v?: string | null): string {
  if (!v) return ''
  return v.replace('T', ' ').slice(0, 19)
}

onMounted(reload)
watch(() => domainStore.currentDomain, reload)
</script>

<style scoped>
.mcp-access {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 4px 0;
}

.mcp-access__section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mcp-access__section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.mcp-access__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.mcp-access__desc {
  margin: 4px 0 0;
  max-width: 560px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--kb-text-secondary);
}

.mcp-access__key {
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--el-fill-color);
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  word-break: break-all;
}

.mcp-access__kbs {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.mcp-access__kb-meta {
  margin-left: 6px;
  font-size: 12px;
  color: var(--kb-text-tertiary);
}
</style>
