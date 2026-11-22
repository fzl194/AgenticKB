import type { zhKbStruct } from '../zh/kbStruct'

export const enKbStruct: Record<keyof typeof zhKbStruct, string> = {
  // 内容组类型
  'kbStruct.block.prose': 'Paragraph',
  'kbStruct.block.list': 'List',
  'kbStruct.block.code': 'Code',
  'kbStruct.block.formula': 'Formula',
  'kbStruct.block.figure_caption': 'Figure caption',
  // 文档根 / 章节
  'kbStruct.untitledDoc': 'Untitled document',
  'kbStruct.currentDoc': 'Current document',
  'kbStruct.untitledSection': 'Untitled section',
  'kbStruct.headingLevel': 'Heading level {level}',
  'kbStruct.segmentCount': '{n} segments',
  'kbStruct.unassigned': 'Unassigned content',
  // 表格
  'kbStruct.tableFallback': 'Table {n}',
  'kbStruct.tableDims': '{rows} rows × {columns} columns',
  // 诊断
  'kbStruct.diag.tooManySections': 'Too many sections; the structure view only builds the first {max}',
  'kbStruct.diag.sectionsTruncated': 'The section list was truncated by the server; see the advanced details for the full structure',
  'kbStruct.diag.dupSection': 'Duplicate structure element id {id}; kept as a separate section',
  'kbStruct.diag.parentMissing': 'The parent of section "{title}" is unavailable; moved to the document root',
  'kbStruct.diag.tooManyTables': 'Too many tables; the structure view only builds the first {max}',
  'kbStruct.diag.tablesTruncated': 'The table list was truncated by the server; see the advanced details for the full list',
  'kbStruct.diag.dupTable': 'Duplicate table id {id}; kept as a separate table',
}
