import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PreprocessNotice from '../PreprocessNotice.vue'

describe('PreprocessNotice', () => {
  it('shows partial Excel warnings and source locations', () => {
    const wrapper = mount(PreprocessNotice, { props: {
      status: 'partial',
      warnings: [{
        code: 'excel_formula_cache_missing',
        message: '公式没有已保存的计算结果',
        sheet_name: '汇总',
        cell_range: 'F18',
      }],
      summary: { sheet_count: 2, parsed_sheet_count: 2, table_region_count: 4 },
    } })

    expect(wrapper.text()).toContain('部分解析成功')
    expect(wrapper.text()).toContain('汇总')
    expect(wrapper.text()).toContain('F18')
    expect(wrapper.text()).toContain('4')
  })

  it('shows an actionable fatal error', () => {
    const wrapper = mount(PreprocessNotice, { props: {
      status: 'failed',
      errorCode: 'doc_converter_unavailable',
      errorDetail: 'No .doc converter is available',
    } })

    expect(wrapper.text()).toContain('解析失败')
    expect(wrapper.text()).toContain('LibreOffice')
  })
})
