import type { zhKbManage } from '../zh/kbManage'

export const enKbManage: Record<keyof typeof zhKbManage, string> = {
  // 替换原件（api/kb.ts replaceDocumentContent）
  'kbManage.replace.invalidRevision': 'The file revision is invalid; refresh and retry',
}
