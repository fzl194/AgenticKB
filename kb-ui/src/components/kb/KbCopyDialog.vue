<template>
  <el-dialog
    :model-value="modelValue"
    :title="t('kbManage.copy.title')"
    width="480px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert type="info" :closable="false" class="kb-copy__notice">
      <p>{{ t('kbManage.copy.intro') }}</p>
      <p>{{ t('kbManage.copy.hint1') }}<b>{{ t('kbManage.copy.hint2') }}</b>{{ t('kbManage.copy.hint3') }}</p>
    </el-alert>
    <el-form :model="form" label-width="72px" @submit.prevent>
      <el-form-item :label="t('kbManage.copy.source')">
        <el-input :model-value="kb?.name ?? ''" disabled />
      </el-form-item>
      <el-form-item :label="t('common.name')" required>
        <el-input v-model="form.name" maxlength="80" show-word-limit />
      </el-form-item>
      <el-form-item :label="t('kbManage.copy.visibility')">
        <el-radio-group v-model="form.visibility" :disabled="!canSetPublic && form.visibility === 'public'">
          <el-radio value="private">{{ t('kbView.visibility.private') }}</el-radio>
          <el-radio value="public" :disabled="!canSetPublic">{{ t('kbView.visibility.public') }}</el-radio>
        </el-radio-group>
        <div v-if="!canSetPublic" class="kb-copy__hint">
          {{ t('kbManage.copy.publicHint') }}
        </div>
      </el-form-item>
      <el-form-item :label="t('common.description')">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          maxlength="300"
          show-word-limit
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">{{ t('kbManage.copy.submit') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useKbApi } from '@/api/kb'
import { apiErrorDetail } from '@/api/proxyClient'
import type { KbCopyResult, KbSummary, KbVisibility } from '@/types/kb'
import { t } from '@/locales/t'

const props = defineProps<{ modelValue: boolean; kb: KbSummary | null }>()
const emit = defineEmits<{
  'update:modelValue': [boolean]
  copied: [KbCopyResult]
}>()

const kbApi = useKbApi()
const authStore = useAuthStore()
const submitting = ref(false)

const form = reactive<{ name: string; visibility: KbVisibility; description: string }>({
  name: '',
  visibility: 'private',
  description: '',
})

/** 防放大规则（后端强约束，此处仅 UI 预锁）：public 仅源库 owner / site-admin。 */
const canSetPublic = computed(
  () => props.kb?.my_role === 'owner' || authStore.siteRole === 'admin',
)

/** 缺省名派生（与后端 _derive_copy_name 同规则）：源名超长先截源名保后缀。 */
function deriveName(sourceName: string): string {
  const suffix = t('kbManage.copy.nameSuffix', {
    date: new Date().toISOString().slice(0, 10).replace(/-/g, ''),
  })
  const stem = sourceName.length + suffix.length > 80
    ? sourceName.slice(0, 80 - suffix.length).trimEnd()
    : sourceName
  return stem + suffix
}

// 每次打开按源库预填
watch(
  () => props.modelValue,
  (open) => {
    if (open && props.kb) {
      form.name = deriveName(props.kb.name)
      // 非 owner 继承 public 会被后端强制 private——直接预填 private，不制造假选项
      form.visibility = props.kb.visibility === 'public' && canSetPublic.value
        ? 'public'
        : 'private'
      form.description = props.kb.description ?? ''
    }
  },
)

function close() {
  emit('update:modelValue', false)
}

async function submit() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning(t('kbManage.copy.nameRequired'))
    return
  }
  submitting.value = true
  try {
    const result = await kbApi.copyKb(props.kb!.id, {
      new_name: name,
      visibility: form.visibility,
      description: form.description.trim() || null,
    })
    ElMessage.success(
      t('kbManage.copy.success', {
        docs: result.copy_stats.documents,
        lineage: result.copy_stats.linked_snapshots,
      }),
    )
    emit('update:modelValue', false)
    emit('copied', result)
  } catch (e) {
    ElMessage.error(await apiErrorDetail(e))
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.kb-copy__notice {
  margin-bottom: 14px;
}
.kb-copy__notice p {
  margin: 0 0 4px;
  font-size: 12.5px;
  line-height: 1.6;
}
.kb-copy__notice p:last-child {
  margin-bottom: 0;
}
.kb-copy__hint {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--kb-text-tertiary);
  line-height: 1.5;
}
</style>
