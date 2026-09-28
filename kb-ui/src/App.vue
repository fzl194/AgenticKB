<template>
  <router-view />
</template>

<script setup lang="ts">
import { watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

// A storage event can end the session in another tab without a click or route
// change in this tab. Replace avoids leaving a protected page in browser history.
watch(() => auth.loggingOut, (loggingOut) => {
  if (loggingOut) void router.replace('/login')
}, { immediate: true })

watch(() => auth.externalSessionGeneration, async () => {
  await auth.ready
  if (auth.isAuthenticated) await router.replace('/')
})
</script>
