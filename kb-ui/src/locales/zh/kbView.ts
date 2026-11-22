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
  // 检索错误三分型（api/serving.ts localizeSearchError）/ 任务详情部分加载（stores/mining.ts）
  'kbView.search.noActiveBuild': '所选知识库暂无可检索内容：尚未完成挖掘，或全部文档挖掘失败。请先完成一次成功的挖掘（部分文档成功即可检索）。',
  'kbView.search.kbNotFound': '无法访问所选知识库：库不存在，或当前账号无读取权限（需库主/成员/public/站点管理员）。',
  'kbView.search.forbidden': '没有访问所选知识库的权限，请确认知识库可见性或联系管理员。',
  'kbView.runDetail.partialLoad': '任务已加载，但部分进度或文档明细暂时不可用',
} as const
