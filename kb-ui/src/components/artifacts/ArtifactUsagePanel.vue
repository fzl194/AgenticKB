<template>
  <div class="usage-panel">
    <!-- C01 运行态说明（2026-11-13 重排，高保真 ar-summary 语言）：三枚摘要卡
         + 使用说明段落 + 引用键行 + Agent 三条件条——替代旧 el-descriptions 表格。

         文案契约（测试断言面，改动须同步 __tests__/ArtifactUsagePanel.spec.ts）：
         消费方式两案整句、usage_note 缺省「未填写（发布前必填）」、rev0「未发布」、
         无 artifact_ref 时整块隐藏且不出现「引用键」字样。 -->
    <div class="usage-panel__summary">
      <div class="usage-panel__cell">
        <span class="usage-panel__cell-label">当前正式版</span>
        <span>{{ artifact.current_revision ? `r${artifact.current_revision}` : '未发布' }}</span>
      </div>
      <div class="usage-panel__cell">
        <span class="usage-panel__cell-label">使用方式</span>
        <span>{{ consumptionHint }}</span>
      </div>
      <div class="usage-panel__cell">
        <span class="usage-panel__cell-label">Agent 开放</span>
        <span>{{ agentOpenHint }}</span>
      </div>
    </div>

    <p v-if="artifact.description" class="usage-panel__desc">{{ artifact.description }}</p>
    <p class="usage-panel__usage" :class="{ 'is-missing': !artifact.usage_note }">
      {{ artifact.usage_note || '未填写（发布前必填）' }}
    </p>

    <div v-if="artifact.artifact_ref" class="usage-panel__refline">
      <span class="usage-panel__ref-label">引用键</span>
      <code class="usage-panel__ref">{{ artifact.artifact_ref }}</code>
      <el-button
        size="small" text type="primary"
        data-test="copy-artifact-ref"
        @click="copyRef"
      >复制</el-button>
    </div>

    <!-- Agent 三条件（66号 C01）：前两条本地判定 ✓/✗；钥匙条不查询钥匙状态
         （M01「不能显示推测的已全部开放」）——恒为指引 + MCP 配置链接。 -->
    <div class="usage-panel__agent" data-test="agent-conditions">
      <div class="usage-panel__agent-title">Agent 调用条件（三条均满足才可读取）</div>
      <div class="usage-panel__cond" data-test="cond-published">
        <span :class="condClass(isPublished)">{{ isPublished ? '✓' : '✗' }}</span>
        <span>制品已正式发布（当前：{{ statusText }}）</span>
      </div>
      <div class="usage-panel__cond" data-test="cond-readable">
        <span :class="condClass(isReadable)">{{ isReadable ? '✓' : '✗' }}</span>
        <span>当前用户可读{{ isReadable ? `（身份：${artifact.my_role}）` : '（无成员身份）' }}</span>
      </div>
      <div class="usage-panel__cond" data-test="cond-key-open">
        <span class="usage-panel__cond-unknown">？</span>
        <span>
          MCP 钥匙已开放本制品——<router-link to="/mcp">请到 MCP 配置检查</router-link>
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { copyToClipboard } from '@/utils/clipboard'
import type { Artifact, ArtifactKind } from '@/types/artifacts'

const props = defineProps<{ artifact: Artifact }>()

/** 消费方式文案（66号 §13 按 kind 分派；Record 键钉死 ArtifactKind 全集，
 * 四形态路线新增 kind 时 TS 强制补齐——与 DetailView EMPTY_GUIDES 同风格）。 */
const CONSUMPTION_HINTS: Record<ArtifactKind, string> = {
  table: '通过 Agent 或 MCP 以结构化查询读取（筛选列 / 聚合）；网页端运行态直接浏览表格',
  wiki: '通过 Agent 或 MCP 搜索页面 / 整页读取 / 链接导航；网页端运行态直接阅读',
}

const consumptionHint = computed(() => CONSUMPTION_HINTS[props.artifact.kind])

/** Agent 开放口径（M01）：不查询钥匙状态，不显示推测态——停用是制品侧
 * 事实可判定，其余恒指引导向 MCP 配置。 */
const agentOpenHint = computed(() =>
  props.artifact.status === 'deprecated' ? '制品已停用' : '需显式开放（见下方条件）')

/** 条件一：正式发布——draft/rev0 与 deprecated 均 ✗（停用后 Agent 侧不可见）。 */
const isPublished = computed(() => props.artifact.status === 'published')
/** 条件二：当前用户可读——my_role 存在即 ✓（public 非成员由后端合成 viewer，
 * null 只是防御形态——正常进详情页的读者都有身份）。 */
const isReadable = computed(() => !!props.artifact.my_role)

const STATUS_TEXTS: Record<string, string> = {
  published: '已发布', deprecated: '已停用', draft: '草稿',
}

const statusText = computed(() => STATUS_TEXTS[props.artifact.status] ?? props.artifact.status)

function condClass(ok: boolean): string {
  return ok ? 'usage-panel__cond-ok' : 'usage-panel__cond-bad'
}

/** 复制引用键：navigator.clipboard 优先，非安全上下文降级 execCommand
 * （utils/clipboard 内置降级）；两条路都失败给手动复制提示。 */
async function copyRef(): Promise<void> {
  const ref = props.artifact.artifact_ref
  if (!ref) return
  const ok = await copyToClipboard(ref)
  if (ok) {
    ElMessage.success('引用键已复制')
  } else {
    ElMessage.warning('复制失败——请选中引用键文本手动复制')
  }
}
</script>

<style scoped>
/* 高保真 ar-summary：三枚 hairline 摘要卡（label 小写字距 + 值），下接使用说明
   段落——观感统一系统电青令牌，不加依赖。 */
.usage-panel { text-align: left; }
.usage-panel__summary {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px;
}
.usage-panel__cell {
  min-width: 0;
  padding: 11px 14px;
  border: 1px solid var(--kb-border, #e2e8f0);
  border-radius: 10px;
  background: var(--kb-bg-card, #fff);
  display: flex; flex-direction: column; gap: 4px;
}
.usage-panel__cell-label {
  font-size: 12px; font-weight: 600; letter-spacing: .5px;
  color: var(--kb-text-secondary, #64748b);
}
.usage-panel__cell span {
  font-size: 12.5px; color: var(--kb-text-primary, inherit); line-height: 1.55;
  overflow-wrap: anywhere;
}

.usage-panel__desc {
  margin: 10px 0 0;
  font-size: 12.5px; color: var(--kb-text-secondary, #64748b); line-height: 1.7;
}
.usage-panel__usage {
  margin: 8px 0 0;
  padding: 10px 14px;
  border-left: 3px solid var(--kb-accent-medium, rgba(8, 145, 178, 0.35));
  border-radius: 0 8px 8px 0;
  background: var(--kb-accent-soft, rgba(8, 145, 178, 0.06));
  font-size: 12.5px; line-height: 1.75; color: var(--kb-text-primary, inherit);
}
.usage-panel__usage.is-missing {
  border-left-color: var(--kb-warning, #f59e0b);
  background: rgba(245, 158, 11, 0.07);
  color: var(--kb-warning, #f59e0b);
}

.usage-panel__refline {
  display: flex; align-items: center; gap: 8px;
  margin-top: 10px;
  font-size: 12px;
}
.usage-panel__ref-label { color: var(--kb-text-tertiary, #94a3b8); flex-shrink: 0; }
.usage-panel__ref {
  font-family: 'SF Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 12px; color: var(--kb-text-primary, inherit);
  padding: 2px 8px; border-radius: 5px;
  background: var(--kb-bg-card-hover, #f1f5f9);
  border: 1px solid var(--kb-border-light, #f1f5f9);
  word-break: break-all;
  min-width: 0;
}

.usage-panel__agent {
  margin-top: 12px; padding: 11px 14px;
  border: 1px solid var(--kb-border-light, #f1f5f9);
  border-radius: 10px;
  background: var(--kb-bg-card-hover, #f8fafc);
  font-size: 12.5px; display: flex; flex-direction: column; gap: 7px;
}
.usage-panel__agent-title {
  font-size: 12px; font-weight: 600; letter-spacing: .5px;
  color: var(--kb-text-secondary, #64748b);
}
.usage-panel__cond { display: flex; align-items: center; gap: 8px; }
.usage-panel__cond-ok,
.usage-panel__cond-bad,
.usage-panel__cond-unknown {
  display: inline-grid; place-items: center;
  width: 17px; height: 17px; border-radius: 50%;
  font-size: 10.5px; font-weight: 700; flex-shrink: 0;
}
.usage-panel__cond-ok { color: var(--kb-success, #10b981); background: var(--kb-success-soft, rgba(16, 185, 129, 0.1)); }
.usage-panel__cond-bad { color: var(--kb-danger, #ef4444); background: var(--kb-danger-soft, rgba(239, 68, 68, 0.1)); }
.usage-panel__cond-unknown { color: var(--kb-warning, #f59e0b); background: var(--kb-warning-soft, rgba(245, 158, 11, 0.1)); }
</style>
