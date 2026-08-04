<template>
  <el-drawer
    :model-value="modelValue"
    title="证据原文"
    size="46%"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-loading="loading" class="fulltext">
      <el-alert v-if="error" type="error" :closable="false" :title="error" class="fulltext__alert" />

      <template v-for="item in result?.items ?? []" :key="item.ref.id">
        <!--
          found=false 覆盖三种情况——不存在、无权限、已被新一轮挖掘移出当前 build——
          后端刻意不区分它们，所以这里也不能编造更具体的解释。
        -->
        <el-alert
          v-if="!item.found"
          type="warning"
          :closable="false"
          class="fulltext__alert"
          title="该证据已不在当前检索范围内"
          description="内容可能已被重新挖掘，或所在知识库当前不可见。请基于已有证据作答。"
        />

        <template v-else>
          <div v-if="item.unit" class="fulltext__block">
            <div class="fulltext__label">检索单元{{ item.unit.title ? ` · ${item.unit.title}` : '' }}</div>
            <p class="fulltext__text">{{ item.unit.text }}</p>
          </div>

          <!--
            命中段与前后文一起按原文顺序返回。上下文淡显，让人一眼看出哪一段是检索命中的，
            又不必在两个抽屉之间来回切。
          -->
          <div
            v-for="seg in item.segments"
            :key="seg.id"
            class="fulltext__block"
            :class="{ 'fulltext__block--context': seg.role !== 'target' }"
          >
            <div class="fulltext__label">
              <span>{{ seg.sectionPath?.length ? seg.sectionPath.join(' › ') : (seg.sectionTitle || '正文') }}</span>
              <span v-if="seg.blockType" class="fulltext__tag">{{ seg.blockType }}</span>
              <span v-if="seg.role === 'target'" class="fulltext__tag fulltext__tag--target">命中</span>
            </div>
            <p class="fulltext__text">{{ seg.text }}</p>

            <div class="fulltext__source" v-if="seg.role === 'target'">
              <span class="fulltext__doc">{{ seg.documentName || seg.documentKey || '未知来源' }}</span>
              <span v-if="seg.kbId" class="fulltext__tag">{{ kbLabel(seg.kbId) }}</span>
              <el-button
                v-if="seg.hasRawFile && seg.documentId"
                link
                type="primary"
                size="small"
                :loading="downloadingDocId === seg.documentId"
                @click="download(seg)"
              >
                下载原件
              </el-button>
            </div>
            <!--
              原件是磁盘上「当前的」文件，片段来自挖掘那一刻的快照。文档重传过就对不上，
              说清楚比让人自己发现强。
            -->
            <div v-if="seg.role === 'target' && seg.hasRawFile" class="fulltext__hint">
              原件为文档当前版本，若挖掘后重新上传过，内容可能与上方片段不一致
            </div>
          </div>
        </template>
      </template>

      <EmptyState v-if="!loading && !error && !result?.items?.length" text="没有可展开的内容" />
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useServingApi } from '@/api/serving'
import { apiErrorDetail } from '@/api/proxyClient'
import { filenameFromDisposition, saveBlob } from '@/utils/download'
import type { FullTextResult, FullTextSegment } from '@/types'
import type { KbSummary } from '@/types/kb'
import EmptyState from '@/components/common/EmptyState.vue'

const props = defineProps<{
  modelValue: boolean
  result: FullTextResult | null
  loading: boolean
  error: string
  /** 产生这批 id 的那次检索所用的范围，下载必须复现它，否则文档会落在 scope 之外。 */
  domain?: string
  kbIds?: string[]
  kbs?: KbSummary[]
}>()

const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

const servingApi = useServingApi()
const downloadingDocId = ref('')

function kbLabel(kbId: string): string {
  return props.kbs?.find(kb => kb.id === kbId)?.name || kbId
}

async function download(seg: FullTextSegment) {
  if (!seg.documentId) return
  downloadingDocId.value = seg.documentId
  try {
    const { blob, disposition } = await servingApi.downloadRawFile(seg.documentId, {
      domain: props.domain,
      kbIds: props.kbIds,
    })
    saveBlob(blob, filenameFromDisposition(disposition, seg.documentName || 'download'))
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    downloadingDocId.value = ''
  }
}
</script>

<style scoped>
.fulltext {
  min-height: 200px;
}

.fulltext__alert {
  margin-bottom: 12px;
}

.fulltext__block {
  padding: 12px 14px;
  margin-bottom: 12px;
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius-sm);
}

.fulltext__block--context {
  background: transparent;
  border-style: dashed;
}

.fulltext__block--context .fulltext__text {
  color: var(--kb-text-secondary);
}

.fulltext__label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  color: var(--kb-text-tertiary);
  margin-bottom: 8px;
}

.fulltext__text {
  font-size: 13px;
  line-height: 1.7;
  color: var(--kb-text-primary);
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.fulltext__source {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--kb-border-light);
}

.fulltext__doc {
  font-size: 12px;
  color: var(--kb-text-secondary);
}

.fulltext__tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--kb-border-light);
  color: var(--kb-text-secondary);
  font-weight: 500;
}

.fulltext__tag--target {
  background: var(--kb-accent-soft);
  color: var(--kb-accent);
}

.fulltext__hint {
  font-size: 11px;
  color: var(--kb-text-tertiary);
  margin-top: 6px;
}
</style>
