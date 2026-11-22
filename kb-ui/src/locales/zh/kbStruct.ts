/** 文档结构图映射文案（utils/documentStructureGraph）。 */
export const zhKbStruct = {
  // 内容组类型
  'kbStruct.block.prose': '正文',
  'kbStruct.block.list': '列表',
  'kbStruct.block.code': '代码',
  'kbStruct.block.formula': '公式',
  'kbStruct.block.figure_caption': '图片说明',
  // 文档根 / 章节
  'kbStruct.untitledDoc': '未命名文档',
  'kbStruct.currentDoc': '当前文档',
  'kbStruct.untitledSection': '未命名章节',
  'kbStruct.headingLevel': '{level} 级标题',
  'kbStruct.segmentCount': '{n} 个内容片段',
  'kbStruct.unassigned': '未归属内容',
  // 表格
  'kbStruct.tableFallback': '表格 {n}',
  'kbStruct.tableDims': '{rows} 行 × {columns} 列',
  // 诊断
  'kbStruct.diag.tooManySections': '章节数量较多，当前结构视图仅构建前 {max} 个章节',
  'kbStruct.diag.sectionsTruncated': '章节列表已由服务端截断，完整结构请查看高级信息',
  'kbStruct.diag.dupSection': '发现重复结构元素编号 {id}，已保留为独立章节',
  'kbStruct.diag.parentMissing': '章节 {title} 的父章节不可用，已降级到文档根节点',
  'kbStruct.diag.tooManyTables': '表格数量较多，当前结构视图仅构建前 {max} 张表格',
  'kbStruct.diag.tablesTruncated': '表格列表已由服务端截断，完整结构请查看高级信息',
  'kbStruct.diag.dupTable': '发现重复表格编号 {id}，已保留为独立表格',
} as const
