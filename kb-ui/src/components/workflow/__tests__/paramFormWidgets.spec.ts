import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import JsonSchemaParamForm from '@/components/workflow/JsonSchemaParamForm.vue'

/**
 * x-widget dispatch for array params. The shared form is used by BOTH the mining workflow editor
 * and the retrieval paradigm editor, so the important half of these tests is the fallback: a
 * caller that supplies no optionSources must keep the pre-existing free-text tag input.
 */
const KB_SCHEMA = JSON.stringify({
  type: 'object',
  properties: {
    kbIds: { type: 'array', items: { type: 'string' }, 'x-widget': 'kb-picker', title: '知识库范围' },
  },
})

const PLAIN_ARRAY_SCHEMA = JSON.stringify({
  type: 'object',
  properties: { tags: { type: 'array', items: { type: 'string' }, title: '标签' } },
})

/** Records which el-select variant rendered, by capturing the props it received. */
const SELECT_STUB = {
  name: 'ElSelect',
  // allowCreate must be declared Boolean so Vue casts the bare `allow-create` attribute to true,
  // the way Element Plus does. Declared as a bare name it would arrive as "" and read falsy.
  props: { modelValue: null, multiple: Boolean, allowCreate: Boolean, placeholder: String },
  template: '<div class="select" :data-allow-create="!!allowCreate"><slot /></div>',
}
const OPTION_STUB = {
  name: 'ElOption',
  props: ['label', 'value'],
  template: '<div class="option" :data-value="value">{{ label }}</div>',
}

function mountForm(schemaJson: string, modelValue: Record<string, unknown>, optionSources?: unknown) {
  return mount(JsonSchemaParamForm, {
    props: { schemaJson, modelValue, ...(optionSources ? { optionSources } : {}) },
    global: { stubs: { ElSelect: SELECT_STUB, ElOption: OPTION_STUB } },
  })
}

describe('JsonSchemaParamForm x-widget dispatch', () => {
  it('renders a picker when the widget has a matching option source', () => {
    const wrapper = mountForm(KB_SCHEMA, {}, {
      'kb-picker': [
        { value: 'kb1', label: '核心网知识库', hint: '12 篇' },
        { value: 'kb2', label: '传输知识库' },
      ],
    })

    const options = wrapper.findAll('.option')
    expect(options).toHaveLength(2)
    expect(options[0].text()).toContain('核心网知识库')
    // A picker must not let users invent ids — that is what the fallback control is for.
    expect(wrapper.get('.select').attributes('data-allow-create')).toBe('false')
  })

  it('falls back to the free-text tag input when no option source is supplied', () => {
    // This is the mining workflow editor's path: it passes no optionSources at all.
    const wrapper = mountForm(KB_SCHEMA, {})

    expect(wrapper.findAll('.option')).toHaveLength(0)
    expect(wrapper.get('.select').attributes('data-allow-create')).toBe('true')
  })

  it('falls back when optionSources exists but lacks this widget', () => {
    const wrapper = mountForm(KB_SCHEMA, {}, { 'some-other-picker': [{ value: 'x', label: 'x' }] })

    expect(wrapper.get('.select').attributes('data-allow-create')).toBe('true')
  })

  it('leaves arrays without x-widget on the free-text control', () => {
    const wrapper = mountForm(PLAIN_ARRAY_SCHEMA, {}, { 'kb-picker': [{ value: 'kb1', label: 'KB1' }] })

    expect(wrapper.get('.select').attributes('data-allow-create')).toBe('true')
  })

  it('keeps already-saved values that are no longer in the source', () => {
    // A deleted or cross-domain KB must not silently vanish from a saved paradigm just because
    // someone opened the form.
    const wrapper = mountForm(KB_SCHEMA, { kbIds: ['kb1', 'kb-gone'] }, {
      'kb-picker': [{ value: 'kb1', label: '核心网知识库' }],
    })

    const values = wrapper.findAll('.option').map(o => o.attributes('data-value'))
    expect(values).toEqual(['kb1', 'kb-gone'])
    expect(wrapper.findAll('.option')[1].text()).toContain('kb-gone')
  })

  it('does not duplicate an option that is both selected and known', () => {
    const wrapper = mountForm(KB_SCHEMA, { kbIds: ['kb1'] }, {
      'kb-picker': [{ value: 'kb1', label: '核心网知识库' }],
    })

    expect(wrapper.findAll('.option')).toHaveLength(1)
  })
})
