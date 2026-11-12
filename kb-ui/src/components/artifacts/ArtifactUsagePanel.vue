<template>
  <div class="usage-panel">
    <!-- C01 说明卡：描述/使用说明/当前版本/消费方式 + 引用键（响应附带才显示，
         不前端拼装——66号「不构造看似真实实际不可调用的引用」） -->
    <el-descriptions :column="1" border size="small">
      <el-descriptions-item label="描述">{{ artifact.description || '—' }}</el-descriptions-item>
      <el-descriptions-item label="使用说明">
        <span :class="{ 'usage-panel__usage-missing': !artifact.usage_note }">
          {{ artifact.usage_note || '未填写（发布前必填）' }}
        </span>
      </el-descriptions-item>
      <el-descriptions-item label="当前版本">
        {{ artifact.current_revision ? `r${artifact.current_revision}` : '未发布' }}
      </el-descriptions-item>
      <el-descriptions-item label="消费方式">{{ consumptionHint }}</el-descriptions-item>
      <el-descriptions-item v-if="artifact.artifact_ref" label="引用键">
        <span class="usage-panel__ref">{{ artifact.artifact_ref }}</span>
        <el-button
          size="small" text type="primary"
          data-test="copy-artifact-ref"
          @click="copyRef"
        >复制</el-button>
      </el-descriptions-item>
    </el-descriptions>

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
.usage-panel { text-align: left; }
.usage-panel__usage-missing { color: var(--kb-warning, #f59e0b); }
.usage-panel__ref { font-family: monospace; margin-right: 8px; word-break: break-all; }
.usage-panel__agent {
  margin-top: 12px; padding: 12px 14px;
  border: 1px solid var(--kb-border, #e2e8f0); border-radius: 8px;
  background: var(--kb-bg-card, #fff);
  font-size: 12.5px; display: flex; flex-direction: column; gap: 8px;
}
.usage-panel__agent-title {
  font-size: 10.5px; font-weight: 600; letter-spacing: 1px;
  color: var(--kb-text-tertiary, #94a3b8);
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
