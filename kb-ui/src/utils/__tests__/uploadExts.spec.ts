import { describe, expect, it } from 'vitest'
import { zh } from '../../locales/zh'
import { en } from '../../locales/en'
import {
  ARCHIVE_UPLOAD_EXTS,
  SUPPORTED_UPLOAD_EXTS,
  UPLOAD_ACCEPT,
  isArchiveUploadFile,
  isSupportedUploadFile,
} from '../uploadExts'

// S4 单一事实源对账闸门（68号报告 §六.S4）：前端三处消费
// utils/uploadExts.ts，不再允许第二份手写清单。
// - accept 与常量逐字一致（KbFileManager.vue 使用 UPLOAD_ACCEPT 绑定）
// - i18n 文案与常量族对齐（文本/HTML/PDF/Word/Excel/PPT/CSV/JSON/图片/归档）
// - 白名单判定覆盖后端真实能力集（S1 xls/ppt、S2 csv/json、S3 图片）

const MANAGER_SOURCE: Record<string, string> = import.meta.glob(
  ['@/components/kb/*.vue'],
  { eager: true, query: '?raw', import: 'default' },
) as Record<string, string>
const managerVue = MANAGER_SOURCE['/src/components/kb/KbFileManager.vue'] ?? ''

describe('upload extension single source of truth', () => {
  it('KbFileManager binds accept from the shared constant, not a hand-written list', () => {
    expect(managerVue).toContain(':accept="UPLOAD_ACCEPT"')
    // 手写的 accept= 字符串清单不允许回潮
    expect(managerVue).not.toMatch(/accept="\.md/)
    // 上传前校验挂进 handleUpload
    expect(managerVue).toContain('isSupportedUploadFile(file.name)')
    expect(managerVue).toContain("t('kbManage.files.unsupportedExt'")
  })

  it('no second hand-written extension list in the manager', () => {
    // 旧的 ARCHIVE_EXTS 本地常量已上收
    expect(managerVue).not.toContain('const ARCHIVE_EXTS')
    expect(managerVue).toContain('isArchiveUploadFile(file.name)')
  })

  it('accept string is exactly the supported list', () => {
    expect(UPLOAD_ACCEPT).toBe(SUPPORTED_UPLOAD_EXTS.join(','))
    expect(UPLOAD_ACCEPT).toContain('.csv')
    expect(UPLOAD_ACCEPT).toContain('.ppt')
    expect(UPLOAD_ACCEPT).toContain('.json')
    expect(UPLOAD_ACCEPT).toContain('.png')
  })

  it('validation and archive split cover the backend capability set', () => {
    // S1-S3 新能力全部放行
    for (const name of ['a.xls', 'b.ppt', 'c.csv', 'd.json', 'e.png', 'f.jpg', 'g.jpeg']) {
      expect(isSupportedUploadFile(name), name).toBe(true)
    }
    // 后端不解析的死胡同格式拦截（rtf/gif 及无扩展名）
    for (const name of ['a.rtf', 'b.gif', 'c.rar', 'd.exe', 'e', 'f.csvx']) {
      expect(isSupportedUploadFile(name), name).toBe(false)
    }
    // 归档族分流
    for (const name of ['a.zip', 'b.chm', 'c.hdx']) {
      expect(isArchiveUploadFile(name), name).toBe(true)
    }
    expect(isArchiveUploadFile('a.docx')).toBe(false)
    expect(ARCHIVE_UPLOAD_EXTS.every(ext => SUPPORTED_UPLOAD_EXTS.includes(ext))).toBe(true)
  })

  it('i18n format hints match the capability families (zh and en)', () => {
    const zhFormats = zh['kbManage.files.formats'] ?? ''
    expect(zhFormats).toContain('.csv')
    expect(zhFormats).toContain('.ppt')
    expect(zhFormats).toContain('JSON')
    expect(zhFormats).toContain('.png')
    // .xls 虚假宣传时代的旧文案不含"Excel（.xls/.xlsx）"旧序
    expect(zhFormats).not.toBe('支持 Markdown、文本、HTML、PDF、Word（.doc/.docx）、Excel（.xls/.xlsx）及 ZIP 等格式')
    const enFormats = en['kbManage.files.formats'] ?? ''
    expect(enFormats).toContain('.csv')
    expect(enFormats).toContain('.ppt')
    expect(enFormats).toContain('JSON')
    expect(enFormats).toContain('.png')
    // 新增拦截文案 key 双语齐备
    expect(zh['kbManage.files.unsupportedExt']).toBeTruthy()
    expect(en['kbManage.files.unsupportedExt']).toBeTruthy()
  })
})
