import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick, ref } from 'vue'

const router = vi.hoisted(() => ({ replace: vi.fn() }))
const state = vi.hoisted(() => ({
  loggingOut: null as ReturnType<typeof ref<boolean>> | null,
  externalSessionGeneration: null as ReturnType<typeof ref<number>> | null,
  authenticated: false,
  ready: Promise.resolve(),
}))

vi.mock('vue-router', () => ({ useRouter: () => router }))
vi.mock('@/stores/auth', async () => {
  const { ref: vueRef } = await import('vue')
  state.loggingOut = vueRef(false)
  state.externalSessionGeneration = vueRef(0)
  return {
    useAuthStore: () => ({
      get loggingOut() { return state.loggingOut!.value },
      get externalSessionGeneration() { return state.externalSessionGeneration!.value },
      get isAuthenticated() { return state.authenticated },
      get ready() { return state.ready },
    }),
  }
})

import App from '@/App.vue'

describe('App session synchronization', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.loggingOut!.value = false
    state.externalSessionGeneration!.value = 0
    state.authenticated = false
    state.ready = Promise.resolve()
  })

  it('replaces the current route when another tab ends the session', async () => {
    mount(App, { global: { stubs: { RouterView: true } } })

    state.loggingOut!.value = true
    await nextTick()

    expect(router.replace).toHaveBeenCalledWith('/login')
  })

  it('reruns navigation after another tab replaces the authenticated identity', async () => {
    mount(App, { global: { stubs: { RouterView: true } } })
    state.authenticated = true

    const externalGeneration = state.externalSessionGeneration!
    externalGeneration.value = (externalGeneration.value ?? 0) + 1
    await flushPromises()

    expect(router.replace).toHaveBeenCalledWith('/')
  })
})
