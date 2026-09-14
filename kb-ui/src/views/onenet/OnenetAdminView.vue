<template>
  <div class="onenet-admin">
    <el-alert v-if="notConfigured" type="warning" :closable="false"
              title="知识一张网未配置（onenet.yaml / 环境变量），功能不可用" />

    <el-card class="onenet-admin__step" shadow="never">
      <template #header><b>① 查询产品文档</b>（fuzzy 命中封顶 10000，结果可能不全）</template>
      <el-form inline @submit.prevent>
        <el-form-item label="文档名"><el-input v-model="filters.doc_name" placeholder="如 UDG" clearable style="width: 220px" /></el-form-item>
        <el-form-item label="文件名"><el-input v-model="filters.file_name" placeholder="如 产品文档" clearable style="width: 220px" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="filters.doc_type" clearable style="width: 140px" placeholder="全部">
            <el-option v-for="t in ['hwics', 'pdf', 'docx', 'chm', 'html']" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item><el-button type="primary" :loading="searching" :disabled="!hasFilter" @click="doSearch">查询</el-button></el-form-item>
      </el-form>

      <el-table v-if="hits.length" :data="hits" size="small" highlight-current-row @current-change="onPickHit">
        <el-table-column prop="doc_name" label="文档名" min-width="260" show-overflow-tooltip />
        <el-table-column prop="source_id" label="source_id" width="160" />
        <el-table-column prop="parsed_version" label="版本" width="110" />
        <el-table-column prop="publish_time" label="发布" width="110" />
        <el-table-column label="命中" width="80">
          <template #default="{ row }">{{ row.slice_hits }} 片</template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" @click.stop="onPickHit(row)">摸底并选章</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="searchNotice" class="onenet-admin__notice">{{ searchNotice }}</div>
    </el-card>

    <el-card v-if="probe" class="onenet-admin__step" shadow="never">
      <template #header><b>② 摸底</b></template>
      <el-descriptions :column="3" size="small" border>
        <el-descriptions-item label="文档">{{ probe.doc_name ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="source_id">{{ probe.source_id }}</el-descriptions-item>
        <el-descriptions-item label="切片总数">{{ probe.total_slices }}</el-descriptions-item>
        <el-descriptions-item label="part 范围">{{ probe.part_id.min }} ~ {{ probe.part_id.max }}</el-descriptions-item>
        <el-descriptions-item label="解析版本">{{ probe.parsed_version ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="产品线">{{ (probe.product_line ?? []).join(' / ') || '-' }}</el-descriptions-item>
      </el-descriptions>
      <div class="onenet-admin__actions">
        <el-button type="primary" :loading="tocLoading" @click="loadToc()">
          {{ toc ? '刷新章节树' : '加载章节树（轻量扫描）' }}
        </el-button>
      </div>
    </el-card>

    <el-card v-if="toc" class="onenet-admin__step" shadow="never">
      <template #header>
        <b>③ 勾选章节</b>
        <span class="onenet-admin__hint">（不勾选任何节点 = 整包导入；勾选后按节点子树过滤）</span>
      </template>
      <el-tree ref="tocTreeRef" :data="toc.tree" node-key="path" show-checkbox
               :props="{ label: 'title', children: 'children' }" default-expand-all>
        <template #default="{ data }">
          <span>{{ data.title }} <el-tag size="small" type="info">{{ data.slice_count }} 片</el-tag></span>
        </template>
      </el-tree>
      <div class="onenet-admin__actions">
        <el-button type="primary" :loading="starting" @click="startImport">确认导入（逐文档）</el-button>
        <span v-if="toc.cached" class="onenet-admin__hint">已用缓存章节树</span>
      </div>
    </el-card>

    <el-card class="onenet-admin__step" shadow="never">
      <template #header><b>④ 导入记录</b></template>
      <el-table :data="imports" size="small" v-loading="importsLoading">
        <el-table-column prop="source_id" label="source_id" width="150" />
        <el-table-column prop="doc_name" label="文档" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="document_count" label="文档数" width="80" />
        <el-table-column prop="total_slices" label="切片数" width="90" />
        <el-table-column prop="parsed_version_seen" label="版本" width="100" />
        <el-table-column prop="updated_at" label="更新时间" width="170" show-overflow-tooltip />
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :loading="resyncingId === row.id" :disabled="!canResync(row)"
                       @click="doResync(row)">重同步</el-button>
            <el-button size="small" @click="loadImportDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="detail" class="onenet-admin__detail">
        <b>{{ detail.source_id }} 产物文档（{{ detail.documents?.length ?? 0 }}）：</b>
        <el-table :data="detail.documents ?? []" size="small" max-height="260">
          <el-table-column prop="document_name" label="文件" min-width="240" show-overflow-tooltip />
          <el-table-column prop="directory_path" label="目录" min-width="180" show-overflow-tooltip />
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { ElTree } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useOnenetApi } from '@/api/onenet'
import type { OnenetDocHit, OnenetImport, OnenetImportStatus, OnenetProbe, OnenetToc } from '@/api/onenet'

const domainStore = useDomainStore()
const api = useOnenetApi()

const filters = ref<Partial<Record<'doc_name' | 'file_name' | 'doc_type' | 'language', string>>>({})
const hits = ref<OnenetDocHit[]>([])
const searchNotice = ref('')
const searching = ref(false)
const notConfigured = ref(false)

const probe = ref<OnenetProbe | null>(null)
const toc = ref<OnenetToc | null>(null)
const tocLoading = ref(false)
const tocTreeRef = ref<InstanceType<typeof ElTree> | null>(null)
const starting = ref(false)

const imports = ref<OnenetImport[]>([])
const importsLoading = ref(false)
const detail = ref<OnenetImport | null>(null)
const resyncingId = ref('')

const hasFilter = computed(() => Object.values(filters.value).some((v) => v && String(v).trim()))

async function doSearch() {
  searching.value = true
  searchNotice.value = ''
  try {
    const out = await api.search(filters.value)
    hits.value = out.documents
    searchNotice.value = out.capped ? '⚠ ' + out.notice : out.notice
    if (!out.documents.length) ElMessage.info('无命中（注意 fuzzy 封顶 10000，可换更精确的关键词）')
  } catch (e: unknown) {
    handleError(e)
  } finally {
    searching.value = false
  }
}

function onPickHit(row: OnenetDocHit | null) {
  if (!row) return
  probe.value = null
  toc.value = null
  void (async () => {
    try {
      probe.value = await api.probe(row.source_id)
    } catch (e) {
      handleError(e)
    }
  })()
}

async function loadToc() {
  if (!probe.value) return
  tocLoading.value = true
  try {
    toc.value = await api.toc(domainStore.currentDomain, probe.value.source_id)
  } catch (e) {
    handleError(e)
  } finally {
    tocLoading.value = false
  }
}

async function startImport() {
  if (!probe.value) return
  const checked = (tocTreeRef.value?.getCheckedNodes(false, true) ?? []) as Array<{ path?: string }>
  const subtrees = checked.map((n) => n.path).filter((p): p is string => Boolean(p))
  starting.value = true
  try {
    await api.startImport({
      domain: domainStore.currentDomain,
      source_id: probe.value.source_id,
      selection: subtrees.length ? { subtrees } : {},
      doc_name: probe.value.doc_name ?? undefined,
      parsed_version: probe.value.parsed_version ?? undefined,
      total_slices: probe.value.total_slices,
    })
    ElMessage.success('导入任务已创建（后台拉取+挖掘，完成后在记录中查看）')
    await reloadImports()
  } catch (e) {
    handleError(e)
  } finally {
    starting.value = false
  }
}

async function reloadImports() {
  importsLoading.value = true
  try {
    imports.value = await api.listImports(domainStore.currentDomain)
  } catch (e) {
    handleError(e)
  } finally {
    importsLoading.value = false
  }
}

async function loadImportDetail(row: OnenetImport) {
  try {
    detail.value = await api.getImport(row.id)
  } catch (e) {
    handleError(e)
  }
}

async function doResync(row: OnenetImport) {
  resyncingId.value = row.id
  try {
    const out = await api.resync(row.id)
    if (!out.changed) {
      ElMessage.info('上游无变化（版本/总数/范围三信号一致）')
    } else {
      ElMessage.success(
        `已同步：${out.diff?.added.length ?? 0} 新增 / ${out.diff?.changed.length ?? 0} 变更 / `
        + `${out.diff?.removed.length ?? 0} 消失；更新 ${out.updated_documents.length} 篇，`
        + `下线 ${out.removed_documents.length} 篇`,
      )
    }
    await reloadImports()
  } catch (e) {
    handleError(e)
  } finally {
    resyncingId.value = ''
  }
}

function canResync(row: OnenetImport): boolean {
  return row.status === 'done' || row.status === 'failed'
}

const STATUS_LABELS: Record<OnenetImportStatus, string> = {
  queued: '排队中', fetching: '拉取中', restoring: '还原中',
  importing: '落库中', mining: '挖掘中', done: '完成', failed: '失败',
}

function statusLabel(s: OnenetImportStatus): string {
  return STATUS_LABELS[s] ?? s
}

function statusType(s: OnenetImportStatus): 'success' | 'danger' | 'warning' | 'info' {
  if (s === 'done') return 'success'
  if (s === 'failed') return 'danger'
  if (s === 'queued') return 'info'
  return 'warning'
}

function handleError(e: unknown) {
  const detail = (e as { response?: { status?: number; data?: unknown } })?.response
  const status = detail?.status
  if (status === 503) {
    notConfigured.value = true
    ElMessage.error('知识一张网未配置')
    return
  }
  const msg = (e as Error)?.message ?? String(e)
  ElMessage.error(status ? `${status}: ${msg}` : msg)
}

onMounted(reloadImports)
</script>

<style scoped>
.onenet-admin { display: flex; flex-direction: column; gap: 12px; }
.onenet-admin__step :deep(.el-card__header) { padding: 10px 16px; }
.onenet-admin__notice { margin-top: 8px; color: var(--el-text-color-secondary); font-size: 12px; }
.onenet-admin__hint { color: var(--el-text-color-secondary); font-size: 12px; font-weight: normal; }
.onenet-admin__actions { margin-top: 10px; display: flex; align-items: center; gap: 10px; }
.onenet-admin__detail { margin-top: 10px; }
</style>
