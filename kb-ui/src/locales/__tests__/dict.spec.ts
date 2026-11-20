import { describe, expect, it } from 'vitest'
import { zh } from '../zh'
import { en } from '../en'

describe('locale dictionaries', () => {
  it('en covers exactly the zh key set', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort())
  })

  it('no empty values in either dictionary', () => {
    for (const [key, value] of Object.entries(zh)) expect(value, `zh.${key}`).toBeTruthy()
    for (const [key, value] of Object.entries(en)) expect(value, `en.${key}`).toBeTruthy()
  })
})
