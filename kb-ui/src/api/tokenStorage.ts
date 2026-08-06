/**
 * Token 持久化（叶模块，零依赖）。
 *
 * 单独成模块是为了打断 axios 拦截器 ↔ auth store ↔ api/auth ↔ proxyClient 的循环依赖：
 * proxyClient 的拦截器只 import 本叶模块读写 token，不 import store。
 * store 启动期 restore() 也读本模块；401 时拦截器清 token + 跳登录，store 在下次加载时自然未认证。
 */
const TOKEN_KEY = 'kb-token'

export function loadToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function saveToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* localStorage 不可用（隐私模式等）—— 忽略，token 仅存内存 */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}
