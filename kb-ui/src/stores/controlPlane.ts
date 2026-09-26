import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { useControlPlaneApi } from '@/api/controlPlane'
import type { LogContent, LogFileInfo, LogQuery } from '@/api/controlPlane'

export const useControlPlaneStore = defineStore('control-plane', () => {
  const api = useControlPlaneApi()

  // ── System config ──
  const systemConfigNames = ref<string[]>([])
  const selectedSystemConfigName = ref('')
  const systemConfigText = ref('')
  const systemConfigOriginal = ref('')

  // ── Domains ──
  const domains = ref<{ domain_id: string; display_name: string; enabled: boolean }[]>([])
  const selectedDomainId = ref('')
  const domainYamlText = ref('')
  const domainYamlOriginal = ref('')

  // ── Scenario ──
  const scenarioYamlText = ref('')
  const scenarioYamlOriginal = ref('')

  // ── Shared ──
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')

  // ── Service logs ──
  const logFiles = ref<LogFileInfo[]>([])
  const logDir = ref('')
  const selectedLogName = ref('')
  const logContent = ref<LogContent | null>(null)
  const logLoading = ref(false)

  const systemConfigDirty = computed(() => systemConfigText.value !== systemConfigOriginal.value)
  const domainYamlDirty = computed(() => domainYamlText.value !== domainYamlOriginal.value)
  const scenarioDirty = computed(() => scenarioYamlText.value !== scenarioYamlOriginal.value)

  const selectedDomainSummary = computed(() =>
    domains.value.find(d => d.domain_id === selectedDomainId.value) ?? null
  )

  // ── System config actions ──
  async function loadSystemConfigs() {
    loading.value = true
    error.value = ''
    try {
      systemConfigNames.value = await api.listSystemConfigs()
      if (!selectedSystemConfigName.value && systemConfigNames.value.length > 0) {
        await selectSystemConfig(systemConfigNames.value[0])
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load system configs'
    } finally {
      loading.value = false
    }
  }

  async function selectSystemConfig(name: string) {
    selectedSystemConfigName.value = name
    loading.value = true
    try {
      const text = await api.getSystemConfigRaw(name)
      systemConfigText.value = text
      systemConfigOriginal.value = text
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load config'
    } finally {
      loading.value = false
    }
  }

  async function saveSystemConfig() {
    if (!selectedSystemConfigName.value) return
    saving.value = true
    try {
      await api.updateSystemConfigRaw(selectedSystemConfigName.value, systemConfigText.value)
      systemConfigOriginal.value = systemConfigText.value
    } finally {
      saving.value = false
    }
  }

  // ── Domain actions ──
  async function loadDomains() {
    loading.value = true
    error.value = ''
    try {
      domains.value = await api.getDomains()
      if (!selectedDomainId.value && domains.value.length > 0) {
        await selectDomain(domains.value[0].domain_id)
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load domains'
    } finally {
      loading.value = false
    }
  }

  async function selectDomain(domainId: string) {
    selectedDomainId.value = domainId
    loading.value = true
    try {
      const [domainText, scenarioText] = await Promise.all([
        api.getDomainRaw(domainId),
        api.getScenarioRaw(domainId),
      ])
      domainYamlText.value = domainText
      domainYamlOriginal.value = domainText
      scenarioYamlText.value = scenarioText
      scenarioYamlOriginal.value = scenarioText
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load domain'
    } finally {
      loading.value = false
    }
  }

  async function createDomain(domainId: string) {
    await api.createDomain(domainId)
    await loadDomains()
    await selectDomain(domainId)
  }

  async function saveDomainYaml() {
    if (!selectedDomainId.value) return
    saving.value = true
    try {
      await api.updateDomainRaw(selectedDomainId.value, domainYamlText.value)
      domainYamlOriginal.value = domainYamlText.value
    } finally {
      saving.value = false
    }
  }

  async function deleteDomain(domainId: string) {
    await api.deleteDomain(domainId)
    if (selectedDomainId.value === domainId) {
      selectedDomainId.value = ''
      domainYamlText.value = ''
      domainYamlOriginal.value = ''
      scenarioYamlText.value = ''
      scenarioYamlOriginal.value = ''
    }
    await loadDomains()
  }

  // ── Scenario actions ──
  async function saveScenarioYaml() {
    if (!selectedDomainId.value) return
    saving.value = true
    try {
      await api.updateScenarioRaw(selectedDomainId.value, scenarioYamlText.value)
      scenarioYamlOriginal.value = scenarioYamlText.value
    } finally {
      saving.value = false
    }
  }

  // ── Service log actions ──
  async function loadLogFiles() {
    try {
      const result = await api.listLogs()
      logFiles.value = result.files
      logDir.value = result.log_dir
      // 首次进入自动选中第一个，省一次点击
      if (!selectedLogName.value && result.files.length) {
        selectedLogName.value = result.files[0].name
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : '读取日志列表失败'
    }
  }

  async function loadLogContent(query: LogQuery = {}) {
    if (!selectedLogName.value) return
    logLoading.value = true
    try {
      logContent.value = await api.readLog(selectedLogName.value, query)
    } catch (err) {
      logContent.value = null
      error.value = err instanceof Error ? err.message : '读取日志失败'
    } finally {
      logLoading.value = false
    }
  }

  return {
    systemConfigNames,
    selectedSystemConfigName,
    systemConfigText,
    systemConfigOriginal,
    domains,
    selectedDomainId,
    selectedDomainSummary,
    domainYamlText,
    domainYamlOriginal,
    scenarioYamlText,
    scenarioYamlOriginal,
    loading,
    saving,
    error,
    systemConfigDirty,
    domainYamlDirty,
    scenarioDirty,
    loadSystemConfigs,
    selectSystemConfig,
    saveSystemConfig,
    loadDomains,
    selectDomain,
    createDomain,
    saveDomainYaml,
    deleteDomain,
    saveScenarioYaml,
    logFiles,
    logDir,
    selectedLogName,
    logContent,
    logLoading,
    loadLogFiles,
    loadLogContent,
  }
})
