import { describe, expect, it } from 'vitest'
import { createApp, h, nextTick } from 'vue'
// 注意：ElementPlus 只有 default 导出（命名导入得到 undefined → install 不跑 →
// 弹窗回落英文默认）。main.ts 同为 default 导入。
import ElementPlus, { ElMessageBox } from 'element-plus'
import { applyAppLocale, epLocale } from '../element'

/**
 * 脱挂通道 spike（spec §3.4）：ElMessageBox 挂在 document.body 下、不在
 * el-config-provider 子树内，语言只随 app 级 provides 走。
 * provideGlobalConfig 是 element-plus 导出但未文档化的 API——本测试即
 * element-plus 升级的绊网：若行为变化，这里先红。
 * 注意 confirm().catch：close() 会以 'close' 拒绝 promise（messageBox.mjs:54-55），
 * 不捕获会成为 unhandled rejection 直接红掉 vitest。
 */
describe('element locale bridge (detached channel spike)', () => {
  it('ElMessageBox buttons follow applyAppLocale after toggle', async () => {
    const app = createApp({ render: () => h('div') })
    app.use(ElementPlus, { locale: epLocale('zh') })
    app.mount(document.createElement('div'))

    // 切换前：脱挂弹窗取安装时语言
    ElMessageBox.confirm('spike', 'spike').catch(() => {})
    await nextTick()
    await nextTick()
    expect(document.body.textContent).toContain('确定')
    ElMessageBox.close()

    // 重注入 app 级 locale（模拟运行时切到 EN）
    applyAppLocale(app, 'en')

    ElMessageBox.confirm('spike', 'spike').catch(() => {})
    await nextTick()
    await nextTick()
    expect(document.body.textContent).toContain('OK')
    expect(document.body.textContent).toContain('Cancel')
    ElMessageBox.close()
  })
})
