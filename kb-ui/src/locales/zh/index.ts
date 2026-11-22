import { zhShell } from './shell'
import { zhCommon } from './common'
import { zhDashboard } from './dashboard'
import { zhKbView } from './kbView'
import { zhKbSearch } from './kbSearch'
import { zhKbManage } from './kbManage'
import { zhKbStruct } from './kbStruct'
import { zhRecords } from './records'
import { zhMcp } from './mcp'

/** 全量 zh 字典 = 各 part 合并。part 间 key 不得重复（__tests__/dict.spec.ts 有闸门）。 */
export const zh = {
  ...zhShell,
  ...zhCommon,
  ...zhDashboard,
  ...zhKbView,
  ...zhKbSearch,
  ...zhKbManage,
  ...zhKbStruct,
  ...zhRecords,
  ...zhMcp,
}

export type MessageKey = keyof typeof zh
