/**
 * PreprocessNotice 的 EN 渲染断言（四区英化 Task 9 / 规则 R11）。
 *
 * 挂载代码与同目录 PreprocessNotice.spec.ts 相同（无需 mock），区别只在语言：
 * 全局语言键必须在 pinia 创建**之前**写入——locale store 在实例化时读它。
 * 装进插件的 pinia 与活动 pinia 是同一个实例（否则 t() 读不到切换）。
 *
 * 夹具数据一律 ASCII：EN 态下整块界面的文本里出现任何 CJK 字符都算泄漏。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { Pinia } from 'pinia'

import PreprocessNotice from '../PreprocessNotice.vue'

enableAutoUnmount(afterEach)

/** 任何 CJK 字符都算泄漏：EN 态下整块界面的文本由字典产出，不该残留中文。 */
const CJK = /[一-龥]/

let pinia: Pinia

// 顺序铁律：全局键先于 pinia 写入——store 在创建时读它
beforeEach(() => {
  localStorage.setItem('kb-ui-lang', 'en')
  pinia = createPinia()
  setActivePinia(pinia)
})
afterEach(() => localStorage.removeItem('kb-ui-lang'))

describe('PreprocessNotice (EN)', () => {
  it('renders the partial title, summary labels and warning location in English', () => {
    const wrapper = mount(PreprocessNotice, {
      props: {
        status: 'partial',
        warnings: [{
          code: 'excel_formula_cache_missing',
          message: 'Formula has no saved cached result',
          sheet_name: 'Summary',
          cell_range: 'F18',
        }],
        summary: { sheet_count: 2, parsed_sheet_count: 2, table_region_count: 4 },
      },
      global: { plugins: [pinia] },
    })

    expect(wrapper.find('.preprocess-notice__title').text()).toBe('Partially Parsed')
    expect(wrapper.text()).toContain('Worksheets')
    expect(wrapper.text()).toContain('Table Regions')
    // 全角括号随语言切成半角；分隔符 / 与单元格地址原样
    expect(wrapper.find('.preprocess-notice__location').text()).toBe('(Summary / F18)')
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('renders the failed title and the LibreOffice action hint in English', () => {
    const wrapper = mount(PreprocessNotice, {
      props: {
        status: 'failed',
        errorCode: 'doc_converter_unavailable',
        errorDetail: 'No .doc converter is available',
      },
      global: { plugins: [pinia] },
    })

    expect(wrapper.find('.preprocess-notice__title').text()).toBe('Parsing Failed')
    expect(wrapper.find('.preprocess-notice__action').text()).toBe(
      'Install LibreOffice offline in the Linux environment, or convert the file to .docx and upload it again.',
    )
    expect(wrapper.text()).not.toMatch(CJK)
  })

  it('renders the password hint and every summary label in English', () => {
    const wrapper = mount(PreprocessNotice, {
      props: {
        status: 'success',
        errorCode: 'excel_password_protected',
        errorDetail: 'encrypted',
        summary: {
          sheet_count: 3, parsed_sheet_count: 2, skipped_empty_sheet_count: 1,
          table_region_count: 5, nonempty_cell_count: 42,
        },
      },
      global: { plugins: [pinia] },
    })

    expect(wrapper.find('.preprocess-notice__title').text()).toBe('Preprocessing Succeeded')
    expect(wrapper.find('.preprocess-notice__action').text()).toBe(
      'Password-protected Excel files are not supported yet; remove the password and upload the file again.',
    )
    expect(
      wrapper.findAll('.preprocess-notice__summary span').map(item => item.text().replace(/\s+/g, ' ').trim()),
    ).toEqual([
      'Worksheets 3', 'Parsed 2', 'Empty Worksheets 1', 'Table Regions 5', 'Non-empty Cells 42',
    ])
    expect(wrapper.text()).not.toMatch(CJK)
  })
})
