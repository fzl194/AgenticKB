<template>
  <div class="mcp-view">
    <div class="mcp-view__head">
      <div>
        <h2 class="mcp-view__title">MCP 接入</h2>
        <p class="mcp-view__desc">
          你的 Agent（dify / 扣子 / Claude 等）连平台知识库的唯一入口。一个服务、一把钥匙：
          下方所有配置只影响你自己的 Agent，其他人互不可见。
        </p>
      </div>
    </div>

    <!-- 加载失败：显式错误态（禁止伪装"未配置密钥"诱导误轮换） -->
    <el-alert v-if="loadFailed" type="error" :closable="false" show-icon style="margin-bottom: 14px">
      <template #title>配置加载失败——密钥轮换与保存已暂时禁用，不会影响你现役的接入密钥。</template>
      <el-button size="small" @click="reload">重试加载</el-button>
    </el-alert>

    <!-- ① Agent 接入配置（无密钥时引导先生成） -->
    <section class="mcp-view__card mcp-view__card--hero">
      <div class="mcp-view__card-head">
        <div>
          <h3 class="mcp-view__card-title">Agent 接入配置</h3>
          <p class="mcp-view__card-desc">选一种格式，点击复制后粘贴进你的 Agent / MCP 客户端配置。</p>
        </div>
        <el-radio-group v-model="configFormat" size="small">
          <el-radio-button value="generic">通用</el-radio-button>
          <el-radio-button value="dify">dify</el-radio-button>
        </el-radio-group>
      </div>
      <template v-if="hasKey">
        <div class="mcp-view__code-wrap">
          <button class="mcp-view__copy" title="复制" @click="copy(configJson)">复制</button>
          <pre class="mcp-view__code">{{ configJson }}</pre>
        </div>
        <p class="mcp-view__note">
          连接地址由当前部署地址推导（{{ mcpEndpoint }}）；密钥就是下面你的接入密钥——
          轮换密钥后记得同步更新 Agent 配置。
        </p>
      </template>
      <el-empty v-else description="先生成接入密钥，配置会自动拼好" :image-size="60" />
    </section>

    <!-- ② 密钥 -->
    <section class="mcp-view__card">
      <div class="mcp-view__card-head">
        <div>
          <h3 class="mcp-view__card-title">接入密钥</h3>
          <p class="mcp-view__card-desc">
            Agent 用它证明"我是你"。重新生成后旧密钥立即失效（防泄漏）；明文只在生成时显示一次。
          </p>
        </div>
        <el-button type="primary" plain :loading="rotating" :disabled="loadFailed" @click="rotate">
          {{ hasKey ? '重新生成（旧钥立即失效）' : '生成密钥' }}
        </el-button>
      </div>
      <el-descriptions v-if="status?.configured" :column="3" border size="small">
        <el-descriptions-item label="密钥标识">{{ status.key_prefix }}…</el-descriptions-item>
        <el-descriptions-item label="最近使用">{{ fmtTime(status.last_used_at) || '从未' }}</el-descriptions-item>
        <el-descriptions-item label="生成时间">{{ fmtTime(status.created_at) }}</el-descriptions-item>
      </el-descriptions>
      <el-alert v-if="freshKey" type="success" :closable="false" class="mcp-view__fresh">
        <template #title>
          新密钥（仅此一次可见）：<code class="mcp-view__key">{{ freshKey }}</code>
          <el-button size="small" text type="primary" @click="copy(freshKey)">复制</el-button>
        </template>
      </el-alert>
    </section>

    <!-- ③ 工具开关 -->
    <section class="mcp-view__card">
      <div class="mcp-view__card-head">
        <div>
          <h3 class="mcp-view__card-title">开放的工具</h3>
          <p class="mcp-view__card-desc">关掉的工具对你的 Agent 完全不可见（清单里都不出现）。至少保留一个。</p>
        </div>
        <el-button :loading="savingTools" :disabled="!toolsDirty" @click="saveTools">保存</el-button>
      </div>
      <div class="mcp-view__tools">
        <div v-for="t in ALL_TOOLS" :key="t.name" class="mcp-view__tool">
          <el-switch v-model="toolOn[t.name]" :disabled="savingTools" />
          <div class="mcp-view__tool-text">
            <span class="mcp-view__tool-name">{{ t.name }}</span>
            <span class="mcp-view__tool-desc">{{ t.label }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- ④ 开放库 -->
    <section class="mcp-view__card">
      <div class="mcp-view__card-head">
        <div>
          <h3 class="mcp-view__card-title">开放的知识库</h3>
          <p class="mcp-view__card-desc">
            你的 Agent 只能访问这里勾选的库（且仅限你本人有权限的）。只开放一个库时，检索连库名都不用传。
          </p>
        </div>
        <el-button :loading="savingKbs" :disabled="!kbsDirty" @click="saveOpenKbs">保存</el-button>
      </div>
      <el-checkbox-group v-model="selectedKbs" class="mcp-view__kbs">
        <el-checkbox v-for="kb in myKbs" :key="kb.id" :value="kb.id" :label="kb.id">
          {{ kb.name }}<span class="mcp-view__kb-meta">{{ kb.document_count }} 文档</span>
        </el-checkbox>
      </el-checkbox-group>
      <el-empty v-if="!myKbs.length" description="当前域没有可见的知识库" :image-size="60" />
    </section>

    <!-- ⑤ 提示词 -->
    <section class="mcp-view__card">
      <div class="mcp-view__card-head">
        <div>
          <h3 class="mcp-view__card-title">提示词与工具说明</h3>
          <p class="mcp-view__card-desc">
            提示词是 MCP 对你 Agent 的"系统级自我介绍"；工具说明可逐个改写成适合你业务的说法。
            留空 = 使用默认文案。
          </p>
        </div>
        <el-button :loading="savingPrompt" :disabled="!promptDirty" @click="savePrompt">保存</el-button>
      </div>
      <el-input
        v-model="instructions"
        type="textarea"
        :rows="6"
        maxlength="4000"
        show-word-limit
        placeholder="留空使用默认提示词。写清楚你的 Agent 该怎么用这套知识库——例如业务口径、常用问法、注意事项。"
        :title="'已知限制：当前 MCP 服务端版本的 initialize 响应不注入自定义提示词（fastmcp 上游限制，批次9 升级收口）；本配置保存后将在接线生效时启用。'"
      />
      <div class="mcp-view__descs">
        <div v-for="t in ALL_TOOLS" :key="t.name" class="mcp-view__desc-row">
          <span class="mcp-view__desc-name">{{ t.name }}</span>
          <el-input
            v-model="toolDescs[t.name]"
            type="textarea"
            :rows="2"
            maxlength="2000"
            :placeholder="`默认：${t.label}`"
          />
        </div>
      </div>
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

/** 三件套（与后端 MCP_TOOL_NAMES 一致）；label 为默认文案摘要。
 * 2026-08-31 工具族两轮收敛：get_knowledge = get_content + browse_knowledge +
 * inspect + navigate + query_structured（一切读取行为，ref/库分流，默认能力报告）。 */
const ALL_TOOLS = [
  { name: 'search_knowledge', label: '检索知识证据（domain 单域免传）' },
  { name: 'get_knowledge', label: '深入读取：浏览层级 / 取原文 / 看能力 / 导航 / 查表格' },
  { name: 'upload_document', label: '上传文件入库（不自动挖掘）' },
] as const

const status = ref<McpAccessStatus | null>(null)
const freshKey = ref('')
const rotating = ref(false)
const configFormat = ref<'generic' | 'dify'>('generic')

const myKbs = ref<{ id: string; name: string; document_count: number }[]>([])
const selectedKbs = ref<string[]>([])
const savingKbs = ref(false)

const toolOn = ref<Record<string, boolean>>({})
const savingTools = ref(false)

const instructions = ref('')
const toolDescs = ref<Record<string, string>>({})
const savingPrompt = ref(false)

const hasKey = computed(() => !!status.value?.configured)
const loadFailed = ref(false)
const mcpEndpoint = computed(() => `${window.location.hostname}:9000/mcp`)

const configJson = computed(() => {
  if (!hasKey.value) return ''
  const url = `http://${mcpEndpoint.value}`
  const key = '__KEY__'
  const generic = {
    mcpServers: {
      knowledge: {
        // 显式声明传输类型：Claude 等客户端要求远程服务带 type，
        // 缺省时部分客户端按 stdio 解析而报错
        type: 'http',
        url,
        headers: { Authorization: `Bearer ${key}` },
      },
    },
  }
  const dify = {
    server_url: url,
    authorization: `Bearer ${key}`,
  }
  const raw = JSON.stringify(configFormat.value === 'dify' ? dify : generic, null, 2)
  // freshKey 只在轮换后短暂持有；平时展示占位符，提示用户密钥位置
  return freshKey.value ? raw.replace(key, freshKey.value) : raw
})

const toolsDirty = computed(() => {
  const on = ALL_TOOLS.filter(t => toolOn.value[t.name]).map(t => t.name)
  const saved = status.value?.open_tools
  if (saved == null) return on.length !== ALL_TOOLS.length
  return JSON.stringify(on) !== JSON.stringify(saved)
})

const kbsDirty = computed(() => {
  const a = [...selectedKbs.value].sort()
  const b = [...(status.value?.open_kb_ids ?? [])].sort()
  return JSON.stringify(a) !== JSON.stringify(b)
})

const promptDirty = computed(() => {
  const savedInstr = status.value?.instructions ?? ''
  const savedDescs = status.value?.tool_descriptions ?? {}
  const nowDescs: Record<string, string> = {}
  for (const t of ALL_TOOLS) {
    const v = (toolDescs.value[t.name] || '').trim()
    if (v) nowDescs[t.name] = v
  }
  return instructions.value.trim() !== savedInstr.trim()
    || JSON.stringify(nowDescs) !== JSON.stringify(savedDescs)
})

async function reload() {
  if (!domainStore.currentDomain) return
  loadFailed.value = false
  try {
    const [access, kbs] = await Promise.all([
      kbApi.getMcpAccess(domainStore.currentDomain),
      kbApi.listKbs(domainStore.currentDomain),
    ])
    status.value = access
    myKbs.value = kbs.map(k => ({ id: k.id, name: k.name, document_count: k.document_count }))
    // 只保留当前仍可见的库——软删/权限收走的库自动从勾选中消失，
    // 不让幽灵 id 混进下一次保存请求（后端也会剔除，这里保证界面所见即所得）
    const visibleIds = new Set(myKbs.value.map(k => k.id))
    selectedKbs.value = access.open_kb_ids.filter(id => visibleIds.has(id))
    const savedOn = access.open_tools
    for (const t of ALL_TOOLS) {
      toolOn.value[t.name] = savedOn == null ? true : savedOn.includes(t.name)
      toolDescs.value[t.name] = access.tool_descriptions?.[t.name] ?? ''
    }
    instructions.value = access.instructions ?? ''
    freshKey.value = ''
  } catch (e) {
    // 加载失败不能伪装成"未配置密钥"：那会诱导用户点"生成密钥"把现役
    // 密钥轮换掉，正在运行的 Agent 全部断连（2026-08-31 前端审查 M8）。
    loadFailed.value = true
    ElMessage.error(await apiErrorDetail(e))
  }
}

async function rotate() {
  if (loadFailed.value || status.value === null) return
  rotating.value = true
  try {
    const r = await kbApi.rotateMcpKey()
    freshKey.value = r.key
    ElMessage.success('密钥已生成；旧密钥（如有）立即失效')
    const key = r.key
    await reload()
    freshKey.value = key
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    rotating.value = false
  }
}

async function saveTools() {
  const on = ALL_TOOLS.filter(t => toolOn.value[t.name]).map(t => t.name)
  if (!on.length) { ElMessage.warning('至少保留一个工具'); return }
  savingTools.value = true
  try {
    status.value = await kbApi.putMcpConfig({ open_tools: on })
    ElMessage.success('工具开关已保存')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    savingTools.value = false
  }
}

async function saveOpenKbs() {
  savingKbs.value = true
  try {
    const r = await kbApi.putMcpOpenKbs(selectedKbs.value)
    status.value = { ...(status.value ?? { configured: true, open_kb_ids: [] }), open_kb_ids: r.open_kb_ids }
    selectedKbs.value = [...r.open_kb_ids]  // 以响应为准（后端可能剔除了失效勾选）
    ElMessage.success('开放库已更新')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    savingKbs.value = false
  }
}

async function savePrompt() {
  savingPrompt.value = true
  try {
    const descs: Record<string, string> = {}
    for (const t of ALL_TOOLS) {
      const v = (toolDescs.value[t.name] || '').trim()
      if (v) descs[t.name] = v
    }
    status.value = await kbApi.putMcpConfig({
      instructions: instructions.value,
      tool_descriptions: descs,
    })
    ElMessage.success('提示词已保存')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    savingPrompt.value = false
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

onMounted(reload)
watch(() => domainStore.currentDomain, reload)
</script>

<style scoped>
.mcp-view {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 4px;
  max-width: 960px;
}

.mcp-view__head { margin-bottom: 2px; }
.mcp-view__title { margin: 0; font-size: 20px; font-weight: 700; }
.mcp-view__desc { margin: 4px 0 0; font-size: 13px; color: var(--kb-text-secondary); line-height: 1.6; }

.mcp-view__card {
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
  padding: 18px 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.mcp-view__card--hero { border-color: var(--kb-accent, #3b82f6); }

.mcp-view__card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.mcp-view__card-title { margin: 0; font-size: 14.5px; font-weight: 650; }
.mcp-view__card-desc { margin: 4px 0 0; font-size: 12.5px; color: var(--kb-text-secondary); line-height: 1.6; max-width: 620px; }

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

.mcp-view__note { margin: 0; font-size: 12px; color: var(--kb-text-tertiary); line-height: 1.6; }

.mcp-view__fresh :deep(.el-alert__title) { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mcp-view__key {
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--el-fill-color);
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  word-break: break-all;
}

.mcp-view__tools { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
.mcp-view__tool { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid var(--kb-border-light); border-radius: 8px; }
.mcp-view__tool-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.mcp-view__tool-name { font-size: 13px; font-weight: 600; font-family: var(--el-font-family-mono, monospace); }
.mcp-view__tool-desc { font-size: 12px; color: var(--kb-text-tertiary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.mcp-view__kbs { display: flex; flex-direction: column; gap: 4px; }
.mcp-view__kb-meta { margin-left: 6px; font-size: 12px; color: var(--kb-text-tertiary); }

.mcp-view__descs { display: flex; flex-direction: column; gap: 10px; }
.mcp-view__desc-row { display: grid; grid-template-columns: 180px 1fr; gap: 10px; align-items: start; }
.mcp-view__desc-name { padding-top: 6px; font-size: 12.5px; font-weight: 600; font-family: var(--el-font-family-mono, monospace); color: var(--kb-text-secondary); }
</style>
