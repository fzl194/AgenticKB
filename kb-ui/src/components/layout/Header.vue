<template>
  <header class="header">
    <div class="header__left">
      <h2 class="header__title">{{ pageTitle }}</h2>
      <span class="header__divider" />
      <span class="header__breadcrumb">{{ route.meta?.description || '' }}</span>
    </div>

    <div class="header__right">
      <el-select
        v-model="domainStore.currentDomain"
        class="header__domain-select"
        size="default"
        @change="onDomainChange"
      >
        <el-option
          v-for="d in domainStore.enabledDomains"
          :key="d.domain_id"
          :label="d.display_name"
          :value="d.domain_id"
        />
      </el-select>

      <div class="header__health" :title="allHealthy ? '全部正常' : '存在异常'">
        <span
          class="header__health-dot"
          :class="{
            'header__health-dot--healthy': allHealthy,
            'header__health-dot--degraded': !allHealthy && someHealthy,
            'header__health-dot--unhealthy': !someHealthy,
          }"
        />
        <span class="header__health-label">{{ allHealthy ? '正常' : '异常' }}</span>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useDomainStore } from '@/stores/domain'
import { useBrandStore } from '@/stores/brand'

const route = useRoute()
const domainStore = useDomainStore()
const brand = useBrandStore()

const pageTitles: Record<string, string> = {
  dashboard: '概览',
  kb: '知识库',
  'kb-detail': '知识库',
  'mining-workflows': '挖掘范式',
  'mining-workflow-editor': '挖掘范式',
  search: '检索测试',
  knowledge: '知识资产',
  'knowledge-detail': '文档详情',
  graph: '知识图谱',
  llm: 'LLM 服务',
  settings: '系统设置',
}

const pageTitle = computed(() => pageTitles[route.name as string] || brand.title)

const allHealthy = ref(true)
const someHealthy = ref(true)

function onDomainChange() {
  allHealthy.value = true
  someHealthy.value = true
}
</script>

<style scoped>
.header {
  height: var(--kb-header-height);
  background: var(--kb-bg-card);
  border-bottom: 1px solid var(--kb-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
}

.header__left {
  display: flex;
  align-items: baseline;
  gap: 0;
}

.header__title {
  font-size: 17px;
  font-weight: 650;
  color: var(--kb-text-primary);
  margin: 0;
  letter-spacing: -0.2px;
}

.header__divider {
  width: 1px;
  height: 16px;
  background: var(--kb-border);
  margin: 0 14px;
  align-self: center;
}

.header__breadcrumb {
  font-size: 13px;
  color: var(--kb-text-tertiary);
}

.header__right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header__domain-select {
  width: 200px;
}

.header__health {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 20px;
  background: var(--kb-accent-soft);
}

.header__health-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--kb-text-tertiary);
  transition: background var(--kb-duration) var(--kb-ease);
}

.header__health-dot--healthy {
  background: var(--kb-success);
  box-shadow: 0 0 6px rgba(16, 185, 129, 0.4);
}

.header__health-dot--degraded {
  background: var(--kb-warning);
  box-shadow: 0 0 6px rgba(245, 158, 11, 0.4);
}

.header__health-dot--unhealthy {
  background: var(--kb-danger);
  box-shadow: 0 0 6px rgba(239, 68, 68, 0.4);
}

.header__health-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--kb-text-secondary);
}
</style>
