import { ref, onMounted, onUnmounted } from 'vue'

export function usePolling(
  fn: () => Promise<void>,
  intervalMs: number,
  options?: { immediate?: boolean; shouldRun?: () => boolean },
) {
  const isPolling = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null

  async function poll() {
    if (isPolling.value || options?.shouldRun?.() === false) return
    isPolling.value = true
    try {
      await fn()
    } finally {
      isPolling.value = false
    }
  }

  function start() {
    stop()
    if (options?.immediate !== false) void poll()
    timer = setInterval(poll, intervalMs)
  }

  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onMounted(() => start())
  onUnmounted(() => stop())

  return { isPolling, start, stop }
}
