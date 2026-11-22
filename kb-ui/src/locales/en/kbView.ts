import type { zhKbView } from '../zh/kbView'

export const enKbView: Record<keyof typeof zhKbView, string> = {
  'kbView.visibility.public': 'Public',
  'kbView.visibility.private': 'Private',
  'kbView.role.owner': 'Owner',
  'kbView.role.editor': 'Editor',
  'kbView.role.viewer': 'Viewer',
  'kbView.docStatus.minedUnchanged': 'Indexed (unchanged)',
  'kbView.docStatus.uploaded': 'Not mined',
  'kbView.docStatus.updateFailed': 'Update failed; previous version still in use',
  'kbView.docStatus.failed': 'Mining failed; waiting for retry',
  'kbView.docStatus.unknown': 'Unknown',
  // 检索错误三分型（api/serving.ts localizeSearchError）/ 任务详情部分加载（stores/mining.ts）
  'kbView.search.noActiveBuild': 'The selected knowledge base has no retrievable content: mining has not completed, or all documents failed to mine. Complete a successful mining run first (partial success is enough to search).',
  'kbView.search.kbNotFound': 'Cannot access the selected knowledge base: it does not exist, or this account cannot read it (owner/member/public/System Admin required).',
  'kbView.search.forbidden': 'No permission to access the selected knowledge base; check its visibility or contact an administrator.',
  'kbView.runDetail.partialLoad': 'The task was loaded, but some progress or document details are temporarily unavailable',
}
