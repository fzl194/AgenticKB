<template>
  <div class="fm" @click="closeCtx" @contextmenu="onVoidContext">
    <!-- Toolbar -->
    <div class="fm__toolbar">
      <el-breadcrumb separator="/" class="fm__crumb">
        <el-breadcrumb-item>
          <span class="fm__crumb-root" @click="navTo(null)">根目录</span>
        </el-breadcrumb-item>
        <el-breadcrumb-item v-for="seg in breadcrumb" :key="seg.id">
          <span class="fm__crumb-seg" @click="navTo(seg.id)">{{ seg.name }}</span>
        </el-breadcrumb-item>
      </el-breadcrumb>
      <div class="fm__actions">
        <el-button v-if="canWrite" size="small" @click="newFolder">
          <el-icon class="el-icon--left"><FolderAdd /></el-icon>新建文件夹
        </el-button>
        <el-upload
          v-if="canWrite"
          :show-file-list="false"
          :multiple="true"
          :auto-upload="true"
          :http-request="handleUpload"
          :disabled="uploading > 0"
          accept=".md,.markdown,.txt,.html,.htm,.pdf,.doc,.docx,.xls,.xlsx,.zip,.chm,.hdx"
        >
          <el-button size="small" type="primary" :loading="uploading > 0">
            <el-icon class="el-icon--left"><UploadFilled /></el-icon>上传到当前文件夹
          </el-button>
        </el-upload>
        <el-button text size="small" :loading="loading" @click.stop="reload">
          <el-icon><Refresh /></el-icon>
        </el-button>
      </div>
    </div>
    <div v-if="canWrite" class="fm__formats">
      支持 Markdown、文本、HTML、PDF、Word（.doc/.docx）、Excel（.xls/.xlsx）及 ZIP 等格式
    </div>

    <!-- 批量操作栏（多选文件后出现） -->
    <div v-if="selectedCount > 0" class="fm__batch">
      <span class="fm__batch-count">已选 {{ selectedCount }} 个文件</span>
      <el-tooltip
        v-if="canWrite"
        content="忽略内容哈希缓存，对选中文档重跑 pipeline（含 LLM 阶段），重生已挖知识"
        placement="top"
      >
        <el-checkbox v-if="canWrite" v-model="forceRedo" size="small">强制重挖</el-checkbox>
      </el-tooltip>
      <el-button v-if="canWrite" size="small" type="primary" :disabled="!workflowId" @click="batchMine">
        <el-icon class="el-icon--left"><Cpu /></el-icon>{{ forceRedo ? '强制重挖选中' : '挖掘选中' }} ({{ selectedCount }})
      </el-button>
      <el-button v-if="canWrite" size="small" type="danger" plain @click="batchDelete">
        <el-icon class="el-icon--left"><Delete /></el-icon>删除选中
      </el-button>
      <el-button link size="small" @click="clearSelection">清除选择</el-button>
      <span v-if="canWrite && !workflowId" class="fm__batch-warn">先到「挖掘」tab 绑定范式才能挖掘</span>
    </div>

    <!-- List -->
    <div class="fm__list" v-loading="loading">
      <div class="fm__row fm__row--head">
        <div class="fm__col fm__col--check">
          <el-checkbox
            v-if="files.length"
            :model-value="allFilesSelected"
            @change="toggleAllFiles"
          />
        </div>
        <div class="fm__col fm__col--name">名称</div>
        <div class="fm__col fm__col--type">类型</div>
        <div class="fm__col fm__col--size">大小</div>
        <div class="fm__col fm__col--status">状态</div>
        <div class="fm__col fm__col--time">修改时间</div>
      </div>

      <div
        v-if="currentFolderId !== null && dragId"
        class="fm__rootdrop"
        @dragover.prevent
        @drop.stop="dropToRoot"
      >拖到此处 = 移到根目录</div>

      <!-- folders -->
      <div
        v-for="f in childFolders"
        :key="f.id"
        class="fm__row fm__row--folder"
        :class="{ 'fm__row--dragover': dragOverId === f.id, 'fm__row--sel': selId === f.id }"
        draggable="true"
        @click="enterFolder(f.id)"
        @contextmenu.prevent="openCtx($event, 'folder', f)"
        @dragstart="onDragStart($event, 'folder', f.id)"
        @dragend="onDragEnd"
        @dragover.prevent="dragOverId = f.id"
        @dragleave="dragOverId = null"
        @drop.stop="onDrop($event, f.id)"
      >
        <div class="fm__col fm__col--check"></div>
        <div class="fm__col fm__col--name" @click.stop>
          <el-icon class="fm__icon fm__icon--folder"><Folder /></el-icon>
          <span
            class="fm__name"
            :title="f.name"
            @click="onNameClick('folder', f)"
            @dblclick="onNameDblClick('folder', f)"
          >{{ f.name }}</span>
        </div>
        <div class="fm__col fm__col--type">文件夹</div>
        <div class="fm__col fm__col--size fm__col--muted">—</div>
        <div class="fm__col fm__col--status fm__col--muted">—</div>
        <div class="fm__col fm__col--time">{{ formatDate(f.created_at) }}</div>
      </div>

      <!-- files -->
      <div
        v-for="file in files"
        :key="file.id"
        class="fm__row fm__row--file"
        :class="{ 'fm__row--dragover': dragOverId === file.id, 'fm__row--sel': selId === file.id }"
        draggable="true"
        @click="openPreview(file)"
        @contextmenu.prevent="openCtx($event, 'file', file)"
        @dragstart="onDragStart($event, 'file', file.id)"
        @dragend="onDragEnd"
      >
        <div class="fm__col fm__col--check" @click.stop>
          <el-checkbox
            :model-value="selectedFileIds.includes(file.id)"
            @change="(v: boolean) => toggleFileSelect(file.id, v)"
          />
        </div>
        <div class="fm__col fm__col--name" @click.stop>
          <el-icon class="fm__icon" :class="fileIconClass(file)"><component :is="fileIcon(file)" /></el-icon>
          <span
            class="fm__name"
            :title="file.document_name"
            @click="onNameClick('file', file)"
            @dblclick="onNameDblClick('file', file)"
          >{{ file.document_name }}</span>
        </div>
        <div class="fm__col fm__col--type">{{ extOf(file.document_name) || '文件' }}</div>
        <div class="fm__col fm__col--size">{{ humanSize(file.file_size) }}</div>
        <div class="fm__col fm__col--status">
          <el-tag v-if="file.status" :type="docStatusTagType(file.status)" size="small" effect="light">
            {{ docStatusLabel(file.status) }}
          </el-tag>
          <span v-else class="fm__col--muted">—</span>
        </div>
        <div class="fm__col fm__col--time">{{ formatDate(file.modified_at || file.created_at) }}</div>
      </div>

      <EmptyState
        v-if="!loading && !childFolders.length && !files.length"
        :text="canWrite ? '空文件夹，上传文件或新建子文件夹（右键更多操作）' : '空文件夹'"
      />
    </div>

    <div v-if="hint" class="fm__hint">提示：左键进入 · 双击文件名改名 · 右键更多操作 · 拖拽移动</div>

    <!-- 右键菜单 -->
    <teleport to="body">
      <div
        v-if="ctx"
        class="fm__ctx"
        :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }"
        @click.stop
        @contextmenu.prevent
      >
        <div v-if="ctx.kind === 'file'" class="fm__ctx-item" @click="ctxDownload">下载</div>
        <div v-if="canWrite" class="fm__ctx-item" @click="ctxRename">重命名</div>
        <div v-if="canWrite" class="fm__ctx-item fm__ctx-item--danger" @click="ctxDelete">删除</div>
      </div>
    </teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Folder, FolderAdd, UploadFilled, Refresh,
  Document, Picture, Tickets, Cpu, Delete,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import { filenameFromDisposition, saveBlob } from '@/utils/download'
import EmptyState from '@/components/common/EmptyState.vue'
import { docStatusLabel, docStatusTagType } from '@/views/kb/kbMeta'
import type { Component } from 'vue'
import type { KbDocument, KbFolder } from '@/types/kb'

const NAME_CLICK_DELAY = 250 // 区分「单击进入」与「双击改名」

const props = defineProps<{ kbId: string; canWrite: boolean; workflowId: string | null; active: boolean }>()
const emit = defineEmits<{ (e: 'mine-queued'): void }>()
const router = useRouter()
const kbApi = useKbApi()

const folders = ref<KbFolder[]>([])
const files = ref<KbDocument[]>([])
const currentFolderId = ref<string | null>(null)
const loading = ref(false)
const uploading = ref(0)
const selId = ref<string | null>(null)
const hint = ref(true)

// ── 多选 + 批量操作（挖掘触发统一到这里，挖掘 Tab 只展示任务流） ──
const selectedFileIds = ref<string[]>([])
const forceRedo = ref(false)
const selectedCount = computed(() => selectedFileIds.value.length)
const allFilesSelected = computed(
  () => files.value.length > 0 && files.value.every((f) => selectedFileIds.value.includes(f.id)),
)
function toggleFileSelect(id: string, checked: boolean) {
  selectedFileIds.value = checked
    ? (selectedFileIds.value.includes(id) ? selectedFileIds.value : [...selectedFileIds.value, id])
    : selectedFileIds.value.filter((x) => x !== id)
}
function toggleAllFiles(checked: boolean) {
  selectedFileIds.value = checked ? files.value.map((f) => f.id) : []
}
function clearSelection() {
  selectedFileIds.value = []
}

const currentFolder = computed(() => folders.value.find((f) => f.id === currentFolderId.value) ?? null)
const currentPath = computed(() => currentFolder.value?.path ?? '')
const childFolders = computed(() => folders.value.filter((f) => f.parent_id === currentFolderId.value))

const breadcrumb = computed(() => {
  const segs: { id: string; name: string }[] = []
  const parts = currentPath.value ? currentPath.value.split('/') : []
  let acc = ''
  for (const p of parts) {
    acc = acc ? `${acc}/${p}` : p
    const f = folders.value.find((x) => x.path === acc)
    if (f) segs.push({ id: f.id, name: f.name })
  }
  return segs
})

async function loadFolders() { folders.value = await kbApi.listFolders(props.kbId) }
async function loadFiles() { files.value = await kbApi.listDocuments(props.kbId, currentPath.value) }
async function reload() {
  loading.value = true
  try { await Promise.all([loadFolders(), loadFiles()]) }
  catch (e) { ElMessage.error(await apiErrorDetail(e)) }
  finally { loading.value = false }
}

function navTo(id: string | null) { currentFolderId.value = id }
function enterFolder(id: string) { currentFolderId.value = id; selId.value = id }
function openPreview(file: KbDocument) {
  selId.value = file.id
  router.push(`/kb/${props.kbId}/doc/${file.id}`)
}

// ── 单击进入 / 双击改名（计时器区分）──
let nameTimer: number | null = null
function onNameClick(kind: 'file' | 'folder', item: KbDocument | KbFolder) {
  selId.value = item.id
  if (nameTimer) { clearTimeout(nameTimer); nameTimer = null }
  nameTimer = window.setTimeout(() => {
    nameTimer = null
    if (kind === 'folder') enterFolder((item as KbFolder).id)
    else openPreview(item as KbDocument)
  }, NAME_CLICK_DELAY)
}
function onNameDblClick(_kind: 'file' | 'folder', item: KbDocument | KbFolder) {
  if (nameTimer) { clearTimeout(nameTimer); nameTimer = null }
  if ('document_name' in item) renameFile(item)
  else renameFolder(item)
}

// ── 文件夹 CRUD ──
async function newFolder() {
  let name: string
  try {
    const r = await ElMessageBox.prompt('文件夹名称', '新建文件夹', {
      confirmButtonText: '创建', cancelButtonText: '取消',
      inputValidator: (v) => (!!v?.trim() && !/[\\/]/.test(v)) || '名称无效',
    })
    name = r.value.trim()
  } catch { return }
  try { await kbApi.createFolder(props.kbId, { parent_id: currentFolderId.value, name }); ElMessage.success('已创建'); await loadFolders() }
  catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}
async function renameFolder(f: KbFolder) {
  let name: string
  try {
    const r = await ElMessageBox.prompt('新名称', '重命名文件夹', {
      inputValue: f.name, confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: (v) => (!!v?.trim() && !/[\\/]/.test(v)) || '名称无效',
    })
    name = r.value.trim()
  } catch { return }
  try { await kbApi.renameFolder(props.kbId, f.id, name); await reload() }
  catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}
async function deleteFolder(f: KbFolder) {
  try {
    await ElMessageBox.confirm(`确定删除文件夹「${f.name}」？仅空文件夹可删。`, '删除文件夹', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch { return }
  try { await kbApi.deleteFolder(props.kbId, f.id); ElMessage.success('已删除'); await loadFolders() }
  catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

// ── 文件操作 ──
async function handleUpload(opts: UploadRequestOptions) {
  const file = opts.file as File
  uploading.value += 1
  try {
    if (file.name.toLowerCase().endsWith('.zip')) {
      const n = (await kbApi.uploadZip(props.kbId, file)).length
      ElMessage.success(`已解压上传 ${n} 个文档`)
    } else {
      await kbApi.uploadDocument(props.kbId, file, { directory: currentPath.value || undefined })
      ElMessage.success(`已上传 ${file.name}`)
    }
    await loadFiles()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
  finally { uploading.value -= 1 }
}
async function renameFile(file: KbDocument) {
  let name: string
  try {
    const r = await ElMessageBox.prompt('新文件名', '重命名', {
      inputValue: file.document_name, confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: (v) => !!v?.trim() || '名称无效',
    })
    name = r.value.trim()
  } catch { return }
  try { await kbApi.patchDocument(props.kbId, file.id, { document_name: name }); await loadFiles() }
  catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}
async function download(file: KbDocument) {
  try {
    const blob = await kbApi.downloadDocument(props.kbId, file.id)
    saveBlob(blob, filenameFromDisposition(null, file.document_name))
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}
async function deleteFile(file: KbDocument) {
  try {
    await ElMessageBox.confirm(
      `确定删除文件「${file.document_name}」？磁盘文件与库内记录一并删除。`,
      '删除文件',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  try {
    await kbApi.deleteDocument(props.kbId, file.id)
    ElMessage.success('已删除')
    await loadFiles()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

// ── 批量操作（多选文件后出现批量栏） ──
async function batchMine() {
  if (!props.workflowId) {
    ElMessage.warning('请先在「挖掘」tab 选择挖掘范式')
    return
  }
  const ids = [...selectedFileIds.value]
  if (!ids.length) return
  try {
    const res = await kbApi.mineKb(props.kbId, ids, forceRedo.value)
    ElMessage.success(
      `已排队${forceRedo.value ? '强制重挖' : '挖掘'} ${ids.length} 个文档（run ${res.run_id.slice(0, 8)}）` +
        (res.auto_force_redo ? '（范式已变更，自动强制重挖）' : ''),
    )
    forceRedo.value = false
    clearSelection()
    emit('mine-queued') // 通知父组件切到挖掘 tab 并刷新任务列表
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  }
}
async function batchDelete() {
  const ids = [...selectedFileIds.value]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${ids.length} 个文件？磁盘文件与库内记录一并删除。`,
      '批量删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  let ok = 0
  for (const id of ids) {
    try {
      await kbApi.deleteDocument(props.kbId, id)
      ok += 1
    } catch {
      /* 单个失败继续删其余 */
    }
  }
  ElMessage.success(`已删除 ${ok}/${ids.length} 个文件`)
  clearSelection()
  await loadFiles()
}

// ── 拖拽移动 ──
const dragId = ref<string | null>(null)
const dragKind = ref<'file' | 'folder' | null>(null)
const dragOverId = ref<string | null>(null)
function onDragStart(_e: DragEvent, kind: 'file' | 'folder', id: string) {
  dragKind.value = kind; dragId.value = id
}
function onDragEnd() { dragId.value = null; dragKind.value = null; dragOverId.value = null }
async function onDrop(_e: DragEvent, targetFolderId: string) {
  const id = dragId.value; const kind = dragKind.value
  dragId.value = null; dragKind.value = null; dragOverId.value = null
  if (!id || id === targetFolderId) return
  try {
    if (kind === 'file') await kbApi.moveDocument(props.kbId, id, targetFolderId)
    else if (kind === 'folder') await kbApi.moveFolder(props.kbId, id, targetFolderId)
    ElMessage.success('已移动'); await reload()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}
async function dropToRoot() {
  const id = dragId.value; const kind = dragKind.value
  dragId.value = null; dragKind.value = null
  if (!id) return
  try {
    if (kind === 'file') await kbApi.moveDocument(props.kbId, id, null)
    else if (kind === 'folder') await kbApi.moveFolder(props.kbId, id, null)
    ElMessage.success('已移到根目录'); await reload()
  } catch (e) { ElMessage.error(await apiErrorDetail(e)) }
}

// ── 右键菜单 ──
const ctx = ref<{ x: number; y: number; kind: 'file' | 'folder'; item: KbDocument | KbFolder } | null>(null)
function openCtx(e: MouseEvent, kind: 'file' | 'folder', item: KbDocument | KbFolder) {
  selId.value = item.id
  // 菜单贴右下，避免溢出视口
  const x = Math.min(e.clientX, window.innerWidth - 160)
  const y = Math.min(e.clientY, window.innerHeight - 160)
  ctx.value = { x, y, kind, item }
}
function closeCtx() { ctx.value = null }
function onVoidContext(e: MouseEvent) {
  // 仅在空白处右键时不弹自定义菜单（交给浏览器）；停在列表内才拦
  const target = e.target as HTMLElement
  if (!target.closest('.fm__row')) return
  e.preventDefault()
}
function ctxDownload() {
  const i = ctx.value?.item; closeCtx()
  if (i && 'document_name' in i) download(i)
}
function ctxRename() {
  const i = ctx.value?.item; closeCtx()
  if (!i) return
  if ('document_name' in i) renameFile(i); else renameFolder(i)
}
function ctxDelete() {
  const i = ctx.value?.item; closeCtx()
  if (!i) return
  if ('document_name' in i) deleteFile(i); else deleteFolder(i)
}

// ── 辅助 ──
function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}
function humanSize(n?: number | null): string {
  if (!n && n !== 0) return '-'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}
function fileIcon(file: KbDocument): Component {
  const e = extOf(file.document_name)
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'].includes(e)) return Picture
  if (['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'].includes(e)) return Tickets
  return Document
}
function fileIconClass(file: KbDocument): string {
  const e = extOf(file.document_name)
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'].includes(e)) return 'fm__icon--img'
  if (['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'].includes(e)) return 'fm__icon--doc'
  return ''
}
function formatDate(t?: string | null): string {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN')  // 含具体时间
}

onMounted(reload)
watch(() => currentFolderId.value, loadFiles)
watch(() => props.kbId, reload)
// 切回文件 Tab 时刷新：挖掘结束后文件状态（uploaded→mined 等）能即时看到
watch(() => props.active, (now, prev) => {
  if (now && !prev) reload()
})
</script>

<style scoped>
.fm { display: flex; flex-direction: column; gap: 12px; }

.fm__toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.fm__crumb { font-size: 13px; }
.fm__crumb-root, .fm__crumb-seg { cursor: pointer; color: var(--kb-accent); }
.fm__crumb-root:hover, .fm__crumb-seg:hover { text-decoration: underline; }
.fm__actions { display: flex; gap: 8px; align-items: center; }
.fm__formats { color: var(--kb-text-secondary); font-size: 12px; line-height: 1.5; }

.fm__list {
  background: var(--kb-bg-card); border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius); overflow: hidden;
}
.fm__rootdrop {
  border-top: 1px dashed var(--kb-accent); border-bottom: 1px dashed var(--kb-accent);
  background: var(--kb-accent-soft); padding: 6px 14px; color: var(--kb-accent); font-size: 12px;
}

.fm__row {
  display: grid;
  grid-template-columns: 36px minmax(240px, 1fr) 80px 90px 100px 120px;
  align-items: center; gap: 10px;
  padding: 8px 14px; border-bottom: 1px solid var(--kb-border-light);
  font-size: 13px; color: var(--kb-text-primary); user-select: none;
}
.fm__row:last-child { border-bottom: none; }
.fm__row--head {
  font-size: 11.5px; font-weight: 600; color: var(--kb-text-tertiary);
  text-transform: uppercase; letter-spacing: 0.3px; border-bottom: 1px solid var(--kb-border);
}
.fm__row--folder, .fm__row--file { cursor: pointer; }
.fm__row--folder { background: var(--kb-accent-soft); }
.fm__row--folder:hover, .fm__row--file:hover { background: var(--kb-bg-sidebar-hover); }
.fm__row--sel { box-shadow: inset 0 0 0 1.5px var(--kb-accent); }
.fm__row--dragover { background: var(--kb-accent-medium) !important; box-shadow: inset 0 0 0 2px var(--kb-accent); }

.fm__col { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fm__col--name { display: flex; align-items: center; gap: 8px; }
.fm__col--type, .fm__col--size, .fm__col--time { color: var(--kb-text-secondary); font-size: 12px; }
.fm__col--muted { color: var(--kb-text-tertiary); }

.fm__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: text; }
.fm__name:hover { color: var(--kb-accent); }

.fm__icon { font-size: 17px; flex-shrink: 0; color: var(--kb-text-secondary); }
.fm__icon--folder { color: var(--kb-warning); }
.fm__icon--img { color: var(--kb-success); }
.fm__icon--doc { color: var(--kb-danger); }

.fm__hint { font-size: 11.5px; color: var(--kb-text-tertiary); }

.fm__col--check { display: flex; align-items: center; }
.fm__batch {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  background: var(--kb-accent-soft);
  border: 1px solid var(--kb-accent);
  border-radius: var(--kb-radius);
  padding: 8px 14px;
  font-size: 13px;
}
.fm__batch-count {
  font-weight: 600;
  color: var(--kb-accent);
  margin-right: 4px;
}
.fm__batch-warn {
  font-size: 11.5px;
  color: var(--kb-warning);
}
</style>

<style>
/* 右键菜单：全局（teleport 到 body），不受 scoped 影响 */
.fm__ctx {
  position: fixed; z-index: 3000; min-width: 140px;
  background: var(--kb-bg-card); border: 1px solid var(--kb-border);
  border-radius: 8px; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.16);
  padding: 4px; font-size: 13px;
}
.fm__ctx-item { padding: 7px 12px; border-radius: 5px; cursor: pointer; color: var(--kb-text-primary); }
.fm__ctx-item:hover { background: var(--kb-accent-soft); color: var(--kb-accent); }
.fm__ctx-item--danger:hover { background: var(--kb-danger-soft); color: var(--kb-danger); }
</style>
