/** kb 视图文案（kb 视图 5 文件）。 */
export const zhKbView = {
  // views/kb/kbMeta.ts：可见性 / 角色 / 文档派生状态
  // （已入库/处理中/已发布/已撤回 复用 common.docStatus.*；系统管理员/域管理员 复用 header.roleAdmin/roleDomainAdmin）
  'kbView.visibility.public': '公开',
  'kbView.visibility.private': '私有',
  'kbView.role.owner': '拥有者',
  'kbView.role.editor': '编辑者',
  'kbView.role.viewer': '只读',
  'kbView.docStatus.minedUnchanged': '已入库（未变化）',
  'kbView.docStatus.uploaded': '未挖掘',
  'kbView.docStatus.updateFailed': '更新失败，仍用上一版本',
  'kbView.docStatus.failed': '挖掘失败，等待重试',
  'kbView.docStatus.unknown': '未知',
} as const
