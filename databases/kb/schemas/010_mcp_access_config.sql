-- 批次7：用户级 MCP 配置扩展——工具开关 / 提示词 / 工具描述可编辑。
-- open_tools：NULL = 全部开放（默认，兼容批次5 行为）；非空数组 = 仅这些工具名可用。
-- instructions / tool_descriptions：NULL = 使用服务端默认文案；tools/list 按此渲染。
ALTER TABLE mcp_access ADD COLUMN IF NOT EXISTS open_tools JSONB;
ALTER TABLE mcp_access ADD COLUMN IF NOT EXISTS instructions TEXT;
ALTER TABLE mcp_access ADD COLUMN IF NOT EXISTS tool_descriptions JSONB;
