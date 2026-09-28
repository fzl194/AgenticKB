import { apiErrorDetail } from '@/api/proxyClient'

const STABLE_MESSAGES: Record<string, string> = {
  username_reserved: '该用户名已被已删除账号保留，请恢复原账号',
  user_deleted: '用户已删除，请先恢复账号',
  user_not_active: '用户未启用，无法执行该操作',
  last_active_admin: '至少保留一个启用的系统管理员',
  cannot_delete_self: '不能删除自己的账号',
  user_owns_active_kbs: '该用户仍拥有知识库，请先转移所有者',
  owner_target_not_domain_bound: '新所有者必须是已启用且绑定该知识域的用户',
  domain_grants_must_not_be_empty: '启用的普通用户至少需要一个知识域',
  domain_admin_requires_password: '设为域管理员前必须设置密码',
  cannot_manage_domain_admin: '域管理员不能修改或移除其他域管理员',
  mcp_key_must_be_revoked_before_delete: '请先吊销钥匙，再删除记录',
  invalid_member_role: '知识库成员角色只能是编辑者或只读用户',
  import_validation_failed: '导入校验失败，请修正错误行后重试',
}

const CODE_ALIASES: Record<string, string> = {
  user_owns_knowledge_bases: 'user_owns_active_kbs',
  domain_has_owned_kbs: 'user_owns_active_kbs',
  domains_must_not_be_empty: 'domain_grants_must_not_be_empty',
  import_user_not_active: 'user_not_active',
  '至少保留一个启用的管理员': 'last_active_admin',
  '不能删除自己的账号': 'cannot_delete_self',
  '请先吊销钥匙，再删除记录': 'mcp_key_must_be_revoked_before_delete',
}

const CURRENT_MESSAGES: Record<string, string> = {
  '至少保留一个启用的管理员': STABLE_MESSAGES.last_active_admin,
  '不能删除自己的账号': STABLE_MESSAGES.cannot_delete_self,
  '请先吊销钥匙，再删除记录': STABLE_MESSAGES.mcp_key_must_be_revoked_before_delete,
  confirm_username_mismatch: '输入的用户名与待删除账号不一致',
  password_changed_concurrently: '用户密码状态已变化，请刷新后重试',
  initial_password_not_required: '该用户已有密码，无需再次设置初始密码',
  new_owner_has_same_name_kb: '新所有者在当前域已有同名知识库',
  owner_target_not_found: '新所有者不存在',
  owner_target_inactive: '新所有者未启用或已删除',
  owner_previous_inactive: '原所有者未启用，不能保留为编辑者',
  owner_transfer_actor_forbidden: '你没有转移该知识库所有者的权限',
  user_not_found: '用户不存在或已删除',
  domain_membership_not_found: '用户不属于当前知识域',
  domain_admin_required: '需要当前知识域管理员权限',
  site_admin_does_not_require_domain_grant: '系统管理员默认全域通行，无需分配域权限',
  invalid_domain: '知识域不存在或未启用',
  domain_not_bound: '未绑定该知识域，请联系管理员分配',
  import_changed_since_preview: '预检后用户数据已变化，请重新预检',
  file_too_large: '文件超过 5 MB 上限',
  unsupported_file_type: '仅支持 CSV 或 XLSX 文件',
  empty_import: '导入文件没有数据',
  missing_required_columns: '导入文件缺少必需列',
  too_many_rows: '导入文件超过 5,000 行上限',
  formula_not_allowed: '导入字段不能包含公式',
  csv_must_be_utf8: 'CSV 文件必须使用 UTF-8 编码',
  invalid_xlsx: 'XLSX 文件无效或已损坏',
  xlsx_archive_limits_exceeded: 'XLSX 文件解压规模超过安全限制',
  username_required: '用户名不能为空',
  conflicting_display_name: '同一用户名存在冲突的显示名',
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function stableCode(code: string): string | null {
  const trimmed = code.trim()
  if (STABLE_MESSAGES[trimmed]) return trimmed
  if (CURRENT_MESSAGES[trimmed]) return trimmed
  const alias = CODE_ALIASES[trimmed]
  if (alias) return alias
  if (trimmed.startsWith('invalid_domain:')) return 'invalid_domain'
  if (trimmed.startsWith('domain_not_bound:')) return 'domain_not_bound'
  return null
}

function codeFromValue(value: unknown): string | null {
  if (typeof value === 'string') {
    const text = value.trim()
    if (!text) return null
    const direct = stableCode(text)
    if (direct) return direct
    try {
      return codeFromValue(JSON.parse(text))
    } catch {
      return null
    }
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      const record = asRecord(item)
      const location = record?.loc
      if (
        record?.type === 'literal_error'
        && Array.isArray(location)
        && location.at(-1) === 'role'
      ) {
        return 'invalid_member_role'
      }
      const nested = codeFromValue(item)
      if (nested) return nested
    }
    return null
  }

  const record = asRecord(value)
  if (!record) return null
  if (typeof record.code === 'string') {
    const code = stableCode(record.code)
    if (code) return code
  }
  return codeFromValue(record.detail)
    ?? codeFromValue(record.error)
    ?? codeFromValue(record.errors)
    ?? codeFromValue(record.message)
}

function responseData(error: unknown): unknown {
  return asRecord(error)?.response
    ? asRecord(asRecord(error)?.response)?.data
    : undefined
}

export function managementErrorCodeMessage(code: string): string {
  const trimmed = code.trim()
  const normalized = stableCode(trimmed)
  if (normalized) {
    return STABLE_MESSAGES[normalized] ?? CURRENT_MESSAGES[normalized] ?? normalized
  }
  return CURRENT_MESSAGES[trimmed] ?? trimmed
}

export async function managementErrorMessage(
  error: unknown,
  fallback = '请求失败',
): Promise<string> {
  const code = codeFromValue(responseData(error))
  if (code) return managementErrorCodeMessage(code)

  const detail = await apiErrorDetail(error)
  const translated = managementErrorCodeMessage(detail)
  if (translated !== detail) return translated
  return detail && detail !== '请求失败' ? detail : fallback
}
