import { getActivePinia } from 'pinia'
import { useLocaleStore } from '@/stores/locale'
import { translate } from './translate'

/**
 * 四区组件与所有非 .vue 模块（utils/api/stores）的统一入口。
 * 有活动 pinia → 走 store.t（读 lang ref，渲染期调用即建立响应依赖，切换语言自动重算）；
 * 无活动 pinia → 回落 zh 纯查表（纯函数单测不崩，既有中文断言零改动）。
 */
export function t(key: string, params?: Record<string, string | number>): string {
  return getActivePinia() ? useLocaleStore().t(key, params) : translate('zh', key, params)
}
