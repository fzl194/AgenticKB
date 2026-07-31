/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CONTROL_PLANE_API_BASE: string
  readonly VITE_KB_DEFAULT_USER?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
