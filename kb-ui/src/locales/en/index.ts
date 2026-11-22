import type { zh } from '../zh'
import { enShell } from './shell'
import { enCommon } from './common'
import { enDashboard } from './dashboard'
import { enKbView } from './kbView'
import { enKbSearch } from './kbSearch'
import { enKbManage } from './kbManage'
import { enKbStruct } from './kbStruct'
import { enRecords } from './records'
import { enMcp } from './mcp'

/** 全量 en 字典：keyof 为字面量联合，缺 part/漏 key 编译即错。 */
export const en: Record<keyof typeof zh, string> = {
  ...enShell,
  ...enCommon,
  ...enDashboard,
  ...enKbView,
  ...enKbSearch,
  ...enKbManage,
  ...enKbStruct,
  ...enRecords,
  ...enMcp,
}
