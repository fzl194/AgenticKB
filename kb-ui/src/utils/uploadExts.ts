/**
 * 上传扩展名单一事实源（S4 文件格式支持补全，68号报告 §六.S4）.
 *
 * 三个消费点共享本常量，禁止再手写第二份清单：
 * 1. KbFileManager.vue 的 el-upload accept（文件选择器过滤）
 * 2. handleUpload 前的扩展名白名单校验（绕过 accept 选"所有文件"时拦截）
 * 3. i18n 格式提示文案（kbManage.files.formats，由对账闸门保证与
 *    SUPPORTED_UPLOAD_EXTS 一致——见 locales 测试）
 *
 * 与后端真实能力逐字对齐（解析链判定 = safe_intake/inspect/router/
 * parser 四环）：文本族 md/txt/html、文档 pdf/doc/docx、表格
 * xlsx/xls/csv、演示 pptx/ppt（S1 旧格式转换）、结构化 json、图片
 * png/jpg/jpeg（S3 VLM 解析）、归档 zip/chm/hdx（解压成多文档）。
 * rtf/gif 等后端不解析的格式**故意不收**，避免"能上传必挖失败"。
 */
export const SUPPORTED_UPLOAD_EXTS: readonly string[] = [
  '.md', '.markdown', '.txt', '.html', '.htm',
  '.pdf',
  '.doc', '.docx',
  '.xlsx', '.xls', '.csv',
  '.pptx', '.ppt',
  '.json',
  '.png', '.jpg', '.jpeg',
  '.zip', '.chm', '.hdx',
] as const

/** 归档族（上传走解压通道而非单文档通道）。 */
export const ARCHIVE_UPLOAD_EXTS: readonly string[] = ['.zip', '.chm', '.hdx'] as const

/** accept 属性值（el-upload / 原生 input）。 */
export const UPLOAD_ACCEPT: string = SUPPORTED_UPLOAD_EXTS.join(',')

/** 白名单校验：扩展名不在支持清单 → 拦截提示文案所需的判定。 */
export function isSupportedUploadFile(name: string): boolean {
  const lower = name.toLowerCase()
  return SUPPORTED_UPLOAD_EXTS.some((ext) => lower.endsWith(ext))
}

/** 归档判定（保持与后端上传通道分流一致）。 */
export function isArchiveUploadFile(name: string): boolean {
  const lower = name.toLowerCase()
  return ARCHIVE_UPLOAD_EXTS.some((ext) => lower.endsWith(ext))
}
