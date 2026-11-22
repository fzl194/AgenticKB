import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// 与同目录 KbReplaceDocumentDialog.spec.ts 相同的 mock 面（element-plus 的部分 mock 是安全的：
// locales/element.ts 对 provideGlobalConfig 的使用是惰性的）
vi.mock('@/api/kb', () => ({ useKbApi: () => ({ replaceDocumentContent: vi.fn() }) }))
vi.mock('@/api/proxyClient', () => ({ apiErrorDetail: async () => '上传失败' }))
vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn(), error: vi.fn() } }))
import KbReplaceDocumentDialog from '../KbReplaceDocumentDialog.vue'
import { useLocaleStore } from '@/stores/locale'

const document = { id: 'doc-1', document_name: 'original.md', content_revision: 4 }
const props = { kbId: 'kb-1', document, canWrite: true }

afterEach(() => localStorage.removeItem('kb-ui-lang'))

describe('file replacement dialog language', () => {
  // 顺序铁律：全局键先于 pinia 创建写入——store 在创建时读它
  beforeEach(() => {
    localStorage.setItem('kb-ui-lang', 'en')
    setActivePinia(createPinia())
  })

  it('renders English when the language is en', () => {
    const wrapper = mount(KbReplaceDocumentDialog, { props })
    expect(wrapper.text()).toContain('the file name and its directory stay unchanged')
    expect(wrapper.text()).toContain('Select a new file')
    expect(wrapper.get('[data-testid="replace-confirm"]').text()).toContain('Confirm Replacement')
    expect(wrapper.text()).not.toContain('确认替换')
    wrapper.unmount()
  })

  it('follows a live language switch (t() called during render is reactive)', async () => {
    // 本用例从中文起步：先清掉 beforeEach 写入的 en，再创建 store（store 在创建时读全局键）
    localStorage.removeItem('kb-ui-lang')
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(KbReplaceDocumentDialog, { props, global: { plugins: [pinia] } })
    expect(wrapper.get('[data-testid="replace-confirm"]').text()).toContain('确认替换')
    useLocaleStore().setLang('en')
    await nextTick()
    expect(wrapper.get('[data-testid="replace-confirm"]').text()).toContain('Confirm Replacement')
    useLocaleStore().setLang('zh')
    await nextTick()
    expect(wrapper.get('[data-testid="replace-confirm"]').text()).toContain('确认替换')
    wrapper.unmount()
  })
})
