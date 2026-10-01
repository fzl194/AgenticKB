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
          <p class="mkey-panel__desc">
            默认文案已预填，直接在原文上增改；内容与默认一致时按默认下发（默认文案将来更新也能跟随）。
          </p>
        </div>
        <div class="mkey-panel__prompt-actions">
          <el-button size="small" :disabled="readonly" @click="restoreDefaults">恢复默认值</el-button>
          <el-button size="small" :loading="savingPrompt" :disabled="readonly || !promptDirty" @click="savePrompt">保存</el-button>
        </div>
      </div>
      <el-input
        v-model="instructions"
        type="textarea"
        :rows="8"
        maxlength="4000"
        show-word-limit
        :disabled="readonly"
        placeholder="正在加载默认提示词…"
        @input="markPromptTouched"
      />
      <div class="mkey-panel__descs">
        <div v-for="t in ALL_TOOLS" :key="t.name" class="mkey-panel__desc-row">
          <span class="mkey-panel__desc-name">{{ t.name }}</span>
          <div class="mkey-panel__desc-edit">
            <el-input
              v-model="toolDescs[t.name]"
              type="textarea"
              :rows="4"
              maxlength="2000"
              :disabled="readonly"
              placeholder="正在加载默认说明…"
              @input="markPromptTouched"
            />
            <el-collapse v-if="paramRows[t.name]?.length" class="mkey-panel__params">
              <el-collapse-item :title="`参数（${paramRows[t.name].length}）`">
                <table class="mkey-panel__param-table">
                  <thead><tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr></thead>
                  <tbody>
                    <tr v-for="p in paramRows[t.name]" :key="p.name">
                      <td><code>{{ p.name }}</code></td>
                      <td>{{ p.type }}</td>
                      <td>{{ p.required ? '是' : '否' }}</td>
                      <td>{{ p.description }}</td>
                    </tr>
                  </tbody>
                </table>
              </el-collapse-item>
            </el-collapse>
          </div>
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

<script lang="ts">
import type { McpToolMeta } from '@/types/kb'

/**
 * tool-meta 的模块级缓存（静态内容）：抽屉多次打开不重复请求；
 * 失败清缓存允许下次重试。放普通 <script> 块才是真正的模块作用域——
 * <script setup> 里的变量是组件实例级的。
 */
let metaPromise: Promise<McpToolMeta> | null = null

export function loadMcpToolMeta(
  fetchMeta: () => Promise<McpToolMeta>,
): Promise<McpToolMeta> {
  metaPromise ??= fetchMeta().catch(reason => {
    metaPromise = null
    throw reason
  })
  return metaPromise
}

/** 测试专用：清模块级缓存，避免用例间串味。 */
export function resetMcpToolMetaCache(): void {
  metaPromise = null
}
</script>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import type { McpKeyItem } from '@/types/kb'

/** 三件套（与后端 MCP_TOOL_NAMES 一致）；label 为默认文案摘要。
 * 58号：upload_document → manage_files（上传/替换双 action，不提供删除/移动/重命名）。 */
const ALL_TOOLS = [
  { name: 'search_knowledge', label: '检索知识证据（domain 单域免传）' },
  { name: 'get_knowledge', label: '深入读取：浏览层级 / 逐层看目录 / 取原文 / 看能力 / 导航 / 查表格' },
  { name: 'manage_files', label: '管理文件：上传到指定目录 / 替换任意本地文件（zip 自动解压，自动排队挖掘；不提供删除/移动/重命名）' },
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
/**
 * 轮换产生的新明文——仅面板内展示一次，用于预填接入 JSON。
 * 生命周期：同一把钥匙的任何刷新（轮换后的 reload、保存后的 updated）都不抹掉；
 * 换钥匙即清空；关闭抽屉由父组件 destroy-on-close 整体卸载，重开不再出现
 * （内网 2026-09-29 实发缺陷：轮换后明文被 reload 换新 keyItem 对象触发
 * 的表单重置当场清掉，用户永远抄不到）。
 */
const freshKey = ref('')
let lastKeyId = ''

/** 工具默认文案与参数 schema——预填默认值、参数表渲染用。 */
const meta = ref<McpToolMeta | null>(null)

const defaultInstructions = computed(() => meta.value?.instructions ?? '')
function defaultDesc(name: string): string {
  return meta.value?.tools.find(t => t.name === name)?.description ?? ''
}

const toolOn = ref<Record<string, boolean>>({})
const savingTools = ref(false)
const selectedKbs = ref<string[]>([])
const savingKbs = ref(false)
const instructions = ref('')
const toolDescs = ref<Record<string, string>>({})
const savingPrompt = ref(false)
const rotating = ref(false)
const revoking = ref(false)
/**
 * 用户是否动过提示词/工具说明（@input 置位，initFromKey/恢复默认复位）。
 * meta 晚到的预填补齐以此为闸门：不能用 promptDirty——meta 落地的瞬间默认值
 * 从空变非空，dirty 立刻为 true，会把要做的预填自己挡掉。
 */
let promptTouched: boolean = false

function markPromptTouched(): void {
  promptTouched = true
}

const mcpEndpoint = computed(() => `${window.location.hostname}:9000/mcp`)

function initFromKey(item: McpKeyItem) {
  const savedOn = item.open_tools
  for (const t of ALL_TOOLS) {
    toolOn.value[t.name] = savedOn == null ? true : savedOn.includes(t.name)
    // 默认值预填：没存过自定义 → 显示默认文案，用户在原文上增改
    toolDescs.value[t.name] = item.tool_descriptions?.[t.name] ?? defaultDesc(t.name)
  }
  instructions.value = item.instructions ?? defaultInstructions.value
  // 幽灵 id 剔除：只保留当前仍可见的库——软删/权限收走的库自动从勾选中消失，
  // 不让失效 id 混进下一次保存请求（后端也会剔除，这里保证所见即所得）
  const visibleIds = new Set(props.domainKbs.map(k => k.id))
  selectedKbs.value = item.open_kb_ids.filter(id => visibleIds.has(id))
  promptTouched = false
  // 同钥匙刷新不动 freshKey（会话内一直可见）；只有换钥匙才清
  if (lastKeyId !== item.id) freshKey.value = ''
  lastKeyId = item.id
  configFormat.value = 'generic'
}

// 钥匙行或域库清单变化（父组件刷新列表后传新对象）都重置表单。
// 隐式耦合：若父组件复用同一数组引用（原地改 domainKbs），watch 源不变则不触发重置。
watch(() => [props.keyItem.id, props.domainKbs], () => initFromKey(props.keyItem), { immediate: true })

onMounted(async () => {
  try {
    meta.value = await loadMcpToolMeta(() => kbApi.getMcpToolMeta())
  } catch {
    // 默认文案拉不到：字段保持空、保存仍可用（存的全是自定义），不打断配置
  }
})

// meta 晚到时按默认预填补齐——仅当用户任何表单都没动过（动过任何一处都不打扰，
// 否则工具开关/开放库勾选也会被 initFromKey 一并回滚）
watch(meta, () => {
  if (!promptTouched && !toolsDirty.value && !kbsDirty.value) initFromKey(props.keyItem)
})

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
  // 生效值 = 已存自定义（非空）否则默认文案；与显示内容比对决定可保存性
  const effInstr = (props.keyItem.instructions ?? '').trim() || defaultInstructions.value.trim()
  if (instructions.value.trim() !== effInstr) return true
  return ALL_TOOLS.some(t => {
    const eff = (props.keyItem.tool_descriptions?.[t.name] ?? '').trim() || defaultDesc(t.name).trim()
    return (toolDescs.value[t.name] || '').trim() !== eff
  })
})

/** 工具参数表行（fastmcp 从函数签名+docstring Args 生成进 inputSchema）。 */
interface ParamRow { name: string; type: string; required: boolean; description: string }

function paramTypeLabel(prop: Record<string, unknown>): string {
  if (typeof prop.type === 'string') return prop.type
  const variants = ['anyOf', 'oneOf']
    .flatMap(key => (Array.isArray(prop[key]) ? prop[key] as Array<Record<string, unknown>> : []))
    .map(v => v.type)
    .filter((t): t is string => typeof t === 'string' && t !== 'null')
  return variants.length ? variants.join(' | ') : '—'
}

const paramRows = computed<Record<string, ParamRow[]>>(() => {
  const rowsByTool: Record<string, ParamRow[]> = {}
  for (const t of ALL_TOOLS) {
    const schema = meta.value?.tools.find(m => m.name === t.name)?.parameters
    const properties = schema?.properties ?? {}
    const required = new Set(schema?.required ?? [])
    rowsByTool[t.name] = Object.entries(properties).map(([name, prop]) => ({
      name,
      type: paramTypeLabel(prop),
      required: required.has(name),
      description: typeof prop.description === 'string' ? prop.description : '',
    }))
  }
  return rowsByTool
})

/** 一键把提示词与全部工具说明填回默认文案（仍需点保存生效）。 */
function restoreDefaults() {
  instructions.value = defaultInstructions.value
  for (const t of ALL_TOOLS) {
    toolDescs.value[t.name] = defaultDesc(t.name)
  }
  ElMessage.info('已填回默认文案，点「保存」生效')
}

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
    // 与默认一致 → 提交空：后端语义 空串/缺项=恢复默认。不把默认文案冻结成
    // 自定义副本，将来默认文案更新时这些钥匙能跟着走。
    const instrIsDefault = instructions.value.trim() === defaultInstructions.value.trim()
    const descs: Record<string, string> = {}
    for (const t of ALL_TOOLS) {
      const v = (toolDescs.value[t.name] || '').trim()
      if (v && v !== defaultDesc(t.name).trim()) descs[t.name] = v
    }
    const item = await kbApi.putMcpKeyConfig(props.keyItem.id, {
      instructions: instrIsDefault ? '' : instructions.value,
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

// el-collapse/el-input 等在测试里是 stub（无法可靠交互），把状态与动作显式
// 暴露出去供断言（与 SettingsView/McpAccessView 同一套路数）。
defineExpose({
  instructions, toolDescs, freshKey, promptDirty, meta,
  rotate, savePrompt, restoreDefaults,
})
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
.mkey-panel__desc-edit { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.mkey-panel__prompt-actions { display: flex; gap: 8px; }

.mkey-panel__params { border: 1px dashed var(--kb-border-light); border-radius: 6px; }
.mkey-panel__params :deep(.el-collapse-item__header) { height: 34px; padding: 0 10px; font-size: 12px; color: var(--kb-text-secondary); background: transparent; border-bottom: 0; }
.mkey-panel__params :deep(.el-collapse-item__wrap) { background: transparent; border-bottom: 0; }
.mkey-panel__params :deep(.el-collapse-item__content) { padding: 0 10px 10px; }
.mkey-panel__param-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mkey-panel__param-table th { padding: 5px 8px; text-align: left; font-weight: 600; color: var(--kb-text-tertiary); border-bottom: 1px solid var(--kb-border-light); white-space: nowrap; }
.mkey-panel__param-table td { padding: 5px 8px; vertical-align: top; color: var(--kb-text-secondary); border-bottom: 1px solid var(--kb-border-light); line-height: 1.6; }
.mkey-panel__param-table tr:last-child td { border-bottom: none; }
.mkey-panel__param-table td:first-child code { font-family: var(--el-font-family-mono, monospace); color: var(--kb-text-primary); }
.mkey-panel__param-table td:nth-child(2), .mkey-panel__param-table td:nth-child(3) { white-space: nowrap; color: var(--kb-text-tertiary); }

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
