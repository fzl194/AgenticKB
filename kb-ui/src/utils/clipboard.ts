/**
 * navigator.clipboard 只在安全上下文（https / localhost）里存在。
 * 部署后前端走 http://<ip>，整个 clipboard API 是 undefined，所以要回落到
 * 老的 execCommand('copy') 选区方案。
 */
function execCommandCopy(text: string): boolean {
  const textarea = document.createElement('textarea')
  textarea.value = text
  // 不能用 display:none / visibility:hidden，否则选不中；挪到视口外即可。
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '-9999px'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)

  try {
    textarea.select()
    textarea.setSelectionRange(0, textarea.value.length)
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    textarea.remove()
  }
}

/** 复制文本到剪贴板，返回是否成功。 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限被拒 / 非安全上下文的实现差异，继续走回落。
    }
  }
  return execCommandCopy(text)
}
