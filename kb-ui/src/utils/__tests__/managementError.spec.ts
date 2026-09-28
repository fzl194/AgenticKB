import { beforeEach, describe, expect, it, vi } from 'vitest'

const proxy = vi.hoisted(() => ({ apiErrorDetail: vi.fn() }))

vi.mock('@/api/proxyClient', () => ({
  apiErrorDetail: proxy.apiErrorDetail,
}))

import {
  managementErrorCodeMessage,
  managementErrorMessage,
} from '@/utils/managementError'

const STABLE_CASES = [
  ['username_reserved', '该用户名已被已删除账号保留，请恢复原账号'],
  ['user_deleted', '用户已删除，请先恢复账号'],
  ['user_not_active', '用户未启用，无法执行该操作'],
  ['last_active_admin', '至少保留一个启用的系统管理员'],
  ['cannot_delete_self', '不能删除自己的账号'],
  ['user_owns_active_kbs', '该用户仍拥有知识库，请先转移所有者'],
  ['owner_target_not_domain_bound', '新所有者必须是已启用且绑定该知识域的用户'],
  ['domain_grants_must_not_be_empty', '启用的普通用户至少需要一个知识域'],
  ['domain_admin_requires_password', '设为域管理员前必须设置密码'],
  ['cannot_manage_domain_admin', '域管理员不能修改或移除其他域管理员'],
  ['mcp_key_must_be_revoked_before_delete', '请先吊销钥匙，再删除记录'],
  ['invalid_member_role', '知识库成员角色只能是编辑者或只读用户'],
  ['import_validation_failed', '导入校验失败，请修正错误行后重试'],
] as const

describe('managementErrorMessage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    proxy.apiErrorDetail.mockResolvedValue('后端原始错误')
  })

  it.each(STABLE_CASES)('maps stable code %s to Chinese', async (code, message) => {
    const error = { response: { data: { detail: code } } }
    await expect(managementErrorMessage(error)).resolves.toBe(message)
  })

  it('maps current structured aliases and Chinese backend messages', async () => {
    await expect(managementErrorMessage({
      response: { data: { detail: { code: 'user_owns_knowledge_bases' } } },
    })).resolves.toBe('该用户仍拥有知识库，请先转移所有者')

    await expect(managementErrorMessage({
      response: { data: { detail: '至少保留一个启用的管理员' } },
    })).resolves.toBe('至少保留一个启用的系统管理员')

    await expect(managementErrorMessage({
      response: { data: { detail: '请先吊销钥匙，再删除记录' } },
    })).resolves.toBe('请先吊销钥匙，再删除记录')

    await expect(managementErrorMessage({
      response: { data: { detail: 'owner_target_not_found' } },
    })).resolves.toBe('新所有者不存在')
    await expect(managementErrorMessage({
      response: { data: { detail: 'owner_target_inactive' } },
    })).resolves.toBe('新所有者未启用或已删除')
    await expect(managementErrorMessage({
      response: { data: { detail: 'owner_transfer_actor_forbidden' } },
    })).resolves.toBe('你没有转移该知识库所有者的权限')
  })

  it('recognizes FastAPI literal-role validation as invalid_member_role', async () => {
    const error = {
      response: {
        data: {
          detail: [{
            type: 'literal_error',
            loc: ['body', 'role'],
            msg: "Input should be 'viewer' or 'editor'",
          }],
        },
      },
    }

    await expect(managementErrorMessage(error)).resolves.toBe(
      '知识库成员角色只能是编辑者或只读用户',
    )
  })

  it('uses apiErrorDetail for unknown shapes and caller fallback for the generic detail', async () => {
    await expect(managementErrorMessage(new Error('boom'))).resolves.toBe('后端原始错误')
    proxy.apiErrorDetail.mockResolvedValue('请求失败')
    await expect(managementErrorMessage({}, '保存失败')).resolves.toBe('保存失败')
  })

  it('also translates import row codes synchronously', () => {
    expect(managementErrorCodeMessage('invalid_domain')).toBe('知识域不存在或未启用')
    expect(managementErrorCodeMessage('import_changed_since_preview')).toBe(
      '预检后用户数据已变化，请重新预检',
    )
    expect(managementErrorCodeMessage('future_code')).toBe('future_code')
  })
})
