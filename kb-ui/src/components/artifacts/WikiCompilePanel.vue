<template>
  <div class="wiki-compile" data-test="wiki-compile-panel">
    <!-- ── 区块一：来源绑定（编译取材范围）── -->
    <section class="wiki-compile__sec">
      <div class="wiki-compile__sec-head">来源绑定</div>
      <div v-loading="sourcesLoading" class="wiki-compile__sources">
        <div
          v-for="src in sources"
          :key="src.kb_id"
          class="wiki-compile__src"
          data-test="wiki-source-row"
        >
          <div class="wiki-compile__src-main">
            <span class="wiki-compile__src-title" :data-kb="src.kb_id">
              {{ src.kb_title || src.kb_id }}
              <el-tag v-if="src.unreadable" size="small" type="info">库不可读</el-tag>
            </span>
            <span class="wiki-compile__dirs">
              <el-tag v-if="directoriesOf(src).length === 0" size="small" type="info" effect="plain">全库</el-tag>
              <el-tag v-for="d in directoriesOf(src)" :key="d" size="small" effect="plain">{{ d }}</el-tag>
            </span>
          </div>
          <el-tag size="small" :type="dirtyBadge(src.dirty).type" data-test="wiki-source-dirty">
            {{ dirtyBadge(src.dirty).text }}
          </el-tag>
          <el-button v-if="canWrite" size="small" text type="danger" data-test="wiki-source-delete" @click="removeSource(src)">解绑</el-button>
        </div>
        <p v-if="!sourcesLoading && sources.length === 0" class="wiki-compile__empty">
          尚未绑定来源——编译需要至少一个知识库来源
        </p>
      </div>
      <!-- 添加表单：域 → KB 级联 → 目录（手输路径数组；留空=全库） -->
      <div v-if="canWrite" class="wiki-compile__add" data-test="wiki-source-form">
        <el-select v-model="addDomain" class="wiki-compile__add-domain" data-test="wiki-source-domain" placeholder="选择域">
          <el-option v-for="d in domainStore.enabledDomains" :key="d.domain_id" :value="d.domain_id" :label="d.domain_id" />
        </el-select>
        <el-select v-model="addKbId" class="wiki-compile__add-kb" data-test="wiki-source-kb"
          :loading="kbsLoading" placeholder="选择知识库">
          <el-option v-for="kb in kbs" :key="kb.id" :value="kb.id" :label="kb.name" />
        </el-select>
        <el-input-tag v-model="addDirs" class="wiki-compile__add-dirs" data-test="wiki-source-dirs"
          placeholder="目录路径（回车添加，留空=全库）" />
        <el-button type="primary" size="small" :loading="savingSource" :disabled="!addDomain || !addKbId"
          data-test="wiki-source-save" @click="saveSource">绑定</el-button>
      </div>
    </section>

    <!-- ── 区块三：脏标记横幅（任一源 dirty → 建议重编译；常显于编译区块上方）── -->
    <el-alert
      v-if="anyDirty"
      type="warning"
      :closable="false"
      title="来源已变化，建议重新编译"
      description="绑定知识库的知识资产已更新，当前页面内容可能落后于来源。"
      data-test="wiki-dirty-banner"
    />

    <!-- ── 区块二：编译（状态机驱动）── -->
    <section class="wiki-compile__sec">
      <div class="wiki-compile__sec-head">编译</div>
      <div v-if="status" class="wiki-compile__flow" data-test="wiki-compile-flow" aria-label="编译流程进度">
        <div class="wiki-compile__steps">
          <div
            v-for="(step, index) in compileSteps"
            :key="step"
            class="wiki-compile__step"
            :class="stepClass(index)"
            :aria-current="index === activeStep && !isTerminal ? 'step' : undefined"
          >
            <span class="wiki-compile__step-dot">{{ index < activeStep || state === 'completed' ? '✓' : index + 1 }}</span>
            <span class="wiki-compile__step-label">{{ step }}</span>
          </div>
        </div>
        <p class="wiki-compile__flow-detail" data-test="wiki-compile-flow-detail">{{ flowDetail }}</p>
      </div>

      <!-- preparing：取材+聚类（后台任务，轮询推进） -->
      <div v-if="state === 'preparing'" class="wiki-compile__phase" data-test="wiki-compile-preparing">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在取材与聚类……（大库可能需要数分钟）</span>
        <el-button v-if="canWrite" size="small" text type="danger" data-test="wiki-compile-cancel" @click="cancelCompile">取消</el-button>
      </div>

      <!-- awaiting_toc：目录确认编辑器（编辑/勾选排除 → 确认进入写作） -->
      <WikiTocEditor
        v-else-if="state === 'awaiting_toc' && status?.toc"
        :key="status.batch_id"
        :toc="status.toc"
        :can-write="canWrite"
        :busy="confirming"
        @confirm="confirmToc"
        @cancel="cancelCompile"
      />

      <!-- writing：页级写作进度 -->
      <div v-else-if="state === 'writing'" class="wiki-compile__phase" data-test="wiki-compile-writing">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span data-test="wiki-compile-writing-progress">
          {{ flowDetail }}
        </span>
        <el-button
          v-if="canWrite"
          size="small"
          text
          type="danger"
          :loading="cancelling"
          data-test="wiki-compile-cancel"
          @click="cancelCompile"
        >取消</el-button>
      </div>

      <!-- idle / 终态：开始按钮 + 上轮报告（stats/conflicts/error 子组件） -->
      <template v-else>
        <div class="wiki-compile__idle">
          <el-tooltip
            v-if="canWrite"
            :disabled="sources.length > 0"
            content="先绑定来源再编译"
            placement="top"
          >
            <span>
              <el-button
                type="primary"
                :loading="starting"
                :disabled="sources.length === 0"
                data-test="wiki-compile-start"
                @click="startCompile"
              >开始编译</el-button>
            </span>
          </el-tooltip>
          <span class="wiki-compile__hint">{{ idleHint }}</span>
        </div>
        <WikiCompileReport v-if="status && isTerminal" :status="status" />
      </template>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useArtifactsApi } from '@/api/artifacts'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { useDomainStore } from '@/stores/domain'
import { usePolling } from '@/composables/usePolling'
import WikiCompileReport from './WikiCompileReport.vue'
import WikiTocEditor from './WikiTocEditor.vue'
import type { KbSummary } from '@/types/kb'
import type { WikiCompileStatus, WikiSource, WikiTocSection } from '@/types/artifacts'

/** 轮询周期（批次3 口径：5s）。 */
const POLL_MS = 5000
const compileSteps = ['取材', '聚类', '生成目录', '确认目录', '页面写作', '质量收尾'] as const
const IN_FLIGHT = new Set(['preparing', 'awaiting_toc', 'writing'])
const TERMINAL = new Set(['completed', 'failed', 'cancelled'])
/** 轮询态=preparing/writing——**不含 awaiting_toc**：该态服务端在等用户
 * 输入无状态变化，轮询响应会以新 toc 引用重置编辑器、静默清空半程编辑。 */
const POLL_STATES = new Set(['preparing', 'writing'])

const props = defineProps<{
  artifactId: string
  canWrite: boolean
  /** 乐观锁令牌（artifact.updated_at）——写操作随请求携带。 */
  expectedUpdatedAt?: string
}>()

const emit = defineEmits<{ changed: [] }>()

const api = useArtifactsApi()
const kbApi = useKbApi()
const domainStore = useDomainStore()

// ── 来源绑定 ──
const sources = ref<WikiSource[]>([])
const sourcesLoading = ref(false)
const addDomain = ref('')
const addKbId = ref('')
const addDirs = ref<string[]>([])
const kbs = ref<KbSummary[]>([])
const kbsLoading = ref(false)
const savingSource = ref(false)

// ── 编译状态 ──
const status = ref<WikiCompileStatus | null>(null)
/** 状态落地序号（过期响应防护，审查 I-1）：applyStatus/制品切换时推进
 * ——发起时捕获旧值的迟到响应（旧制品慢响应/动作后的旧轮询）一律丢弃。 */
let statusEpoch = 0
const starting = ref(false)
const confirming = ref(false)
const cancelling = ref(false)

const state = computed(() => String(status.value?.state ?? ''))
const isTerminal = computed(() => TERMINAL.has(state.value))
const anyDirty = computed(() => sources.value.some(s => s.dirty === true))
const activeStep = computed(() => {
  const phase = status.value?.phase
  if (state.value === 'completed') return compileSteps.length
  if (state.value === 'awaiting_toc') return 3
  if (state.value === 'writing') return phase === 'quality_check' || phase === 'finishing' ? 5 : 4
  if (phase === 'clustering') return 1
  if (phase === 'toc_generation') return 2
  if (phase === 'awaiting_toc') return 3
  if (phase === 'writing_pages') return 4
  if (phase === 'quality_check' || phase === 'finishing') return 5
  return 0
})
const flowDetail = computed(() => {
  if (state.value === 'completed') return '编译完成'
  if (state.value === 'failed') return '编译失败，详情见下方报告'
  if (state.value === 'cancelled') return '编译已取消'
  if (state.value === 'awaiting_toc') return '目录已生成，等待确认后继续写作'
  if (state.value === 'writing') {
    if (status.value?.phase === 'quality_check') return '页面写作已结束，正在检查内容质量'
    if (status.value?.phase === 'finishing') return '质量检查已结束，正在生成专题页并收尾'
    const batch = status.value?.progress?.batch
    return `正在写作页面${batch && batch !== '0/0' ? ` · 批次 ${batch}` : ''} · 已写 ${status.value?.progress?.pages_done ?? 0} 页`
  }
  if (status.value?.phase === 'clustering') return '取材完成，正在聚类'
  if (status.value?.phase === 'toc_generation') return '聚类完成，正在生成目录草案'
  return '正在读取绑定来源的知识单元'
})

function stepClass(index: number): string {
  if (state.value === 'completed' || index < activeStep.value) return 'is-done'
  if (index === activeStep.value) return state.value === 'failed' || state.value === 'cancelled'
    ? 'is-stopped' : 'is-current'
  return 'is-pending'
}

const idleHint = computed(() => {
  if (state.value === 'completed') return '上一轮编译已完成'
  if (state.value === 'failed') return '上一轮编译失败——可重新编译（已完成页命中缓存）'
  if (state.value === 'cancelled') return '上一轮编译已取消'
  return '编译将取材绑定来源 → 聚类 → 确认目录 → 自动写作'
})

/** 轮询（复用 usePolling：unmount 自动 clearInterval；启停唯一入口=
 * syncPolling）。 */
const { start: startPolling, stop: stopPolling } = usePolling(fetchStatus, POLL_MS, {
  immediate: false,
  shouldRun: () => props.canWrite && POLL_STATES.has(state.value),
})

/** 轮询单例编排（applyStatus 统一驱动）：pollRunning 守卫防每次状态落地
 * 重置 interval 节拍；制品切换后重取落态即在此**重启**（定时器已被上一
 * 制品的终态停掉——只靠 gate 不会复活，审查 MEDIUM）。初值=true 对齐
 * composable 挂载即自动 start 的事实——首个非轮询态落地即拆空转 interval
 * （审查 Minor#4：no-op 定时器每 5s 空转）。 */
let pollRunning = true
function syncPolling(): void {
  const want = props.canWrite && POLL_STATES.has(state.value)
  if (want === pollRunning) return
  pollRunning = want
  if (want) startPolling()
  else stopPolling()
}

onMounted(() => {
  addDomain.value = domainStore.currentDomain
  void loadSources()
  if (props.canWrite) void fetchStatus()
  else syncPolling()   // viewer：拆掉挂载自动起的空转 interval
})

/** 制品切换（路由复用组件不重挂）→ 推进 epoch+重置一切并重取；重取落态
 * 经 applyStatus → syncPolling 按新制品状态重启轮询。 */
watch(() => props.artifactId, () => {
  statusEpoch += 1
  status.value = null
  sources.value = []
  void loadSources()
  if (props.canWrite) void fetchStatus()
})

const directoriesOf = (src: WikiSource): string[] => src.scope?.directories ?? []

/** dirty 三态徽标（null=不可读库，显示「未知」——不泄露库内容变化信号）。 */
function dirtyBadge(dirty: boolean | null | undefined): { type: 'warning' | 'success' | 'info'; text: string } {
  if (dirty === true) return { type: 'warning', text: '来源已变化' }
  if (dirty === false) return { type: 'success', text: '最新' }
  return { type: 'info', text: '未知' }
}

async function loadSources(): Promise<void> {
  sourcesLoading.value = true
  try {
    sources.value = await api.listWikiSources(props.artifactId)
  } catch (error) {
    sources.value = []
    ElMessage.error(`加载来源绑定失败：${await apiErrorDetail(error)}`)
  } finally {
    sourcesLoading.value = false
  }
}

/** 域 → KB 级联（listKbs 必须传 domain；失败静默退化为空下拉，不挡面板）。 */
watch(addDomain, (domain) => {
  addKbId.value = ''
  if (!domain) { kbs.value = []; return }
  kbsLoading.value = true
  kbApi.listKbs(domain)
    .then(rows => { kbs.value = rows })
    .catch(() => { kbs.value = [] })
    .finally(() => { kbsLoading.value = false })
})

async function saveSource(): Promise<void> {
  savingSource.value = true
  try {
    await api.upsertWikiSource(props.artifactId, {
      kb_id: addKbId.value,
      directories: addDirs.value.map(d => d.trim()).filter(Boolean),
      expected_updated_at: props.expectedUpdatedAt,
    })
    ElMessage.success('来源已绑定')
    addKbId.value = ''
    addDirs.value = []
    emit('changed')   // 绑定写 touch 制品——必须刷新乐观锁令牌
    await loadSources()
  } catch (error) { await actionFailed('绑定来源失败', error) } finally {
    savingSource.value = false
  }
}

async function removeSource(src: WikiSource): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `解绑来源「${src.kb_title || src.kb_id}」？（不影响已生成页面，重编译时不再取材该库）`,
      '解绑来源', { type: 'warning' })
  } catch { return }
  try {
    await api.deleteWikiSource(props.artifactId, src.kb_id, props.expectedUpdatedAt)
    ElMessage.success('已解绑')
    emit('changed')   // 解绑同 touch 制品
    await loadSources()
  } catch (error) { await actionFailed('解绑失败', error) }
}

/** 状态落地：syncPolling 统一启停（awaiting_toc/终态停，preparing/writing
 * 跑）；从在飞态观测到终态时 emit changed（M4 铁律：编译页写推进乐观锁
 * 令牌——不刷新则编辑器旧令牌首次保存必 409）+ 刷新来源（编译后指纹
 * 对齐，dirty 徽标归零）。 */
function applyStatus(next: WikiCompileStatus): void {
  const wasInFlight = IN_FLIGHT.has(state.value)
  status.value = next
  statusEpoch += 1   // 新状态落地——发起于旧 epoch 的在飞轮询作废（I-1）
  syncPolling()
  if (TERMINAL.has(String(next.state)) && wasInFlight) {
    emit('changed')
    void loadSources()
  }
}

/** 拉取编译状态（轮询与首拉共用）：发起时捕获制品 id + epoch，响应回来
 * 双比对——旧制品慢响应/动作后旧轮询一律丢弃（审查 I-1）。 */
async function fetchStatus(): Promise<void> {
  const aid = props.artifactId
  const epoch = statusEpoch
  const stale = (): boolean => aid !== props.artifactId || epoch !== statusEpoch
  try {
    const next = await api.compileStatus(aid)
    if (stale()) return
    applyStatus(next)
  } catch (error) {
    if (stale()) return
    if (errorCode(error) === 'compile_not_found') {
      status.value = null   // 从未编译（无批）→ idle
      syncPolling()         // 拆挂载自动起的 interval（Minor#4）
      return
    }
    // 轮询连续失败不刷屏：停下提示一次，待用户操作/刷新页面恢复
    pollRunning = false; stopPolling()
    ElMessage.error(`获取编译进度失败：${await apiErrorDetail(error)}`)
  }
}

async function startCompile(): Promise<void> {
  starting.value = true
  try {
    const res = await api.startCompile(props.artifactId, props.expectedUpdatedAt)
    applyStatus({ batch_id: res.batch_id, state: res.state })
  } catch (error) { await actionFailed('启动编译失败', error) } finally {
    starting.value = false
  }
}

async function confirmToc(toc: WikiTocSection[]): Promise<void> {
  confirming.value = true
  try {
    const res = await api.confirmToc(props.artifactId, toc, props.expectedUpdatedAt)
    applyStatus({ ...(status.value ?? { batch_id: '' }), state: res.state })
  } catch (error) { await actionFailed('确认目录失败', error) } finally {
    confirming.value = false
  }
}

async function cancelCompile(): Promise<void> {
  cancelling.value = true
  try {
    const res = await api.cancelCompile(props.artifactId, props.expectedUpdatedAt)
    applyStatus({ ...(status.value ?? { batch_id: '' }), state: res.state })
  } catch (error) { await actionFailed('取消失败', error) } finally {
    cancelling.value = false
  }
}

/** 写操作统一错误面：409 stale_artifact → 提示 + emit changed（父级
 * reload 刷新乐观锁令牌）；其余走 detail 文案（409/422 信封）。 */
async function actionFailed(where: string, error: unknown): Promise<void> {
  if (errorCode(error) === 'stale_artifact') {
    ElMessage.error('制品已被他人修改，已刷新最新状态')
    emit('changed')
  } else {
    ElMessage.error(`${where}：${await apiErrorDetail(error)}`)
  }
}

function errorCode(error: unknown): string | undefined {
  return (error as { response?: { data?: { detail?: { code?: string } } } })
    ?.response?.data?.detail?.code
}
</script>

<style scoped>
.wiki-compile { display: flex; flex-direction: column; gap: 14px; }
/* 区块=hairline 卡（与 Wiki 阅读壳卡同一节奏）；标题走详情页 sec-head 语言
   （2026-11-13 用户两轮反馈「字又小」——11px 眉标弃用，13.5px 可读标题）。 */
.wiki-compile__sec {
  display: flex; flex-direction: column; gap: 10px;
  border: 1px solid var(--kb-border-light, #f1f5f9);
  border-radius: 10px;
  padding: 13px 16px 14px;
  background: var(--kb-bg-card, #fff);
}
.wiki-compile__sec-head {
  font-size: 13.5px; font-weight: 600;
  color: var(--kb-text-primary, inherit);
  display: flex; align-items: center; gap: 8px;
}
.wiki-compile__sec-head::before {
  content: ''; width: 3px; height: 13px; border-radius: 2px;
  background: var(--kb-accent, #0891b2); flex-shrink: 0;
}

/* ── 六步流程条：连接线 + 状态点 ── */
.wiki-compile__flow {
  padding: 14px 16px 12px; border: 1px solid var(--kb-border, #e2e8f0); border-radius: 8px;
  background: var(--kb-bg-card, #fff);
}
.wiki-compile__steps { display: flex; align-items: center; gap: 0; flex-wrap: wrap; }
.wiki-compile__step {
  display: inline-flex; align-items: center; gap: 7px; min-width: 0;
  color: var(--kb-text-tertiary, #94a3b8); font-size: 12.5px;
}
.wiki-compile__step + .wiki-compile__step { margin-left: 26px; position: relative; }
.wiki-compile__step + .wiki-compile__step::before {
  content: ''; position: absolute; left: -19px; top: 50%;
  width: 12px; height: 1px; background: var(--kb-border, #e2e8f0);
}
.wiki-compile__step-dot {
  display: inline-flex; align-items: center; justify-content: center; flex: 0 0 22px;
  height: 22px; border: 1px solid var(--kb-border, #e2e8f0); border-radius: 50%;
  font-size: 11px; font-weight: 600;
  transition: all .18s var(--kb-ease, ease);
}
.wiki-compile__step.is-done { color: var(--kb-accent, #0891b2); }
.wiki-compile__step.is-done .wiki-compile__step-dot {
  border-color: var(--kb-accent, #0891b2); background: var(--kb-accent, #0891b2); color: #fff;
}
.wiki-compile__step.is-current { color: var(--kb-accent, #0891b2); font-weight: 600; }
.wiki-compile__step.is-current .wiki-compile__step-dot {
  color: var(--kb-accent, #0891b2); background: var(--kb-accent-soft, rgba(8, 145, 178, 0.08));
  border-color: var(--kb-accent, #0891b2);
  box-shadow: 0 0 0 4px var(--kb-accent-soft, rgba(8, 145, 178, 0.08));
}
.wiki-compile__step.is-stopped { color: var(--kb-warning, #f59e0b); font-weight: 600; }
.wiki-compile__step.is-stopped .wiki-compile__step-dot {
  border-color: var(--kb-warning, #f59e0b); color: var(--kb-warning, #f59e0b);
}
.wiki-compile__flow-detail {
  margin: 10px 0 0; padding-top: 9px; border-top: 1px solid var(--kb-border-light, #f1f5f9);
  color: var(--kb-text-secondary, #64748b); font-size: 12.5px;
}
@media (max-width: 1100px) { .wiki-compile__steps { gap: 8px; } .wiki-compile__step + .wiki-compile__step { margin-left: 0; } .wiki-compile__step + .wiki-compile__step::before { display: none; } }

/* ── 来源绑定 ── */
.wiki-compile__sources { display: flex; flex-direction: column; gap: 6px; }
.wiki-compile__src {
  display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  border: 1px solid var(--kb-border, #e2e8f0); border-radius: 8px;
  background: var(--kb-bg-card, #fff);
  transition: border-color .14s var(--kb-ease, ease), box-shadow .14s var(--kb-ease, ease);
}
.wiki-compile__src:hover {
  border-color: var(--kb-accent-medium, rgba(8, 145, 178, 0.15));
  box-shadow: var(--kb-shadow-xs, 0 1px 2px rgba(0, 0, 0, 0.04));
}
.wiki-compile__src-main { flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.wiki-compile__src-title { font-size: 13px; font-weight: 600; color: var(--kb-text-primary, inherit); display: inline-flex; align-items: center; gap: 4px; }
.wiki-compile__dirs { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.wiki-compile__empty { margin: 0; padding: 10px 2px; font-size: 12.5px; color: var(--kb-text-tertiary); }
.wiki-compile__add { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
.wiki-compile__add-domain { width: 180px; }
.wiki-compile__add-kb { width: 260px; }
.wiki-compile__add-dirs { flex: 1; min-width: 240px; }

.wiki-compile__phase {
  display: flex; align-items: center; gap: 10px; font-size: 13.5px;
  padding: 10px 14px; border-radius: 8px;
  background: var(--kb-bg-card-hover, #f8fafc);
  border: 1px solid var(--kb-border-light, #f1f5f9);
}
.wiki-compile__phase .el-icon { color: var(--kb-accent, #0891b2); }
.wiki-compile__idle { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.wiki-compile__hint { font-size: 12.5px; color: var(--kb-text-tertiary); }
</style>
