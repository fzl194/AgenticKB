import { beforeEach, describe, expect, it, vi } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
const auth = vi.hoisted(() => ({
  siteRole: 'member',
  user: { username: 'alice', display_name: 'Alice', site_role: 'member' },
  logout: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ name: 'dashboard' }),
  useRouter: () => router,
}))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => auth }))

import Header from '../Header.vue'

describe('Header logout navigation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('replaces history so Back cannot reopen an authenticated route', async () => {
    const wrapper = shallowMount(Header)

    await (wrapper.vm as unknown as {
      onAccountCommand: (command: string) => Promise<void>
    }).onAccountCommand('logout')

    expect(auth.logout).toHaveBeenCalledOnce()
    expect(router.replace).toHaveBeenCalledWith('/login')
    expect(router.push).not.toHaveBeenCalled()
  })
})
