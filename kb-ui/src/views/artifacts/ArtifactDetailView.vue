<template>
  <div class="artifact-detail">
    <!-- Header：图标+标题、徽章成组（高保真 ar-title/ar-badges 排版）+ 生命周期操作 -->
    <div class="artifact-detail__header">
      <div class="artifact-detail__head-left">
        <router-link to="/artifacts" class="artifact-back">
          <el-icon><ArrowLeft /></el-icon>
          <span>知识制品</span>
        </router-link>
        <div class="artifact-detail__title-row">
          <span v-if="artifact" class="artifact-detail__kindicon" :class="`is-${artifact.kind}`" aria-hidden="true">
            {{ artifact.kind === 'wiki' ? 'W' : 'T' }}
          </span>
          <h2 class="artifact-detail__title">{{ artifact?.title || '加载中…' }}</h2>
        </div>
        <div v-if="artifact" class="artifact-detail__badges">
          <el-tag size="small" :type="statusTagType(artifact.status)">
            {{ statusLabel(artifact.status) }}
          </el-tag>
          <el-tag size="small" effect="plain">
            {{ artifact.kind === 'wiki' ? 'Wiki' : '表格' }}
          </el-tag>
          <!-- D01（66号 §5）：服务版本 tag——rev>0 且非停用时点明「rN 正在服务」，
               与下方草稿 tag 并列，让「正式版在服务 + 另有待发布草稿」的并存一眼可读。 -->
          <el-tag v-if="servingRevisionLabel" size="small" type="success" effect="plain">
            {{ servingRevisionLabel }}
          </el-tag>
          <!-- 草稿 tag 与 servingRevisionLabel 同源（Task 3.3 审查 Minor）：有
               服务版（非停用且 rev>0）才说「另有待发布内容」；停用/rev0 语境一律
               「draft 开放中」——deprecated+draft 边角不再出现「另有待发布」悬空。 -->
          <el-tag v-if="artifact.draft_open" size="small" type="warning" effect="plain">
            {{ servingRevisionLabel ? '另有待发布内容' : 'draft 开放中' }}
          </el-tag>
          <el-tag size="small" :type="artifact.visibility === 'public' ? 'success' : 'info'">
            {{ artifact.visibility === 'public' ? '公开' : '私有' }}
          </el-tag>
        </div>
      </div>
      <div v-if="artifact && canWrite" class="artifact-detail__actions">
        <el-tooltip
          content="无待发布变更（编辑数据或改列后自动检出草稿）"
          :disabled="hasPendingChanges"
          placement="bottom"
        >
          <span>
            <el-button
              type="primary"
              data-test="open-publish"
              :disabled="!hasPendingChanges"
              @click="publishVisible = true"
            >发布</el-button>
          </span>
        </el-tooltip>
        <el-button v-if="artifact.status !== 'deprecated'" @click="deprecateArtifact">停用</el-button>
        <el-button v-if="artifact.status === 'deprecated'" @click="republishArtifact">恢复</el-button>
        <el-button v-if="artifact.draft_open" type="warning" plain @click="discardDraftAction">丢弃 draft</el-button>
        <!-- 008 删除门禁：owner 且已停用才可删（级联硬删不可恢复）；未停用时置灰指路 -->
        <el-tooltip
          :content="artifact.status === 'deprecated'
            ? '级联硬删（行/列/版本/成员），不可恢复——请再次确认'
            : '必须先停用（deprecate）制品，删除入口才会开放'"
          placement="bottom"
        >
          <span>
            <el-button
              v-if="isOwner"
              type="danger"
              plain
              :disabled="artifact.status !== 'deprecated'"
              data-test="delete-artifact"
              @click="deleteArtifact"
            >删除</el-button>
          </span>
        </el-tooltip>
      </div>
    </div>

    <p v-if="artifact?.description" class="artifact-detail__desc">{{ artifact.description }}</p>

    <!-- Not found / no access -->
    <div v-if="!loading && loadError" class="artifact-detail__missing">
      <EmptyState :text="loadError" />
      <el-button type="primary" @click="reload">重试</el-button>
    </div>
    <div v-else-if="!loading && !artifact" class="artifact-detail__missing">
      <EmptyState text="制品不存在或无权访问" />
      <router-link to="/artifacts">
        <el-button type="primary">返回列表</el-button>
      </router-link>
    </div>

    <el-tabs v-if="artifact" v-model="activeTab" class="artifact-detail__tabs">
      <!-- ── 设计态：导入向导 / 列定义 / 行编辑三面板（owner+editor；viewer 无设计态）── -->
      <el-tab-pane name="design">
        <template #label><el-icon class="artifact-detail__tab-icon"><EditPen /></el-icon>设计态</template>
        <!-- L01 空制品引导（66号 §4）：刚创建的空制品给「下一步做什么」指路
             ——空 Wiki 提示从知识库生成；空表格提示上传 Excel 或编辑字段。 -->
        <el-alert
          v-if="emptyGuide"
          type="info"
          :closable="false"
          class="artifact-detail__empty-guide"
          data-test="design-empty-guide"
          :title="emptyGuide.title"
          :description="emptyGuide.description"
        />
        <!-- wiki 分支（2026-11-13 重构）：编译面板在上；内容区=统一三栏视图
             （目录｜正文默认渲染｜近邻图谱）——「页面｜图谱」切换取消，
             近邻并入右栏；canWrite 开写入口（编辑正文/页面属性/新建/删除/目录拖拽）。 -->
        <template v-if="artifact.kind === 'wiki'">
          <WikiCompilePanel
            :artifact-id="artifactId"
            :can-write="canWrite"
            :expected-updated-at="artifact.updated_at"
            @changed="reloadAll"
          />
          <WikiReaderPanel
            :key="`design-${artifactId}-${artifact.updated_at}`"
            :artifact-id="artifactId"
            :can-write="canWrite"
            :expected-updated-at="artifact.updated_at"
            @changed="reloadAll"
          />
        </template>
        <template v-else-if="canWrite">
          <ArtifactImportWizard
            :artifact-id="artifactId"
            :active="activeTab === 'design'"
            @imported="reloadAll"
          />
          <div class="artifact-detail__sec-head">
            <span class="artifact-detail__sec-title">列定义</span>
            <span class="artifact-detail__sec-sub">{{ artifact.schema.length }} 列 · 主键/类型/枚举在这里管理</span>
          </div>
          <ArtifactSchemaPanel
            :artifact-id="artifactId"
            :can-write="canWrite"
            :columns="artifact.schema"
            :expected-updated-at="artifact.updated_at"
            @changed="reloadAll"
          />
          <div class="artifact-detail__sec-head">
            <span class="artifact-detail__sec-title">数据编辑</span>
            <span class="artifact-detail__sec-sub">双击单元格编辑所在行 · 草稿口径</span>
          </div>
          <ArtifactRowsPanel
            ref="rowsPanel"
            :artifact-id="artifactId"
            :can-write="canWrite"
            :columns="artifact.schema"
            :expected-updated-at="artifact.updated_at"
            :draft-open="artifact.draft_open"
            @changed="reload"
          />
        </template>
        <EmptyState v-else text="设计态仅 owner / editor 可见（你当前是 viewer——请看运行态）" />
      </el-tab-pane>

      <!-- ── 运行态：说明卡子组件 + 只读行表格（本棒可用）── -->
      <el-tab-pane name="runtime" lazy>
        <template #label><el-icon class="artifact-detail__tab-icon"><Reading /></el-icon>运行态</template>
        <div class="artifact-detail__meta-bar">
          <!-- C01：说明卡（描述/使用说明/版本/消费方式 + artifact_ref 复制 +
               Agent 三条件区）整体抽入子组件——本视图已被多任务触碰，控行数。 -->
          <ArtifactUsagePanel :artifact="artifact" class="artifact-detail__desc-card" />
          <el-button data-test="edit-meta" :icon="Edit" @click="openMetaEdit">编辑元信息</el-button>
        </div>

        <!-- wiki 运行态阅读器/图谱（63号批次2 + 66号 D03.1）：面板自加载
             current_revision 投影；「页面｜图谱」内容区切换（只读——图谱
             draft=false 读正式版）。表格制品走下方只读行表（互斥）。 -->
        <template v-if="artifact.kind === 'wiki'">
          <!-- Q01 检索状态条（66号批次5 Task 5.3）：说明卡下方、内容区切换
               上方——切片向量四计数（done/total 百分比），failed>0/投影缺失
               黄色警示 + 修复投影入口（editor+；修复=幂等重入投影不动正文）。 -->
          <div
            v-if="wikiStatus && wikiStatus.revision > 0"
            class="artifact-detail__search-status"
            :class="{ 'is-warning': wikiUnits.failed > 0 || wikiUnits.total === 0 }"
            data-test="wiki-search-status-bar"
          >
            <el-tag
              v-if="wikiUnits.failed > 0 || wikiUnits.total === 0"
              type="warning" size="small"
            >检索异常</el-tag>
            <el-tag v-else-if="wikiUnits.pending > 0" size="small">向量处理中</el-tag>
            <el-tag v-else type="success" size="small">检索就绪</el-tag>
            <span class="artifact-detail__search-status-text">{{ wikiStatusText }}</span>
            <el-button
              v-if="canWrite && (wikiUnits.failed > 0 || wikiUnits.total === 0)"
              size="small" type="warning" plain
              :loading="wikiRepairing"
              data-test="wiki-status-repair-btn"
              @click="repairProjection"
            >修复投影</el-button>
          </div>
          <WikiReaderPanel
            :key="`runtime-${artifactId}-${artifact.current_revision}`"
            :artifact-id="artifactId"
          />
        </template>
        <template v-else>
          <!-- 统一表格视图（与设计态同形态，只读）：行键并入主键列展示口径 -->
          <div v-loading="rowsLoading" class="artifact-detail__runtime-table">
            <ArtifactTableView
              :columns="runtimeColumns"
              :rows="runtimeRows"
              :editable="false"
              :show-sources="false"
              :index-offset="(rowsPage - 1) * rowsPageSize"
              :footer-text="`${rowsTotal} 行 · ${runtimeColumns.length} 列 · 正式版只读`"
            >
              <template #empty>
                <EmptyState text="暂无已发布数据（发布后在运行态可见）" />
              </template>
            </ArtifactTableView>
          </div>
          <el-pagination
            v-if="rowsTotal > rowsPageSize"
            class="artifact-detail__pager"
            layout="total, prev, pager, next"
            :total="rowsTotal"
            :page-size="rowsPageSize"
            :current-page="rowsPage"
            @current-change="onRowsPageChange"
          />
        </template>
      </el-tab-pane>

      <!-- ── 成员：成员管理仅 owner（后端 ROLE_OWNER_ONLY）── -->
      <el-tab-pane name="members" lazy>
        <template #label><el-icon class="artifact-detail__tab-icon"><User /></el-icon>成员</template>
        <ArtifactMembersPanel
          :artifact-id="artifactId"
          :can-manage="isOwner"
          :visibility="artifact.visibility"
          @changed="reload"
        />
      </el-tab-pane>

      <!-- ── 版本：revisions 列表 + 回滚（面板内）── -->
      <el-tab-pane name="versions" lazy>
        <template #label><el-icon class="artifact-detail__tab-icon"><Timer /></el-icon>版本</template>
        <ArtifactVersionsPanel
          ref="versionsPanel"
          :artifact-id="artifactId"
          :can-write="canWrite"
          :current-revision="artifact.current_revision"
          :expected-updated-at="artifact.updated_at"
          :kind="artifact.kind"
          @changed="reloadAll"
        />
      </el-tab-pane>
    </el-tabs>

    <!-- 编辑元信息（PATCH：title/description/usage_note[/visibility owner] + 乐观锁） -->
    <el-dialog v-model="metaVisible" title="编辑元信息" width="560px">
      <el-form label-width="80px">
        <el-form-item label="标题">
          <el-input v-model="metaForm.title" maxlength="200" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="metaForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="使用说明">
          <el-input
            v-model="metaForm.usage_note"
            type="textarea"
            :rows="3"
            placeholder="给 Agent 使用者的说明——发布前必填（usage_note 门禁）"
          />
        </el-form-item>
        <!-- 008：公开/私有是 owner 专属治理决策（后端 403 兜底，前端同时收敛入口） -->
        <el-form-item v-if="isOwner" label="可见性">
          <el-radio-group v-model="metaForm.visibility" data-test="meta-visibility">
            <el-radio value="private">私有（仅成员可见）</el-radio>
            <el-radio value="public">公开（本域所有用户可读）</el-radio>
          </el-radio-group>
          <div class="artifact-detail__visibility-hint">
            公开只放大「读」——编辑/发布/成员管理仍走成员角色；Agent 侧还需在 MCP 钥匙里显式开放。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="metaVisible = false">取消</el-button>
        <el-button type="primary" :loading="metaSaving" data-test="submit-meta" @click="saveMeta">保存</el-button>
      </template>
    </el-dialog>

    <!-- 发布：diff 三段预览 + usage_note 门禁 + change_summary + 干净态禁发（Task 12） -->
    <ArtifactPublishDialog
      v-model="publishVisible"
      :artifact-id="artifactId"
      :expected-updated-at="artifact?.updated_at"
      :usage-note="artifact?.usage_note || ''"
      :draft-open="artifact?.draft_open"
      :current-revision="artifact?.current_revision ?? 0"
      :unit-label="artifact?.kind === 'wiki' ? '页' : '行'"
      :kind="artifact?.kind"
      @published="reloadAll"
      @changed="reload"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Edit, EditPen, Reading, Timer, User } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useArtifactsApi } from '@/api/artifacts'
import { useDocumentTitle } from '@/composables/useDocumentTitle'
import { apiErrorDetail } from '@/api/proxyClient'
import EmptyState from '@/components/common/EmptyState.vue'
import ArtifactImportWizard from '@/components/artifacts/ArtifactImportWizard.vue'
import ArtifactUsagePanel from '@/components/artifacts/ArtifactUsagePanel.vue'
import ArtifactTableView from '@/components/artifacts/ArtifactTableView.vue'
import ArtifactSchemaPanel from '@/components/artifacts/ArtifactSchemaPanel.vue'
import ArtifactRowsPanel from '@/components/artifacts/ArtifactRowsPanel.vue'
import ArtifactMembersPanel from '@/components/artifacts/ArtifactMembersPanel.vue'
import ArtifactVersionsPanel from '@/components/artifacts/ArtifactVersionsPanel.vue'
import ArtifactPublishDialog from '@/components/artifacts/ArtifactPublishDialog.vue'
import WikiCompilePanel from '@/components/artifacts/WikiCompilePanel.vue'
import WikiReaderPanel from '@/components/artifacts/WikiReaderPanel.vue'
import type {
  ArtifactColumn,
  ArtifactDetail,
  ArtifactKind,
  ArtifactRow,
  WikiStatus,
  WikiUnitsStatus,
} from '@/types/artifacts'

const props = defineProps<{ artifactId: string }>()

const api = useArtifactsApi()
const router = useRouter()

const artifact = ref<ArtifactDetail | null>(null)

// 浏览器标签：制品标题跟随（加载前回落「知识制品」）——站名后缀取挂载时
// document.title（brand 启动期已写入）；须在 artifact 声明后（immediate 求值）
useDocumentTitle(() => artifact.value?.title || '知识制品')
const loading = ref(false)
const loadError = ref('')
const activeTab = ref<'design' | 'runtime' | 'members' | 'versions'>('design')

// 运行态只读行（非 draft，服务端分页）+ 运行态列（发布定格快照，懒加载）
const runtimeRows = ref<ArtifactRow[]>([])
const runtimeColumns = ref<ArtifactColumn[]>([])
const rowsTotal = ref(0)
const rowsPage = ref(1)
const rowsPageSize = 20
const rowsLoading = ref(false)
const runtimeLoaded = ref(false)

// 版本面板（发布后命令式刷新 revisions）+ 行面板（跨面板变更后刷新行）
const versionsPanel = ref<InstanceType<typeof ArtifactVersionsPanel> | null>(null)
const rowsPanel = ref<InstanceType<typeof ArtifactRowsPanel> | null>(null)

// 元信息编辑
const metaVisible = ref(false)
const metaSaving = ref(false)
const metaForm = ref({ title: '', description: '', usage_note: '', visibility: 'private' })

// 发布
const publishVisible = ref(false)

// ── Q01 wiki 检索状态条 + 投影修复（66号批次5 Task 5.3）──

const wikiStatus = ref<WikiStatus | null>(null)
const wikiRepairing = ref(false)

const EMPTY_UNITS: WikiUnitsStatus = { total: 0, done: 0, failed: 0, pending: 0 }
const wikiUnits = computed<WikiUnitsStatus>(() => wikiStatus.value?.units ?? EMPTY_UNITS)

/** done/total 百分比（total=0 无意义——投影缺失走警示文案）。 */
const wikiStatusPercent = computed(() => {
  const u = wikiUnits.value
  return u.total > 0 ? Math.round((u.done / u.total) * 100) : 0
})

const wikiStatusText = computed(() => {
  const u = wikiUnits.value
  if (u.total === 0) {
    return '检索投影缺失（0 个切片）——发布后置投影未落地，可修复重建'
  }
  if (u.failed > 0) {
    return `切片向量 ${u.done}/${u.total}（${wikiStatusPercent.value}%），`
      + `${u.failed} 个失败——向量检索可能不完整`
  }
  if (u.pending > 0) {
    return `向量处理中 ${u.done}/${u.total}（${wikiStatusPercent.value}%）`
  }
  return `检索就绪 ${u.done}/${u.total}（100%）`
})

/** 状态条是增强信息：拉取失败静默隐藏（不扰运行态主内容）。 */
async function loadWikiStatus(): Promise<void> {
  try {
    wikiStatus.value = await api.getWikiStatus(props.artifactId)
  } catch {
    wikiStatus.value = null
  }
}

/** 修复投影（editor+）：幂等重入——健康 no-op 后端自答；完成后刷新状态条。 */
async function repairProjection(): Promise<void> {
  wikiRepairing.value = true
  try {
    const result = await api.reprojectWiki(props.artifactId)
    ElMessage.success(result.reprojected ? '投影已重建，向量处理中' : '投影状态健康，无需修复')
    await loadWikiStatus()
  } catch (error) {
    ElMessage.error(`修复失败：${await apiErrorDetail(error)}`)
  } finally {
    wikiRepairing.value = false
  }
}

// 制品（重新）加载后：wiki 已发布 → 拉检索状态；否则隐藏（rev0 无投影语义）
watch(() => artifact.value, (a) => {
  if (a?.kind === 'wiki' && a.current_revision > 0) void loadWikiStatus()
  else wikiStatus.value = null
})

/** canWrite = my_role ∈ {owner, editor}（详情响应附带；admin 后端归一 owner）。 */
const canWrite = computed(() => artifact.value?.my_role === 'owner' || artifact.value?.my_role === 'editor')
/** 成员管理仅 owner（后端 ROLE_OWNER_ONLY）。 */
const isOwner = computed(() => artifact.value?.my_role === 'owner')
/** 干净态 = draft 关闭且已有发布版 → 无待发布变更（后端 409 门禁镜像）。 */
const hasPendingChanges = computed(() =>
  !!artifact.value && (artifact.value.draft_open || artifact.value.current_revision === 0))
/** D01（66号 §5）：服务版本表述——有发布版且未停用时点明「rN 正在服务」；
 *  停用后 Agent 侧不可见，不再宣称「正在服务」（rev0 无发布版，返回 null）。 */
const servingRevisionLabel = computed(() => {
  const a = artifact.value
  if (!a || a.current_revision === 0 || a.status === 'deprecated') return null
  return `r${a.current_revision} 正在服务`
})

// ── L01 空制品引导（66号 §4）──────────────────────────────────────────

/** 引导文案（Record 键钉死 ArtifactKind 全集——四形态路线下新增 kind 时
 *  TS 强制补齐文案，与列表 KIND_CREATE_HINTS 同风格）。 */
const EMPTY_GUIDES: Record<ArtifactKind, { title: string; description: string }> = {
  wiki: {
    title: '空 Wiki——从知识库生成第一版内容',
    description: '在下方「来源绑定」选择可访问的知识库目录并开始编译，生成页面后可人工修订，填写使用说明再发布。',
  },
  table: {
    title: '空表格——上传 Excel 或编辑字段',
    description: '在下方上传 Excel 走导入向导（列映射 → 预览确认 → 导入草稿），或先在「列定义」手工建列再录数据。',
  },
}

/** 空制品判定：从未发布（rev0）且无未发布改动（draft_open=false——导入/
 *  编译完成/建列/建页都会检出草稿，首次实质操作后引导自然消失）；表格
 *  再要求无列定义。viewer 无操作入口，不显示操作引导。 */
const emptyGuide = computed(() => {
  const a = artifact.value
  if (!a || !canWrite.value || a.current_revision > 0 || a.draft_open) return null
  if (a.kind !== 'wiki' && a.schema.length > 0) return null
  return EMPTY_GUIDES[a.kind]
})

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    artifact.value = await api.get(props.artifactId)
    // 008 UI：viewer（含公开读者）默认落在运行态——设计态对非写者只是空提示
    if (!loadingFirstTabDone.value && !canWrite.value) activeTab.value = 'runtime'
    loadingFirstTabDone.value = true
  } catch (error) {
    artifact.value = null
    loadError.value = `加载失败：${await apiErrorDetail(error)}`
  } finally {
    loading.value = false
  }
}

const loadingFirstTabDone = ref(false)

/**
 * 统一刷新（codex二审功能项②）：详情（乐观锁令牌+设计态 schema）+ 运行态
 * schema/行数据 + 行编辑面板 + 版本面板。导入/列变更/回滚/discard/发布/
 * 停用/恢复后一律走这里——原先分散的局部刷新是「回滚后部分缓存不刷新」的根因。
 */
async function reloadAll() {
  await reload()
  if (runtimeLoaded.value) {
    await Promise.all([loadRuntimeSchema(), loadRuntimeRows()])
  }
  rowsPanel.value?.loadRows?.()
  versionsPanel.value?.loadRevisions?.()
}

/**
 * 运行态列 = 发布定格快照（GET /table/columns?runtime=true 读
 * revisions.schema_json）——设计态草稿新列不再漏进运行态表头（空列）。
 */
async function loadRuntimeSchema() {
  try {
    runtimeColumns.value = await api.listColumns(
      props.artifactId, { runtime: true })
  } catch (error) {
    runtimeColumns.value = []
    ElMessage.error(`加载运行态列定义失败：${await apiErrorDetail(error)}`)
  }
}

async function loadRuntimeRows() {
  rowsLoading.value = true
  try {
    const page = await api.listRows(props.artifactId, {
      page: rowsPage.value, size: rowsPageSize,
    })
    runtimeRows.value = page.rows
    rowsTotal.value = page.total
  } catch (error) {
    ElMessage.error(`加载运行态数据失败：${await apiErrorDetail(error)}`)
  } finally {
    rowsLoading.value = false
  }
}

/** 运行态 tab 首次切入：schema + 行一起拉（懒加载；成员/版本面板走 lazy 自加载）。 */
function loadRuntimeData() {
  runtimeLoaded.value = true
  void Promise.all([loadRuntimeSchema(), loadRuntimeRows()])
}

watch(activeTab, (tab) => {
  // wiki 守卫保留：无表格运行态，不触发 table 列/行懒加载（阅读器面板自加载）
  if (tab === 'runtime' && !runtimeLoaded.value && artifact.value?.kind !== 'wiki') {
    loadRuntimeData()
  }
})

function onRowsPageChange(page: number) {
  rowsPage.value = page
  loadRuntimeRows()
}

function openMetaEdit() {
  if (!artifact.value) return
  metaForm.value = {
    title: artifact.value.title,
    description: artifact.value.description || '',
    usage_note: artifact.value.usage_note || '',
    visibility: (artifact.value.visibility as string) || 'private',
  }
  metaVisible.value = true
}

async function saveMeta() {
  if (!artifact.value) return
  const title = metaForm.value.title.trim()
  if (!title) {
    ElMessage.warning('标题不能为空')
    return
  }
  metaSaving.value = true
  try {
    const updated = await api.updateMeta(props.artifactId, {
      title,
      description: metaForm.value.description.trim(),
      usage_note: metaForm.value.usage_note.trim(),
      // 后端对非 owner 改 visibility 一律 403——前端只在 owner 表单露出该字段
      ...(isOwner.value ? { visibility: metaForm.value.visibility } : {}),
      expected_updated_at: artifact.value.updated_at,
    })
    artifact.value = { ...artifact.value, ...updated }
    metaVisible.value = false
    ElMessage.success('元信息已保存')
  } catch (error) {
    ElMessage.error(`保存失败：${await apiErrorDetail(error)}`)
  } finally {
    metaSaving.value = false
  }
}

/** 删除（008 两级确认第二级）：仅已停用制品可删；输入标题确认后级联硬删。 */
async function deleteArtifact() {
  if (!artifact.value) return
  const title = artifact.value.title
  let typed: string
  try {
    const result = await ElMessageBox.prompt(
      `此操作将**级联硬删**制品「${title}」的全部行数据、列定义、版本快照与成员，不可恢复。\n请输入制品标题确认：`,
      '删除知识制品',
      { type: 'warning', confirmButtonText: '永久删除', confirmButtonClass: 'el-button--danger' },
    )
    typed = String(result.value ?? '').trim()
  } catch {
    return
  }
  if (typed !== title) {
    ElMessage.warning('输入的标题不一致，已取消删除')
    return
  }
  try {
    await api.remove(props.artifactId)
    ElMessage.success('制品已删除')
    await router.push('/artifacts')
  } catch (error) {
    const resp = (error as { response?: { status?: number; data?: { detail?: { code?: string } } } })?.response
    if (resp?.status === 409 && resp.data?.detail?.code === 'artifact_not_deprecated') {
      ElMessage.error('必须先停用制品才能删除——已为你刷新状态')
      await reloadAll()
    } else {
      ElMessage.error(`删除失败：${await apiErrorDetail(error)}`)
    }
  }
}

/** 简单生命周期动作（停用/恢复/丢弃）：确认 → POST → 统一刷新（reloadAll）。 */
async function lifecycleAction(fn: () => Promise<unknown>, confirmText: string) {
  try {
    await ElMessageBox.confirm(confirmText, { type: 'warning' })
  } catch {
    return
  }
  try {
    await fn()
    ElMessage.success('操作成功')
    await reloadAll()
  } catch (error) {
    ElMessage.error(await apiErrorDetail(error))
  }
}

function deprecateArtifact() {
  if (!artifact.value) return
  void lifecycleAction(
    () => api.deprecate(props.artifactId, artifact.value?.updated_at),
    '停用后 Agent 侧不可见（deprecated），可随时恢复。确认停用？',
  )
}

function republishArtifact() {
  if (!artifact.value) return
  void lifecycleAction(
    () => api.republish(props.artifactId, artifact.value?.updated_at),
    '恢复发布后 Agent 侧重新可见。确认恢复？',
  )
}

function discardDraftAction() {
  if (!artifact.value) return
  void lifecycleAction(
    () => api.discardDraft(props.artifactId, artifact.value?.updated_at),
    '丢弃 draft 将放弃所有未发布的修改（回到当前已发布版本）。确认丢弃？',
  )
}

function statusTagType(status: string): 'info' | 'success' | 'danger' {
  if (status === 'published') return 'success'
  if (status === 'deprecated') return 'danger'
  return 'info'
}

function statusLabel(status: string): string {
  if (status === 'published') return '已发布'
  if (status === 'deprecated') return '已停用'
  return '草稿'
}


onMounted(reload)

defineExpose({ reloadAll })
</script>

<style scoped>
.artifact-detail__tabs :deep(.el-tabs__item) { display: inline-flex; align-items: center; gap: 5px; }
.artifact-detail__tab-icon { font-size: 14px; }
.artifact-detail__header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.artifact-detail__head-left { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.artifact-back { display: inline-flex; align-items: center; gap: 4px; color: var(--kb-text-tertiary); text-decoration: none; font-size: 12px; }
.artifact-back:hover { color: var(--kb-accent, #0891b2); }
/* ── 标题行：kindicon + 大标题（高保真 ar-title 排版） ── */
.artifact-detail__title-row { display: flex; align-items: center; gap: 12px; min-width: 0; }
.artifact-detail__kindicon {
  display: inline-grid; place-items: center; flex-shrink: 0;
  width: 40px; height: 40px; border-radius: 10px;
  border: 1px solid var(--kb-border, #e2e8f0);
  background: var(--kb-accent-soft, rgba(8, 145, 178, 0.08));
  color: var(--kb-accent, #0891b2);
  font-size: 15px; font-weight: 700;
}
.artifact-detail__title { margin: 0; font-size: 21px; font-weight: 600; letter-spacing: -.4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.artifact-detail__badges { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding-left: 52px; }
.artifact-detail__desc { margin: 8px 0 12px; color: var(--kb-text-tertiary); font-size: 13px; }
.artifact-detail__missing { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.artifact-detail__meta-bar { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
/* 说明卡占位（desc-card 名字保留——class 传给子组件根元素继承 flex 占位） */
.artifact-detail__desc-card { flex: 1; max-width: 720px; }
.artifact-detail__visibility-hint { width: 100%; color: var(--kb-text-tertiary); font-size: 12px; line-height: 1.5; }
.artifact-detail__empty-guide { margin-bottom: 14px; }
/* 内容区切换与检索记录页保持一致。 */
.artifact-detail__wiki-views {
  display: flex;
  gap: 4px;
  margin: 14px 0 10px;
  border-bottom: 1px solid var(--kb-border-light);
}
.artifact-detail__wiki-views button {
  padding: 9px 16px;
  border: 0;
  border-bottom: 2px solid transparent;
  color: var(--kb-text-secondary);
  background: transparent;
  cursor: pointer;
}
.artifact-detail__wiki-views button.active {
  color: var(--kb-accent);
  border-bottom-color: var(--kb-accent);
  font-weight: 600;
}
.artifact-detail__wiki-views button:focus-visible {
  outline: 2px solid var(--kb-accent);
  outline-offset: -2px;
}
/* Q01 检索状态条（66号批次5）：运行态 wiki 顶部一行；警示=黄边淡底 */
.artifact-detail__search-status {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 10px; padding: 6px 10px; border-radius: 6px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.artifact-detail__search-status.is-warning {
  border-color: var(--el-color-warning-light-5, #f3d19e);
  background: var(--el-color-warning-light-9, #fdf6ec);
}
.artifact-detail__search-status-text { font-size: 12.5px; color: var(--kb-text-secondary, #606266); }
.artifact-detail__pager { margin-top: 12px; justify-content: flex-end; }
</style>

<style>
/**
 * 知识制品详情页 EP 组件观感统一层（非 scoped——作用于本页子树的所有
 * Element Plus 组件：行/字段/成员/版本/说明卡/导入向导/发布弹窗）。
 * 设计语言=docs/高保真 artifacts-1115：软表头、发丝线、soft 徽章、
 * 圆角弹窗。只调观感，不动组件行为（分页/展开/校验全保留）。
 */
.artifact-detail .el-table th.el-table__cell {
  background: var(--kb-bg-card-hover, #f8fafc);
  color: var(--kb-text-secondary, #64748b);
  font-weight: 500;
  font-size: 12px;
}
.artifact-detail .el-table td.el-table__cell { font-size: 12.5px; }
.artifact-detail .el-tag {
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}
.artifact-detail .el-tag--plain { background: transparent; }
.artifact-detail .el-dialog {
  border-radius: 12px;
}
.artifact-detail .el-dialog__title { font-weight: 600; font-size: 15px; }
.artifact-detail .el-descriptions__label {
  color: var(--kb-text-tertiary, #94a3b8);
  font-size: 12px;
  width: 92px;
}
.artifact-detail .el-descriptions__body { font-size: 12.5px; }
.artifact-detail__sec-head {
  display: flex; align-items: baseline; gap: 10px;
  margin: 22px 0 10px;
}
.artifact-detail__sec-head:first-child { margin-top: 4px; }
.artifact-detail__sec-title {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 14.5px; font-weight: 600;
  color: var(--kb-text-primary, inherit);
}
.artifact-detail__sec-title::before {
  content: '';
  width: 3px; height: 14px; border-radius: 2px;
  background: var(--kb-accent, #0891b2);
}
.artifact-detail__sec-sub { font-size: 12px; color: var(--kb-text-tertiary, #94a3b8); }
</style>
