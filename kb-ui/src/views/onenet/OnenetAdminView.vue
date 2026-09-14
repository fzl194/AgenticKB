<template>
  <div class="onenet-admin">
    <el-alert v-if="notConfigured" type="warning" :closable="false"
              title="知识一张网未配置（onenet.yaml / 环境变量），功能不可用" />

    <el-card class="onenet-admin__step" shadow="never">
      <template #header>
        <b>① 查询产品文档</b>
        <span class="onenet-admin__hint">（三元组条件可累加，AND 组合；命中封顶 10000，只作发现手段）</span>
      </template>
      <div v-for="(cond, i) in conditions" :key="i" class="onenet-admin__cond">
        <el-select v-model="cond.field" style="width: 210px" @change="onFieldChange(cond)">
          <el-option v-for="[f, cn] in FIELD_OPTIONS" :key="f" :value="f" :label="`${cn} ${f}`" />
        </el-select>
        <el-select v-model="cond.fuzzy" style="width: 90px" :disabled="cond.field === 'part_id'">
          <el-option :value="false" label="精确" />
          <el-option :value="true" label="模糊" />
        </el-select>
        <el-input v-model="cond.content" placeholder="匹配内容" style="flex: 1"
                  @keyup.enter="doSearch(1)" />
        <el-button size="small" @click="conditions.splice(i, 1)">删除</el-button>
      </div>
      <div class="onenet-admin__actions">
        <el-button @click="addCondition">+ 添加条件</el-button>
        <el-button type="primary" :loading="searching" @click="doSearch(1)">搜 索</el-button>
      </div>

      <template v-if="hits.length || searchMeta">
        <el-table v-if="hits.length" :data="hits" size="small" highlight-current-row
                  @current-change="onPickHit">
          <el-table-column prop="doc_name" label="文档名" min-width="240" show-overflow-tooltip />
          <el-table-column prop="source_id" label="source_id" width="150" />
          <el-table-column label="产品线" width="130" show-overflow-tooltip>
            <template #default="{ row }">{{ (row.product_line ?? []).join(' / ') || '-' }}</template>
          </el-table-column>
          <el-table-column prop="parsed_version" label="版本" width="100" />
          <el-table-column prop="publish_time" label="发布" width="105" />
          <el-table-column label="命中" width="75">
            <template #default="{ row }">{{ row.slice_hits }} 片</template>
          </el-table-column>
          <el-table-column label="命中章节样例" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ row.sample_titles.join('；') || '-' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="110">
            <template #default="{ row }">
              <el-button size="small" @click.stop="onPickHit(row)">摸底并选章</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="searchMeta" class="onenet-admin__notice"
             :class="{ 'onenet-admin__warn': searchResult?.capped }">{{ searchMeta }}</div>
        <el-pagination v-if="searchResult && searchResult.total_documents > pageSize"
                      layout="prev, pager, next, total, sizes" :total="searchResult.total_documents"
                      v-model:current-page="page" v-model:page-size="pageSize"
                      :page-sizes="[20, 50, 100]" style="margin-top: 8px"
                      @current-change="doSearch()" @size-change="doSearch(1)" />
      </template>
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
        <el-button type="primary" :loading="tocLoading" @click="loadToc(Boolean(toc))">
          {{ toc ? '刷新章节树' : '进入文档（轻量扫描）' }}
        </el-button>
      </div>
    </el-card>

    <el-card v-if="toc" class="onenet-admin__step" shadow="never">
      <template #header>
        <b>③ 勾选章节</b>
        <span class="onenet-admin__hint">
          （{{ toc.nodes }} 节点 · β 预计 <b>{{ toc.file_count }}</b> 文件 ·
          规则 {{ toc.rule_version }}{{ toc.cached ? ' · 已用缓存' : '' }}；
          不勾选任何节点 = 整包导入）
        </span>
      </template>
      <el-row :gutter="14">
        <el-col :span="12">
          <div class="onenet-admin__treewrap">
            <!-- 大文档 5000+ 节点：默认只展开根层、层层点开（V1.2 原型教训） -->
            <el-tree ref="tocTreeRef" :data="toc.tree" node-key="path" show-checkbox
                     :props="{ label: 'title', children: 'children' }"
                     :default-expanded-keys="rootKeys">
              <template #default="{ data }">
                <span class="onenet-admin__node">
                  {{ data.title }}
                  <el-tag size="small" type="info" effect="plain">{{ data.slice_count }} 片</el-tag>
                </span>
              </template>
            </el-tree>
          </div>
        </el-col>
        <el-col :span="12">
          <div class="onenet-admin__treewrap">
            <el-table :data="toc.files ?? []" size="small" height="100%">
              <el-table-column prop="file_title" label="β 文件（导入单位）" min-width="150" show-overflow-tooltip />
              <el-table-column prop="folder_path" label="目录" min-width="130" show-overflow-tooltip />
              <el-table-column prop="slice_count" label="切片" width="60" />
              <el-table-column label="part 范围" width="110">
                <template #default="{ row }">{{ row.part_min }}~{{ row.part_max }}</template>
              </el-table-column>
            </el-table>
          </div>
        </el-col>
      </el-row>
      <div class="onenet-admin__actions">
        <el-button type="primary" :loading="starting" @click="startImport">确认导入（勾选子树过滤，不勾=整包）</el-button>
      </div>
    </el-card>

    <el-card class="onenet-admin__step" shadow="never">
      <template #header><b>④ 导入记录</b></template>
      <el-table :data="imports" size="small" v-loading="importsLoading">
        <el-table-column prop="source_id" label="source_id" width="150" />
        <el-table-column prop="doc_name" label="文档" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="document_count" label="文档数" width="75" />
        <el-table-column prop="total_slices" label="切片数" width="90" />
        <el-table-column prop="parsed_version_seen" label="版本" width="100" />
        <el-table-column prop="updated_at" label="更新时间" width="165" show-overflow-tooltip />
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :loading="resyncingId === row.id" :disabled="!canResync(row)"
                       @click="doResync(row)">重同步</el-button>
            <el-button v-if="row.status === 'failed'" size="small" type="warning" plain
                       :loading="retryingId === row.id" @click="doRetry(row)">重试</el-button>
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
import type {
  OnenetCondition, OnenetImport, OnenetImportStatus, OnenetProbe,
  OnenetSearchResult, OnenetToc,
} from '@/api/onenet'

const domainStore = useDomainStore()
const api = useOnenetApi()

/** 字段白名单（后端 SEARCH_FIELDS 同款，顺序一致）+ 中文标签 */
const FIELD_OPTIONS: Array<[string, string]> = [
  ['source_id', '文档ID'], ['nid', '切片ID'], ['url', '资源链接'], ['title', '标题'],
  ['path', '章节目录'], ['content', '切片内容'], ['source_site', '来源站点'],
  ['file_name', '文件名'], ['category_path', '定义标签'], ['doc_name', '文档名称'],
  ['doc_type', '文档类型'], ['part_id', '文档顺序'],
]

// V1.2 默认预置：来源站点 support（产品文档主阵地，可删）+ 文档名称精确（主路径）
const conditions = ref<OnenetCondition[]>([
  { field: 'source_site', fuzzy: false, content: 'support' },
  { field: 'doc_name', fuzzy: false, content: '' },
])
const hits = ref<OnenetSearchResult['documents']>([])
const searchResult = ref<OnenetSearchResult | null>(null)
const searching = ref(false)
const notConfigured = ref(false)
const page = ref(1)
const pageSize = ref(20)

const probe = ref<OnenetProbe | null>(null)
const toc = ref<OnenetToc | null>(null)
const tocLoading = ref(false)
const tocTreeRef = ref<InstanceType<typeof ElTree> | null>(null)
const starting = ref(false)

const imports = ref<OnenetImport[]>([])
const importsLoading = ref(false)
const detail = ref<OnenetImport | null>(null)
const resyncingId = ref('')
const retryingId = ref('')

const searchMeta = computed(() => {
  const r = searchResult.value
  if (!r) return ''
  const cap = r.capped ? '⚠ 命中切片 ≥10000 已封顶，仅基于前 ' + r.slices_pulled + ' 条汇总；' : ''
  return cap + `命中切片 ${r.slice_total_reported ?? '-'} · 拉取 ${r.slices_pulled} · 去重文档 ${r.total_documents} 篇（按 source_id 汇总）`
})

/** 目录树默认只展开根层（懒展开，防 5000+ 节点卡死） */
const rootKeys = computed(() => (toc.value?.tree ?? []).map((n) => n.path))

function addCondition() {
  conditions.value.push({ field: 'doc_name', fuzzy: false, content: '' })
}
function onFieldChange(cond: OnenetCondition) {
  if (cond.field === 'part_id') cond.fuzzy = false
}

async function doSearch(target?: number) {
  if (target) page.value = target
  const conds = conditions.value.filter((c) => c.content.trim())
  if (!conds.length) {
    ElMessage.warning('至少填一条条件')
    return
  }
  searching.value = true
  try {
    searchResult.value = await api.search(conds, page.value, pageSize.value)
    hits.value = searchResult.value.documents
    if (!hits.value.length) ElMessage.info('无命中（注意 fuzzy 封顶 10000，可换更精确的词）')
  } catch (e: unknown) {
    handleError(e)
  } finally {
    searching.value = false
  }
}

function onPickHit(row: { source_id: string } | null) {
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

async function loadToc(refresh = false) {
  if (!probe.value) return
  tocLoading.value = true
  try {
    toc.value = await api.toc(domainStore.currentDomain, probe.value.source_id, { refresh })
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

async function doRetry(row: OnenetImport) {
  retryingId.value = row.id
  try {
    await api.retryImport(row.id)
    ElMessage.success('已重新入队（幂等重跑，不产生重复）')
    await reloadImports()
  } catch (e) {
    handleError(e)
  } finally {
    retryingId.value = ''
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
  const resp = (e as { response?: { status?: number; data?: unknown } })?.response
  const status = resp?.status
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
.onenet-admin__cond { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
.onenet-admin__hint { color: var(--el-text-color-secondary); font-size: 12px; font-weight: normal; }
.onenet-admin__notice { margin-top: 8px; color: var(--el-text-color-secondary); font-size: 12px; }
.onenet-admin__warn { color: var(--el-color-warning); }
.onenet-admin__actions { margin-top: 10px; display: flex; align-items: center; gap: 10px; }
.onenet-admin__detail { margin-top: 10px; }
.onenet-admin__treewrap { height: 420px; overflow: auto; border: 1px solid var(--el-border-color-lighter);
                          border-radius: 6px; padding: 8px; }
.onenet-admin__node { display: inline-flex; align-items: center; gap: 6px; }
</style>
