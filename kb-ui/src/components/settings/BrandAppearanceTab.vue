<template>
  <div class="brand-tab">
    <div class="brand-tab__top-hint">
      站点品牌（部署级，整站统一）。保存后即时生效：浏览器标签、favicon、侧边栏 logo 同步更新。
    </div>

    <div class="brand-tab__grid">
      <!-- 左：表单 -->
      <div class="brand-tab__form">
        <el-form label-position="top" :disabled="loading">
          <el-form-item label="网站标题（浏览器标签 + 页头兜底名）">
            <el-input v-model="form.title" placeholder="CoreMasterKB" />
          </el-form-item>
          <el-form-item label="Logo 主名（侧边栏）">
            <el-input v-model="form.name" placeholder="CoreMaster" />
          </el-form-item>
          <el-form-item label="Logo 副标题（侧边栏）">
            <el-input v-model="form.badge" placeholder="Knowledge Base" />
          </el-form-item>
          <el-form-item label="字母标记（无图标时渐变方块里的字母）">
            <el-input v-model="form.logoText" placeholder="KB" maxlength="4" />
          </el-form-item>
          <el-form-item label="网站图标（favicon + 侧边栏 logo）">
            <div class="brand-tab__icon-row">
              <el-input v-model="form.icon" placeholder="留空用默认 /favicon.svg；可粘贴 data URI 或 URL" />
              <input
                ref="fileInputRef"
                type="file"
                accept="image/*"
                class="brand-tab__file-input"
                @change="onFileChange"
              />
              <el-button size="small" @click="pickFile">选择文件…</el-button>
              <el-button size="small" text @click="form.icon = ''">清空</el-button>
            </div>
            <div class="brand-tab__hint">支持 PNG/SVG 等；文件以 base64 内嵌进 ui.yaml（约 1.3 倍体积）。</div>
          </el-form-item>
          <el-form-item label="管理员联系方式（登录失败「联系管理员」提示用）">
            <el-input v-model="form.adminContact" placeholder="如：张三 / 工号 12345 / 内线 8888（留空则不显示）" />
          </el-form-item>
        </el-form>

        <div class="brand-tab__actions">
          <el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存</el-button>
          <el-button :disabled="loading" @click="load">重置</el-button>
        </div>
      </div>

      <!-- 右：预览 -->
      <div class="brand-tab__preview">
        <div class="brand-tab__preview-label">实时预览</div>

        <div class="brand-tab__browser-tab">
          <img v-if="iconSrc" :src="iconSrc" class="brand-tab__favicon" alt="" />
          <span v-else class="brand-tab__favicon brand-tab__favicon--text">{{ form.logoText || 'KB' }}</span>
          <span class="brand-tab__browser-title">{{ form.title || 'CoreMasterKB' }}</span>
          <span class="brand-tab__browser-close">×</span>
        </div>

        <div class="brand-tab__sidebar-preview">
          <div class="brand-tab__logo-icon" :class="{ 'brand-tab__logo-icon--img': !!iconSrc }">
            <img v-if="iconSrc" :src="iconSrc" alt="" />
            <template v-else>{{ form.logoText || 'KB' }}</template>
          </div>
          <div class="brand-tab__logo-text">
            <span class="brand-tab__logo-name">{{ form.name || 'CoreMaster' }}</span>
            <span class="brand-tab__logo-badge">{{ form.badge || 'Knowledge Base' }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useControlPlaneApi } from '@/api/controlPlane'
import { useBrandStore, resolveIcon } from '@/stores/brand'
import { parseUiYaml, buildUiYaml } from '@/utils/brandYaml'
import type { BrandConfig } from '@/types/brand'

const api = useControlPlaneApi()
const brand = useBrandStore()

interface SiteForm {
  title: string
  name: string
  badge: string
  logoText: string
  icon: string
  adminContact: string
}

const DEFAULTS: SiteForm = {
  title: 'CoreMasterKB',
  name: 'CoreMaster',
  badge: 'Knowledge Base',
  logoText: 'KB',
  icon: '',
  adminContact: '',
}

const form = reactive<SiteForm>({ ...DEFAULTS })
const original = ref<SiteForm>({ ...DEFAULTS })
/** ui.yaml 中 site 以外的键，保存时原样回写（保留历史 api-base 等）。 */
const restConfig = ref<Record<string, unknown>>({})
const loading = ref(false)
const saving = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)

const dirty = computed(() =>
  (Object.keys(DEFAULTS) as (keyof SiteForm)[]).some(
    (k) => (form[k] ?? '') !== (original.value[k] ?? ''),
  ),
)

const iconSrc = computed(() => (form.icon.trim() ? resolveIcon(form.icon) : ''))

function setFormFromSite(site: Partial<BrandConfig>): void {
  form.title = site.title ?? DEFAULTS.title
  form.name = site.name ?? DEFAULTS.name
  form.badge = site.badge ?? DEFAULTS.badge
  form.logoText = site.logoText ?? DEFAULTS.logoText
  form.icon = site.icon ?? DEFAULTS.icon
  form.adminContact = site.adminContact ?? DEFAULTS.adminContact
  original.value = { ...form }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const text = await api.getSystemConfigRaw('ui')
    const parts = parseUiYaml(text)
    restConfig.value = parts.rest
    setFormFromSite(parts.site)
  } catch {
    ElMessage.error('加载品牌配置失败')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  try {
    const text = buildUiYaml({
      rest: restConfig.value,
      site: {
        title: form.title,
        name: form.name,
        badge: form.badge,
        logoText: form.logoText,
        icon: form.icon,
        adminContact: form.adminContact,
      },
    })
    await api.updateSystemConfigRaw('ui', text)
    original.value = { ...form }
    // 即时生效：刷新品牌 store 并应用到 DOM（标题/favicon/侧边栏）
    await brand.fetchBrand()
    brand.applyBrand()
    ElMessage.success('品牌已保存并生效')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

function pickFile(): void {
  fileInputRef.value?.click()
}

function onFileChange(e: Event): void {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  if (file.size > 512 * 1024) {
    ElMessage.warning('图标建议 < 512KB（将以 base64 内嵌进配置）')
  }
  const reader = new FileReader()
  reader.onload = () => {
    form.icon = String(reader.result ?? '')
  }
  reader.onerror = () => ElMessage.error('读取文件失败')
  reader.readAsDataURL(file)
  // 允许重复选同一文件
  target.value = ''
}

onMounted(load)

// 暴露 save/load 便于（测试或父组件）程序化触发，绕过按钮 stub 的事件透传差异。
defineExpose({ save, load })
</script>

<style scoped>
.brand-tab {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.brand-tab__top-hint,
.brand-tab__hint {
  font-size: 12px;
  color: var(--kb-text-tertiary);
  line-height: 1.6;
}

.brand-tab__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 28px;
  align-items: start;
}

.brand-tab__form {
  max-width: 560px;
}

.brand-tab__icon-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.brand-tab__icon-row .el-input {
  flex: 1;
}

.brand-tab__file-input {
  display: none;
}

.brand-tab__actions {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}

.brand-tab__preview {
  background: var(--kb-bg-hover, rgba(0, 0, 0, 0.02));
  border: 1px solid var(--kb-border-light);
  border-radius: var(--kb-radius);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.brand-tab__preview-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--kb-text-secondary);
}

/* 浏览器标签预览 */
.brand-tab__browser-tab {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--kb-bg-card);
  border: 1px solid var(--kb-border-light);
  border-radius: 8px 8px 0 0;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--kb-text-secondary);
}

.brand-tab__favicon {
  width: 16px;
  height: 16px;
  object-fit: contain;
  flex-shrink: 0;
}

.brand-tab__favicon--text {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 700;
  color: var(--kb-text-tertiary);
}

.brand-tab__browser-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.brand-tab__browser-close {
  color: var(--kb-text-tertiary);
}

/* 侧边栏 logo 预览（复刻 Sidebar 样式） */
.brand-tab__sidebar-preview {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-tab__logo-icon {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--kb-accent), var(--kb-accent-light));
  color: #fff;
  font-size: 13px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.brand-tab__logo-icon--img {
  background: transparent;
  overflow: hidden;
}

.brand-tab__logo-icon--img img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.brand-tab__logo-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
  min-width: 0;
}

.brand-tab__logo-name {
  font-size: 14px;
  font-weight: 700;
  color: var(--kb-text-primary);
}

.brand-tab__logo-badge {
  font-size: 10px;
  color: var(--kb-text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-top: 2px;
}
</style>
