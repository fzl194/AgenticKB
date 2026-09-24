import { nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { useTreeFilterSync } from '@/composables/useTreeFilterSync'

describe('useTreeFilterSync', () => {
  it('reapplies the current filter when tree data is replaced', async () => {
    const filterText = ref('告警')
    const treeData = ref<unknown[]>([{ path: 'old' }])
    const filter = vi.fn()
    const stop = useTreeFilterSync(filterText, treeData, () => ({ filter }))

    treeData.value = [{ path: 'new' }]
    await nextTick()
    await nextTick()

    expect(filter).toHaveBeenLastCalledWith('告警')
    stop()
  })
})
