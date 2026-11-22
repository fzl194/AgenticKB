<template>
  <div class="onenet-ref" v-loading="loading">
    <el-alert v-if="!canWrite" type="info" :closable="false"
              :title="t('kbSearch.onenet.readonlyTitle')" />

    <!-- 已引用文档（外部引用区，只读语义） -->
    <div class="onenet-ref__block">
      <h4 class="onenet-ref__title">{{ t('kbSearch.onenet.referencedTitle', { n: refs.length }) }}</h4>
      <el-table v-if="refs.length" :data="refs" size="small">
        <el-table-column prop="document_name" :label="t('kbSearch.graph.kindDocument')" min-width="240" show-overflow-tooltip />
        <el-table-column prop="directory_path" :label="t('kbSearch.onenet.colSectionDirectory')" min-width="200" show-overflow-tooltip />
        <el-table-column prop="source_kb_name" :label="t('kbSearch.onenet.colSourceKb')" width="140" />
        <el-table-column :label="t('common.actions')" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="preview(row)">{{ t('kbSearch.onenet.preview') }}</el-button>
            <el-button v-if="canWrite" size="small" type="danger" plain
                       @click="unref([row.document_id])">{{ t('kbSearch.onenet.unref') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else :description="t('kbSearch.onenet.emptyRefs')" :image-size="60" />
    </div>

    <!-- 导入池（本域已完成的产品文档） -->
    <div class="onenet-ref__block">
      <h4 class="onenet-ref__title">{{ t('kbSearch.onenet.importsTitle') }}</h4>
      <el-collapse v-if="imports.length" v-model="expanded">
        <el-collapse-item v-for="imp in imports" :key="imp.id" :name="imp.id">
          <template #title>
            {{ imp.doc_name ?? imp.source_id }}
            <el-tag size="small" type="info" style="margin-left: 8px">
              {{ t('dashboard.kbCard.docCount', { n: imp.document_count ?? 0 }) }} · {{ imp.parsed_version_seen ?? '-' }}
            </el-tag>
          </template>
          <div class="onenet-ref__docrow" v-if="documentsByImport[imp.id]?.length">
            <el-table :data="documentsByImport[imp.id]" size="small" max-height="300"
                      @selection-change="(rows: ImportDoc[]) => (selection[imp.id] = rows)">
              <el-table-column type="selection" width="40" :selectable="() => canWrite" />
              <el-table-column prop="document_name" :label="t('kbSearch.onenet.colFile')" min-width="220" show-overflow-tooltip />
              <el-table-column prop="directory_path" :label="t('kbSearch.onenet.colDirectory')" min-width="180" show-overflow-tooltip />
            </el-table>
            <div class="onenet-ref__actions">
              <el-button type="primary" size="small" :disabled="!selection[imp.id]?.length"
                         :loading="adding === imp.id" @click="addRefs(imp.id)">
                {{ t('kbSearch.onenet.referenceSelected', { n: selection[imp.id]?.length ?? 0 }) }}
              </el-button>
            </div>
          </div>
          <div v-else class="onenet-ref__loading">{{ t('kbSearch.onenet.expandHint') }}</div>
        </el-collapse-item>
      </el-collapse>
      <el-empty v-else :description="t('kbSearch.onenet.emptyImports')" :image-size="60" />
    </div>

    <!-- 逻辑文档 markdown 预览 -->
    <el-dialog v-model="previewVisible" :title="previewTitle" width="70%" top="5vh">
      <pre class="onenet-ref__md">{{ previewText }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useOnenetApi } from '@/api/onenet'
import type { OnenetImport, OnenetRef } from '@/api/onenet'
import { t } from '@/locales/t'

interface ImportDoc {
  id: string
  document_name: string
  directory_path: string | null
}

const props = defineProps<{ kbId: string; canWrite?: boolean; active?: boolean }>()

const api = useOnenetApi()

const loading = ref(false)
const refs = ref<OnenetRef[]>([])
const imports = ref<OnenetImport[]>([])
const expanded = ref<string[]>([])
const documentsByImport = reactive<Record<string, ImportDoc[]>>({})
const selection = reactive<Record<string, ImportDoc[]>>({})
const adding = ref('')

const previewVisible = ref(false)
const previewTitle = ref('')
const previewText = ref('')

const canWrite = computed(() => props.canWrite !== false)

async function reload() {
  loading.value = true
  try {
    // 审查 H8：走库级端点（KB 成员可读），不依赖 admin 面
    const [refList, importList] = await Promise.all([
      api.listRefs(props.kbId),
      api.listKbImports(props.kbId),
    ])
    refs.value = refList
    imports.value = importList
    for (const imp of imports.value) {
      documentsByImport[imp.id] = (imp.documents ?? []) as ImportDoc[]
    }
  } catch (e) {
    ElMessage.error((e as Error)?.message ?? t('common.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function addRefs(importId: string) {
  const docs = selection[importId] ?? []
  if (!docs.length) return
  adding.value = importId
  try {
    const out = await api.addRefs(props.kbId, docs.map((d) => d.id))
    // 「，跳过 N」是条件片段，自带标点
    ElMessage.success(
      t('kbSearch.onenet.added', { n: out.added.length })
      + (out.skipped.length ? t('kbSearch.onenet.addedSkipped', { n: out.skipped.length }) : ''),
    )
    selection[importId] = []
    await reload()
  } catch (e) {
    ElMessage.error((e as Error)?.message ?? t('kbSearch.onenet.addFailed'))
  } finally {
    adding.value = ''
  }
}

async function unref(documentIds: string[]) {
  try {
    const out = await api.removeRefs(props.kbId, documentIds)
    ElMessage.success(t('kbSearch.onenet.unreferenced', { n: out.removed.length }))
    await reload()
  } catch (e) {
    ElMessage.error((e as Error)?.message ?? t('kbSearch.onenet.unrefFailed'))
  }
}

async function preview(row: OnenetRef) {
  previewTitle.value = row.document_name ?? t('kbSearch.onenet.previewTitle')
  previewText.value = t('common.loading')
  previewVisible.value = true
  try {
    previewText.value = await api.fetchDocumentMarkdown(props.kbId, row.document_id)
  } catch (e) {
    previewText.value = t('kbSearch.onenet.previewLoadFailed') + ((e as Error)?.message ?? String(e))
  }
}

onMounted(reload)
watch(() => props.active, (a) => { if (a) void reload() })
</script>

<style scoped>
.onenet-ref { display: flex; flex-direction: column; gap: 16px; }
.onenet-ref__block { border-top: 1px solid var(--el-border-color-lighter); padding-top: 12px; }
.onenet-ref__title { margin: 0 0 8px; font-size: 14px; }
.onenet-ref__block:first-child { border-top: none; padding-top: 0; }
.onenet-ref__actions { margin-top: 8px; }
.onenet-ref__loading { color: var(--el-text-color-secondary); font-size: 12px; padding: 8px 0; }
.onenet-ref__md {
  white-space: pre-wrap; word-break: break-word; max-height: 70vh; overflow: auto;
  background: var(--el-fill-color-light); padding: 12px; border-radius: 4px;
  font-size: 13px; line-height: 1.6;
}
</style>
