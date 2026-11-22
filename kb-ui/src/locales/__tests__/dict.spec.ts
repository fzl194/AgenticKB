import { describe, expect, it } from 'vitest'
import { zh } from '../zh'
import { en } from '../en'
import { zhShell } from '../zh/shell'
import { zhCommon } from '../zh/common'
import { zhDashboard } from '../zh/dashboard'
import { zhKbView } from '../zh/kbView'
import { zhKbSearch } from '../zh/kbSearch'
import { zhKbManage } from '../zh/kbManage'
import { zhKbStruct } from '../zh/kbStruct'
import { zhRecords } from '../zh/records'
import { zhMcp } from '../zh/mcp'

describe('locale dictionaries', () => {
  it('en covers exactly the zh key set', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort())
  })

  it('no empty values in either dictionary', () => {
    for (const [key, value] of Object.entries(zh)) expect(value, `zh.${key}`).toBeTruthy()
    for (const [key, value] of Object.entries(en)) expect(value, `en.${key}`).toBeTruthy()
  })

  it('no key is defined by two dictionary parts (spread would silently overwrite)', () => {
    const all = [
      zhShell, zhCommon, zhDashboard, zhKbView, zhKbSearch,
      zhKbManage, zhKbStruct, zhRecords, zhMcp,
    ].flatMap(part => Object.keys(part))
    expect(new Set(all).size).toBe(all.length)
  })

  it('interpolation placeholders match between zh and en for parameterized messages', () => {
    const placeholders = (text: string) => (text.match(/\{\w+\}/g) ?? []).sort()
    for (const [key, value] of Object.entries(zh)) {
      expect(placeholders(en[key as keyof typeof en]), key)
        .toEqual(placeholders(value))
    }
  })
})
