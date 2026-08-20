/**
 * 运维使用分析 API —— 经 main_control_service 代理转发到 mining 的 /api/ops/*。
 *
 * **admin-only**：后端 require_admin 现查库，非 admin 一律 403。前端也要按角色藏起
 * 入口——不是为了安全（安全由后端负责），是因为给无权处理的人看服务指标只是噪声，
 * 而且响应里带用户输入原文。
 */
import { createProxyClient, extractOne } from '@/api/proxyClient'
import type { OpsUsage } from '@/types/ops'

export function useOpsApi() {
  const client = createProxyClient('mining')

  return {
    /**
     * 检索使用分析。days 只影响摘要与各列表；趋势折线固定 30 天（后端 TREND_DAYS），
     * 好与挖掘趋势并排比较。
     *
     * 响应恒带 available：serving 从没启动过时那张表不存在，后端回一份形状相同的空壳，
     * 调用方只需判这一个字段。
     */
    async getUsage(domain: string, days?: number): Promise<OpsUsage> {
      const { data } = await client.get('/api/ops/usage', {
        params: days === undefined ? { domain } : { domain, days },
      })
      return extractOne<OpsUsage>(data)
    },
  }
}
