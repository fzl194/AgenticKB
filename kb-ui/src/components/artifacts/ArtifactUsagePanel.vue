<template>
  <div class="usage-panel">
    <!-- C01 运行态说明（2026-11-13 重排，高保真 ar-summary 语言）：三枚摘要卡
         + 使用说明段落 + 引用键行——替代旧 el-descriptions 表格。

         2026-11-13 用户拍板：撤掉「Agent 三条件」检查清单（治理细节不占阅读面），
         钥匙指引收敛为 Agent 开放卡内一句话 + 链接（M01：不显示推测的开放态）。

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
        <span>
          {{ agentOpenHint }}
          <router-link
            v-if="artifact.status !== 'deprecated'"
            class="usage-panel__cell-link"
            to="/mcp"
          >去 MCP 配置</router-link>
        </span>
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
  props.artifact.status === 'deprecated' ? '制品已停用' : '需显式开放')

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
.usage-panel__cell-link {
  margin-left: 6px;
  font-size: 11.5px;
  color: var(--kb-accent, #0891b2);
  text-decoration: none;
  white-space: nowrap;
}
.usage-panel__cell-link:hover { text-decoration: underline; }
</style>
