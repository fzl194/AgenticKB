<template>
  <aside class="sidebar">
    <div class="sidebar__logo">
      <div
        class="sidebar__logo-icon"
        :class="{ 'sidebar__logo-icon--img': !!logoSrc }"
      >
        <img v-if="logoSrc" :src="logoSrc" alt="logo" class="sidebar__logo-img" />
        <template v-else>{{ brand.logoText }}</template>
      </div>
      <div class="sidebar__logo-text">
        <span class="sidebar__logo-name">{{ brand.name }}</span>
        <span class="sidebar__logo-badge">{{ brand.badge }}</span>
      </div>
    </div>

    <nav class="sidebar__nav">
      <router-link
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="sidebar__link"
        :class="{ 'sidebar__link--active': isActive(item.path) }"
      >
        <el-icon :size="18"><component :is="item.icon" /></el-icon>
        <span>{{ item.label }}</span>
      </router-link>
    </nav>

    <div class="sidebar__footer">
      <div class="sidebar__domain">
        <span class="sidebar__domain-dot" />
        <span class="sidebar__domain-name">{{ domainStore.currentDomainInfo?.display_name || domainStore.currentDomain }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import {
  Monitor, Management, Key,
  Cpu, Setting, Collection, Connection, DataLine, Files,
} from '@element-plus/icons-vue'
import { useDomainStore } from '@/stores/domain'
import { useBrandStore, resolveIcon } from '@/stores/brand'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const domainStore = useDomainStore()
const brand = useBrandStore()
const auth = useAuthStore()

// 有图标时显示 <img>；空则回落 logoText 渐变块。
const logoSrc = computed(() => (brand.icon.trim() ? resolveIcon(brand.icon) : ''))

// 导航项：requiresAdmin 标记管理类项，member 不渲染。
// 批次6：独立"检索"菜单下线——检索入口收进知识库详情的"检索" tab（全走检索范式）。
const ALL_NAV = [
  { path: '/', label: '概览', icon: Monitor, requiresAdmin: false },
  { path: '/kb', label: '知识库', icon: Files, requiresAdmin: false },
  { path: '/mcp', label: 'MCP 接入', icon: Key, requiresAdmin: false },
  { path: '/mining/workflows', label: '挖掘范式', icon: Management, requiresAdmin: true },
  { path: '/paradigm', label: '检索范式', icon: Connection, requiresAdmin: true },
  { path: '/entities', label: '实体图谱', icon: Connection, requiresAdmin: true },
  { path: '/ontology', label: '本体版本', icon: Collection, requiresAdmin: true },
  { path: '/ontology/graph', label: '本体图谱', icon: DataLine, requiresAdmin: true },
  { path: '/llm', label: 'LLM 服务', icon: Cpu, requiresAdmin: true },
  { path: '/settings', label: '系统设置', icon: Setting, requiresAdmin: true },
]

const navItems = computed(() =>
  ALL_NAV.filter((it) => !it.requiresAdmin || auth.siteRole === 'admin'),
)

function isActive(path: string): boolean {
  if (path === '/') return route.path === '/'
  if (path === '/ontology') {
    return route.path === '/ontology'
  }
  return route.path === path || route.path.startsWith(path + '/')
}
</script>

<style scoped>
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: var(--kb-sidebar-width);
  background: var(--kb-bg-sidebar);
  display: flex;
  flex-direction: column;
  z-index: 100;
  overflow: hidden;
}

/* Logo */
.sidebar__logo {
  height: var(--kb-header-height);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.sidebar__logo-icon {
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
  letter-spacing: -0.5px;
  flex-shrink: 0;
}

.sidebar__logo-icon--img {
  background: transparent;
  overflow: hidden;
}

.sidebar__logo-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.sidebar__logo-text {
  display: flex;
  flex-direction: column;
  line-height: 1;
}

.sidebar__logo-name {
  color: #f1f5f9;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.3px;
}

.sidebar__logo-badge {
  color: var(--kb-text-tertiary);
  font-size: 10px;
  font-weight: 500;
  margin-top: 3px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

/* Navigation */
.sidebar__nav {
  flex: 1;
  padding: 12px 10px;
  overflow-y: auto;
}

.sidebar__link {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  margin-bottom: 2px;
  border-radius: 8px;
  color: var(--kb-text-sidebar);
  text-decoration: none;
  font-size: 13.5px;
  font-weight: 450;
  transition: all var(--kb-duration) var(--kb-ease);
}

.sidebar__link:hover {
  background: var(--kb-bg-sidebar-hover);
  color: #e2e8f0;
}

.sidebar__link--active {
  background: var(--kb-bg-sidebar-active);
  color: var(--kb-text-sidebar-active);
  font-weight: 600;
}

/* Footer */
.sidebar__footer {
  padding: 14px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.sidebar__domain {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sidebar__domain-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--kb-accent);
  box-shadow: 0 0 6px var(--kb-accent);
}

.sidebar__domain-name {
  color: var(--kb-text-tertiary);
  font-size: 12px;
  font-weight: 500;
}
</style>
