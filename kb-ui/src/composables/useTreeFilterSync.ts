import { nextTick, watch } from 'vue'
import type { Ref, WatchSource } from 'vue'

interface FilterableTree {
  filter: (value: string) => void
}

/** 树数据替换后，在新节点挂载完成时重新应用当前过滤词。 */
export function useTreeFilterSync<T extends FilterableTree>(
  filterText: Ref<string>,
  treeData: WatchSource<unknown>,
  getTree: () => T | null,
) {
  return watch([filterText, treeData], async ([value]) => {
    await nextTick()
    getTree()?.filter(value as string)
  }, { flush: 'post' })
}
