import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { DomainInfo } from '@/types'
import { useControlPlaneApi } from '@/api/controlPlane'

const STORAGE_KEY_PREFIX = 'kb-ui-current-domain:'

function storageKey(username: string): string {
  return `${STORAGE_KEY_PREFIX}${username}`
}

export const useDomainStore = defineStore('domain', () => {
  const domains = ref<DomainInfo[]>([])
  const currentDomain = ref('')
  const loading = ref(false)
  const loaded = ref(false)
  const loadedForUser = ref('')
  const error = ref('')
  let requestVersion = 0
  let inFlightUser = ''
  let inFlightPromise: Promise<void> | null = null

  const currentDomainInfo = computed<DomainInfo | undefined>(() =>
    domains.value.find(d => d.domain_id === currentDomain.value)
  )

  const activeDomains = computed<string[]>(() =>
    domains.value.filter(d => d.enabled).map(d => d.domain_id)
  )

  const enabledDomains = computed<DomainInfo[]>(() =>
    domains.value.filter(d => d.enabled)
  )

  function fetchDomains(username = loadedForUser.value): Promise<void> {
    const userKey = username.trim()
    if (!userKey) return Promise.resolve()
    if (loaded.value && loadedForUser.value === userKey) return Promise.resolve()
    if (inFlightPromise && inFlightUser === userKey) return inFlightPromise

    const promise = loadDomains(userKey)
    inFlightUser = userKey
    inFlightPromise = promise
    void promise.finally(() => {
      if (inFlightPromise === promise) {
        inFlightUser = ''
        inFlightPromise = null
      }
    })
    return promise
  }

  async function loadDomains(userKey: string): Promise<void> {
    const version = ++requestVersion
    domains.value = []
    currentDomain.value = ''
    loaded.value = false
    loadedForUser.value = ''
    loading.value = true
    error.value = ''
    try {
      const api = useControlPlaneApi()
      const fetchedDomains = await api.getDomains()
      if (version !== requestVersion) return

      domains.value = fetchedDomains
      loadedForUser.value = userKey
      loaded.value = true

      const preferredDomain = localStorage.getItem(storageKey(userKey)) || ''
      const isValid = fetchedDomains.some(
        d => d.domain_id === preferredDomain && d.enabled
      )
      const selectedDomain = isValid
        ? preferredDomain
        : fetchedDomains.find(d => d.enabled)?.domain_id || ''
      currentDomain.value = selectedDomain
      if (selectedDomain) {
        localStorage.setItem(storageKey(userKey), selectedDomain)
      }
    } catch (err) {
      if (version !== requestVersion) return
      domains.value = []
      currentDomain.value = ''
      loaded.value = false
      loadedForUser.value = ''
      error.value = err instanceof Error ? err.message : 'Failed to load domains'
    } finally {
      if (version === requestVersion) loading.value = false
    }
  }

  function switchDomain(domainId: string) {
    if (domains.value.some(d => d.domain_id === domainId && d.enabled)) {
      currentDomain.value = domainId
      if (loadedForUser.value) {
        localStorage.setItem(storageKey(loadedForUser.value), domainId)
      }
    }
  }

  async function refreshDomains(username = loadedForUser.value) {
    loaded.value = false
    await fetchDomains(username)
  }

  function resetDomains() {
    requestVersion += 1
    inFlightUser = ''
    inFlightPromise = null
    domains.value = []
    currentDomain.value = ''
    loading.value = false
    loaded.value = false
    loadedForUser.value = ''
    error.value = ''
  }

  return {
    domains,
    currentDomain,
    currentDomainInfo,
    activeDomains,
    enabledDomains,
    loading,
    loaded,
    loadedForUser,
    error,
    fetchDomains,
    switchDomain,
    refreshDomains,
    resetDomains,
  }
})
