import { config } from '@vue/test-utils'

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(globalThis, 'ResizeObserver', {
  value: ResizeObserverStub,
  configurable: true,
})

config.global.stubs = {
  RouterLink: { template: '<a><slot /></a>' },
  ElButton: { template: '<button @click="$emit(\'click\')"><slot /></button>' },
  ElIcon: { template: '<i><slot /></i>' },
  ElTable: { template: '<div><slot /></div>' },
  ElTableColumn: { template: '<div><slot :row="{}" /></div>' },
  ElDialog: { template: '<div><slot /><slot name="footer" /></div>' },
  ElForm: { template: '<form><slot /></form>' },
  ElFormItem: { template: '<div><slot /></div>' },
  ElInput: { template: '<input />' },
  ElSelect: { template: '<select><slot /></select>' },
  ElOption: { template: '<option><slot /></option>' },
  ElUpload: { template: '<div><slot /><slot name="tip" /></div>' },
  ElPagination: true,
  ElTag: { template: '<span class="el-tag"><slot /></span>' },
  ElDropdown: { template: '<div class="el-dropdown"><span class="el-dropdown-trigger"><slot /></span><div class="el-dropdown-menu"><slot name="dropdown" /></div></div>' },
  ElDropdownMenu: { template: '<div><slot /></div>' },
  ElDropdownItem: { template: '<div class="el-dropdown-item" @click="$emit(\'command\')"><slot /></div>' },
}

config.global.mocks = {
  $router: { push: () => undefined },
}
