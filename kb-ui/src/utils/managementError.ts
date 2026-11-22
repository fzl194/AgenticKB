import { apiErrorDetail } from '@/api/proxyClient'
import { t } from '@/locales/t'

const STABLE_KEYS: Record<string, string> = {
  username_reserved: 'common.mgmtError.username_reserved',
  user_deleted: 'common.mgmtError.user_deleted',
  user_not_active: 'common.mgmtError.user_not_active',
  last_active_admin: 'common.mgmtError.last_active_admin',
  cannot_delete_self: 'common.mgmtError.cannot_delete_self',
  user_owns_active_kbs: 'common.mgmtError.user_owns_active_kbs',
  owner_target_not_domain_bound: 'common.mgmtError.owner_target_not_domain_bound',
  domain_grants_must_not_be_empty: 'common.mgmtError.domain_grants_must_not_be_empty',
  domain_admin_requires_password: 'common.mgmtError.domain_admin_requires_password',
  cannot_manage_domain_admin: 'common.mgmtError.cannot_manage_domain_admin',
  mcp_key_must_be_revoked_before_delete: 'common.mgmtError.mcp_key_must_be_revoked_before_delete',
  invalid_member_role: 'common.mgmtError.invalid_member_role',
  import_validation_failed: 'common.mgmtError.import_validation_failed',
}

// 别名归一（原样保留）：中文键 = 后端 detail 文本的匹配数据，**不得翻译**
const CODE_ALIASES: Record<string, string> = {
  user_owns_knowledge_bases: 'user_owns_active_kbs',
  domain_has_owned_kbs: 'user_owns_active_kbs',
  domains_must_not_be_empty: 'domain_grants_must_not_be_empty',
  import_user_not_active: 'user_not_active',
  '至少保留一个启用的管理员': 'last_active_admin',
  '不能删除自己的账号': 'cannot_delete_self',
  '请先吊销钥匙，再删除记录': 'mcp_key_must_be_revoked_before_delete',
}

const CURRENT_KEYS: Record<string, string> = {
  '至少保留一个启用的管理员': STABLE_KEYS.last_active_admin,
  '不能删除自己的账号': STABLE_KEYS.cannot_delete_self,
  '请先吊销钥匙，再删除记录': STABLE_KEYS.mcp_key_must_be_revoked_before_delete,
  confirm_username_mismatch: 'common.mgmtError.confirm_username_mismatch',
  password_changed_concurrently: 'common.mgmtError.password_changed_concurrently',
  initial_password_not_required: 'common.mgmtError.initial_password_not_required',
  new_owner_has_same_name_kb: 'common.mgmtError.new_owner_has_same_name_kb',
  owner_target_not_found: 'common.mgmtError.owner_target_not_found',
  owner_target_inactive: 'common.mgmtError.owner_target_inactive',
  owner_previous_inactive: 'common.mgmtError.owner_previous_inactive',
  owner_transfer_actor_forbidden: 'common.mgmtError.owner_transfer_actor_forbidden',
  user_not_found: 'common.mgmtError.user_not_found',
  domain_membership_not_found: 'common.mgmtError.domain_membership_not_found',
  domain_admin_required: 'common.mgmtError.domain_admin_required',
  site_admin_does_not_require_domain_grant: 'common.mgmtError.site_admin_does_not_require_domain_grant',
  invalid_domain: 'common.mgmtError.invalid_domain',
  domain_not_bound: 'common.mgmtError.domain_not_bound',
  import_changed_since_preview: 'common.mgmtError.import_changed_since_preview',
  file_too_large: 'common.mgmtError.file_too_large',
  unsupported_file_type: 'common.mgmtError.unsupported_file_type',
  empty_import: 'common.mgmtError.empty_import',
  missing_required_columns: 'common.mgmtError.missing_required_columns',
  too_many_rows: 'common.mgmtError.too_many_rows',
  formula_not_allowed: 'common.mgmtError.formula_not_allowed',
  csv_must_be_utf8: 'common.mgmtError.csv_must_be_utf8',
  invalid_xlsx: 'common.mgmtError.invalid_xlsx',
  xlsx_archive_limits_exceeded: 'common.mgmtError.xlsx_archive_limits_exceeded',
  username_required: 'common.mgmtError.username_required',
  conflicting_display_name: 'common.mgmtError.conflicting_display_name',
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function stableCode(code: string): string | null {
  const trimmed = code.trim()
  if (STABLE_KEYS[trimmed]) return trimmed
  if (CURRENT_KEYS[trimmed]) return trimmed
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
    return t(STABLE_KEYS[normalized] ?? CURRENT_KEYS[normalized] ?? normalized)
  }
  return CURRENT_KEYS[trimmed] ? t(CURRENT_KEYS[trimmed]) : trimmed
}

export async function managementErrorMessage(
  error: unknown,
  fallback: string = t('common.requestFailed'),
): Promise<string> {
  const code = codeFromValue(responseData(error))
  if (code) return managementErrorCodeMessage(code)

  const detail = await apiErrorDetail(error)
  const translated = managementErrorCodeMessage(detail)
  if (translated !== detail) return translated
  return detail && detail !== t('common.requestFailed') ? detail : fallback
}
