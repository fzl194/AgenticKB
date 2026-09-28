import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'

import { usePolling } from '@/composables/usePolling'

describe('usePolling session guard', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not invoke the poll callback while its guard is false', async () => {
    vi.useFakeTimers()
    const allowed = ref(false)
    const poll = vi.fn().mockResolvedValue(undefined)
    const wrapper = mount(defineComponent({
      setup() {
        usePolling(poll, 1000, { shouldRun: () => allowed.value })
        return () => null
      },
    }))
    await nextTick()

    await vi.advanceTimersByTimeAsync(2500)
    expect(poll).not.toHaveBeenCalled()

    allowed.value = true
    await vi.advanceTimersByTimeAsync(1000)
    expect(poll).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
