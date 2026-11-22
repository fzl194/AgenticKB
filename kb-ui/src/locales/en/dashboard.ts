import type { zhDashboard } from '../zh/dashboard'

export const enDashboard: Record<keyof typeof zhDashboard, string> = {
  'dashboard.pending.reviewText': 'Mining is paused, awaiting manual review',
  'dashboard.pending.reviewAction': 'Resolve',
  'dashboard.pending.failedDocs': '{n} documents failed to parse',
}
