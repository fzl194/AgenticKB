<template>
  <div v-if="key" class="mkey-panel">
    <!-- 基本信息 -->
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="名称">{{ key.name }}</el-descriptions-item>
      <el-descriptions-item label="知识域">
        <el-tag size="small">{{ key.domain }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="密钥标识">{{ key.key_prefix }}…</el-descriptions-item>
      <el-descriptions-item label="最近使用">{{ fmtTime(key.last_used_at) || '从未' }}</el-descriptions-item>
      <el-descriptions-item label="创建时间">{{ fmtTime(key.created_at) }}</el-descriptions-item>
      <el-descriptions-item label="上次轮换">{{ fmtTime(key.rotated_at) || '—' }}</el-descriptions-item>
    </el-descriptions>

    <!-- 接入配置 JSON -->
    <section class="mkey-panel__card">
      <div class="mkey-panel__card-head">
        <div>
          <h4 class="mkey-panel__title">Agent 接入配置</h4>
          <p class="mkey-panel__desc">
            选一种格式，点击复制后粘贴进你的 Agent / MCP 客户端（{{ mcpEndpoint }}）。
            平时显示 <code>__KEY__</code> 占位——密钥明文只在生成/轮换时显示一次，丢了只能轮换。
          </p>
        </div>
        <el-radio-group v-model="configFormat" size="small" :disabled="!freshKey">
          <el-radio-button value="generic">通用</el-radio-button>
          <el-radio-button value="dify">dify</el-radio-button>
        </el-radio-group>
      </div>
      <div class="mkey-panel__code-wrap">
        <button class="mkey-panel__copy" title="复制" @click="copy(configJson)">复制</button>
        <pre class="mkey-panel__code">{{ configJson }}</pre>
      </div>
    </section>

    <!-- 工具开关 -->
    <section class="mkey-panel__card">
      <div class="mkey-panel__card-head">
        <div>
          <h4 class="mkey-panel__title">开放的工具</h4>
          <p class="mkey-panel__desc">关掉的工具对这把钥匙的 Agent 完全不可见。至少保留一个。</p>
        </div>
        <el-button size="small" :loading="savingTools" :disabled="readonly || !toolsDirty" @click="saveTools">保存</el-button>
      </div>
      <div class="mkey-panel__tools">
        <div v-for="t in ALL_TOOLS" :key="t.name" class="mkey-panel__tool">
          <el-switch v-model="toolOn[t.name]" :disabled="readonly || savingTools" />
          <div class="mkey-panel__tool-text">
            <span class="mkey-panel__tool-name">{{ t.name }}</span>
            <span class="mkey-panel__tool-desc">{{ t.label }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 开放库 -->
    <section class="mkey-panel__card">
      <div class="mkey-panel__card-head">
        <div>
          <h4 class="mkey-panel__title">开放的知识库（{{ key.domain }} 域）</h4>
          <p class="mkey-panel__desc">
            这把钥匙只能访问这里勾选的库。数据源固定为钥匙所属域——与页面顶部的当前域切换无关。
          </p>
        </div>
        <el-button size="small" :loading="savingKbs" :disabled="readonly || !kbsDirty" @click="saveOpenKbs">保存</el-button>
      </div>
      <el-checkbox-group v-model="selectedKbs" class="mkey-panel__kbs" :disabled="readonly">
        <el-checkbox v-for="kb in domainKbs" :key="kb.id" :value="kb.id" :label="kb.id">
          {{ kb.name }}<span class="mkey-panel__kb-meta">{{ kb.document_count }} 文档</span>
        </el-checkbox>
      </el-checkbox-group>
      <el-empty v-if="!domainKbs.length" description="该域没有你能看到的知识库" :image-size="60" />
    </section>

    <!-- 提示词 -->
    <section class="mkey-panel__card">
      <div class="mkey-panel__card-head">
        <div>
          <h4 class="mkey-panel__title">提示词与工具说明</h4>
          <p class="mkey-panel__desc">留空 = 使用默认文案。</p>
        </div>
        <el-button size="small" :loading="savingPrompt" :disabled="readonly || !promptDirty" @click="savePrompt">保存</el-button>
      </div>
      <el-input
        v-model="instructions"
        type="textarea"
        :rows="5"
        maxlength="4000"
        show-word-limit
        :disabled="readonly"
        placeholder="留空使用默认提示词。写清楚你的 Agent 该怎么用这套知识库——例如业务口径、常用问法、注意事项。"
      />
      <div class="mkey-panel__descs">
        <div v-for="t in ALL_TOOLS" :key="t.name" class="mkey-panel__desc-row">
          <span class="mkey-panel__desc-name">{{ t.name }}</span>
          <el-input
            v-model="toolDescs[t.name]"
            type="textarea"
            :rows="2"
            maxlength="2000"
            :disabled="readonly"
            :placeholder="`默认：${t.label}`"
          />
        </div>
      </div>
    </section>

    <!-- 明文一次性展示（轮换后） -->
    <el-alert v-if="freshKey" type="success" :closable="false" class="mkey-panel__fresh">
      <template #title>
        新密钥（仅此一次可见）：<code class="mkey-panel__key">{{ freshKey }}</code>
        <el-button size="small" text type="primary" @click="copy(freshKey)">复制</el-button>
      </template>
    </el-alert>

    <!-- 危险操作 -->
    <section v-if="!readonly" class="mkey-panel__card mkey-panel__card--danger">
      <el-button :loading="rotating" @click="rotate">轮换密钥（旧钥立即失效）</el-button>
      <el-button type="danger" plain :loading="revoking" @click="revoke">吊销这把钥匙（不可恢复）</el-button>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import type { McpKeyItem } from '@/types/kb'

/** 三件套（与后端 MCP_TOOL_NAMES 一致）；label 为默认文案摘要。 */
const ALL_TOOLS = [
  { name: 'search_knowledge', label: '检索知识证据（domain 单域免传）' },
  { name: 'get_knowledge', label: '深入读取：浏览层级 / 取原文 / 看能力 / 导航 / 查表格' },
  { name: 'upload_document', label: '上传一个或多个文件（zip 自动解压）入库并自动排队挖掘（跑完才可检索）' },
] as const

const props = defineProps<{
  keyItem: McpKeyItem
  /** 钥匙所属域的可见库清单（父组件按 key.domain 拉取，不跟随页面当前域）。 */
  domainKbs: { id: string; name: string; document_count: number }[]
}>()

const emit = defineEmits<{
  /** 任一配置保存成功后上报最新钥匙行（父组件同步列表）。 */
  (e: 'updated', item: McpKeyItem): void
  /** 轮换成功：父组件刷新列表（明文留在本面板展示）。 */
  (e: 'rotated', keyId: string): void
  /** 吊销成功。 */
  (e: 'revoked', keyId: string): void
}>()

const kbApi = useKbApi()

const key = computed(() => props.keyItem)
const readonly = computed(() => props.keyItem.status === 'revoked')

const configFormat = ref<'generic' | 'dify'>('generic')
/** 轮换产生的新明文——仅面板内展示一次，用于预填接入 JSON。 */
const freshKey = ref('')

const toolOn = ref<Record<string, boolean>>({})
const savingTools = ref(false)
const selectedKbs = ref<string[]>([])
const savingKbs = ref(false)
const instructions = ref('')
const toolDescs = ref<Record<string, string>>({})
const savingPrompt = ref(false)
const rotating = ref(false)
const revoking = ref(false)

const mcpEndpoint = computed(() => `${window.location.hostname}:9000/mcp`)

function initFromKey(item: McpKeyItem) {
  const savedOn = item.open_tools
  for (const t of ALL_TOOLS) {
    toolOn.value[t.name] = savedOn == null ? true : savedOn.includes(t.name)
    toolDescs.value[t.name] = item.tool_descriptions?.[t.name] ?? ''
  }
  instructions.value = item.instructions ?? ''
  // 幽灵 id 剔除：只保留当前仍可见的库——软删/权限收走的库自动从勾选中消失，
  // 不让失效 id 混进下一次保存请求（后端也会剔除，这里保证所见即所得）
  const visibleIds = new Set(props.domainKbs.map(k => k.id))
  selectedKbs.value = item.open_kb_ids.filter(id => visibleIds.has(id))
  freshKey.value = ''
  configFormat.value = 'generic'
}

// 钥匙行或域库清单变化（父组件刷新列表后传新对象）都重置表单
watch(() => [props.keyItem.id, props.domainKbs], () => initFromKey(props.keyItem), { immediate: true })

const configJson = computed(() => {
  const url = `http://${mcpEndpoint.value}`
  const placeholder = '__KEY__'
  const generic = {
    mcpServers: {
      knowledge: {
        // 显式声明传输类型：Claude 等客户端要求远程服务带 type，
        // 缺省时部分客户端按 stdio 解析而报错
        type: 'http',
        url,
        headers: { Authorization: `Bearer ${placeholder}` },
      },
    },
  }
  const dify = { server_url: url, authorization: `Bearer ${placeholder}` }
  const raw = JSON.stringify(configFormat.value === 'dify' ? dify : generic, null, 2)
  // freshKey 只在轮换后短暂持有；平时展示占位符
  return freshKey.value ? raw.replace(placeholder, freshKey.value) : raw
})

const toolsDirty = computed(() => {
  const on = ALL_TOOLS.filter(t => toolOn.value[t.name]).map(t => t.name)
  const saved = props.keyItem.open_tools
  if (saved == null) return on.length !== ALL_TOOLS.length
  return JSON.stringify(on) !== JSON.stringify(saved)
})

const kbsDirty = computed(() => {
  const a = [...selectedKbs.value].sort()
  const b = [...(props.keyItem.open_kb_ids ?? [])].sort()
  return JSON.stringify(a) !== JSON.stringify(b)
})

const promptDirty = computed(() => {
  const savedInstr = props.keyItem.instructions ?? ''
  const savedDescs = props.keyItem.tool_descriptions ?? {}
  const nowDescs: Record<string, string> = {}
  for (const t of ALL_TOOLS) {
    const v = (toolDescs.value[t.name] || '').trim()
    if (v) nowDescs[t.name] = v
  }
  return instructions.value.trim() !== savedInstr.trim()
    || JSON.stringify(nowDescs) !== JSON.stringify(savedDescs)
})

async function saveTools() {
  const on = ALL_TOOLS.filter(t => toolOn.value[t.name]).map(t => t.name)
  if (!on.length) { ElMessage.warning('至少保留一个工具'); return }
  savingTools.value = true
  try {
    const item = await kbApi.putMcpKeyConfig(props.keyItem.id, { open_tools: on })
    emit('updated', item)
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
    const r = await kbApi.putMcpKeyOpenKbs(props.keyItem.id, selectedKbs.value)
    selectedKbs.value = [...r.open_kb_ids]  // 以响应为准（后端剔除了失效勾选）
    emit('updated', { ...props.keyItem, open_kb_ids: r.open_kb_ids })
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
    const item = await kbApi.putMcpKeyConfig(props.keyItem.id, {
      instructions: instructions.value,
      tool_descriptions: descs,
    })
    emit('updated', item)
    ElMessage.success('提示词已保存')
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    savingPrompt.value = false
  }
}

async function rotate() {
  try {
    await ElMessageBox.confirm(
      `轮换后「${props.keyItem.name}」的旧密钥立即失效，正在使用它的 Agent 会断连。确定继续？`,
      '轮换密钥', { type: 'warning', confirmButtonText: '轮换', cancelButtonText: '取消' },
    )
  } catch { return }
  rotating.value = true
  try {
    const r = await kbApi.rotateMcpKey(props.keyItem.id)
    freshKey.value = r.key
    configFormat.value = 'generic'
    ElMessage.success('密钥已轮换；旧密钥立即失效')
    emit('rotated', props.keyItem.id)
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    rotating.value = false
  }
}

async function revoke() {
  try {
    await ElMessageBox.confirm(
      `吊销后「${props.keyItem.name}」立即失效且不可恢复，正在使用它的 Agent 会断连。确定吊销？`,
      '吊销钥匙', { type: 'warning', confirmButtonText: '吊销', cancelButtonText: '取消' },
    )
  } catch { return }
  revoking.value = true
  try {
    await kbApi.revokeMcpKey(props.keyItem.id)
    ElMessage.success('钥匙已吊销')
    emit('revoked', props.keyItem.id)
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    revoking.value = false
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
</script>

<style scoped>
.mkey-panel { display: flex; flex-direction: column; gap: 16px; }

.mkey-panel__card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
}

.mkey-panel__card--danger {
  flex-direction: row;
  border-color: var(--el-color-danger-light-7);
}

.mkey-panel__card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.mkey-panel__title { margin: 0; font-size: 14px; font-weight: 650; }
.mkey-panel__desc { margin: 4px 0 0; font-size: 12.5px; color: var(--kb-text-secondary); line-height: 1.6; max-width: 560px; }

.mkey-panel__code-wrap { position: relative; }
.mkey-panel__code {
  margin: 0;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--el-fill-color-darker, #1e1e1e);
  color: var(--el-color-success-light-3, #67c23a);
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12.5px;
  line-height: 1.7;
  overflow-x: auto;
}
.mkey-panel__copy {
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
.mkey-panel__copy:hover { color: var(--kb-accent, #3b82f6); border-color: var(--kb-accent, #3b82f6); }

.mkey-panel__tools { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
.mkey-panel__tool { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid var(--kb-border-light); border-radius: 8px; }
.mkey-panel__tool-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.mkey-panel__tool-name { font-size: 13px; font-weight: 600; font-family: var(--el-font-family-mono, monospace); }
.mkey-panel__tool-desc { font-size: 12px; color: var(--kb-text-tertiary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.mkey-panel__kbs { display: flex; flex-direction: column; gap: 4px; }
.mkey-panel__kb-meta { margin-left: 6px; font-size: 12px; color: var(--kb-text-tertiary); }

.mkey-panel__descs { display: flex; flex-direction: column; gap: 10px; }
.mkey-panel__desc-row { display: grid; grid-template-columns: 180px 1fr; gap: 10px; align-items: start; }
.mkey-panel__desc-name { padding-top: 6px; font-size: 12.5px; font-weight: 600; font-family: var(--el-font-family-mono, monospace); color: var(--kb-text-secondary); }

.mkey-panel__fresh :deep(.el-alert__title) { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mkey-panel__key {
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--el-fill-color);
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  word-break: break-all;
}
</style>
