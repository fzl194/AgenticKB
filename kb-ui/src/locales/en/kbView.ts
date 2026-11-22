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
}
