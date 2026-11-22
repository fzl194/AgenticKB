/** MCP 接入区文案。 */
export const zhMcp = {
  // 列表分隔符：制品未保存提示里多标题的拼接（R9 盲区：join('、')）
  'mcp.separator': '、',
  // views/McpAccessView.vue：页头说明。源文件是两行 → 按行各成一 key（zh 逐字节取源行）；
  // 模板里两段之间的换行折叠成一个空格，渲染结果与转换前一致
  'mcp.view.descEntry': '你的 Agent（dify / 扣子 / Claude 等）连平台知识库的入口。每个 Agent 一把钥匙、一把钥匙一个知识域：',
  'mcp.view.descIsolation': '各钥匙的工具、开放库、提示词互不影响。',
  'mcp.tip.atLimit': '已达上限',
  'mcp.action.createKey': '新建钥匙',
  'mcp.view.loadFailedTitle': '钥匙列表加载失败。你的现役钥匙不受影响，Agent 连接不会中断。',
  'mcp.action.retryLoad': '重试加载',
  'mcp.view.emptyTitle': '还没有 MCP 钥匙',
  // 被 <br> 切开的两句（R4 片段 key）
  'mcp.view.emptyTipDomain': '每个 Agent 一把钥匙，一把钥匙一个知识域。',
  'mcp.view.emptyTipStart': '为你的第一个 Agent 创建一把钥匙开始接入。',
  // 钥匙列表表头（「名称」「状态」「操作」复用 common.name / common.status / common.actions）
  'mcp.field.domain': '域',
  'mcp.field.secret': '密钥',
  'mcp.field.lastUsed': '最近使用',
  'mcp.field.never': '从未',
  'mcp.field.createdAt': '创建时间',
  // 钥匙状态标签
  'mcp.status.revoked': '已吊销',
  'mcp.tip.domainUnbound': '该钥匙绑定的知识域已被解绑，请联系管理员重新分配域后重建钥匙',
  'mcp.status.domainUnbound': '域已解绑',
  'mcp.status.normal': '正常',
  // 行内操作
  'mcp.action.callRecords': '调用记录',
  'mcp.action.configure': '配置',
  'mcp.action.rotate': '轮换',
  'mcp.action.revoke': '吊销',
  'mcp.action.deleteRecord': '删除记录',
  // 新建钥匙弹窗（「取消」「创建」复用 common.cancel / common.create）
  'mcp.dialog.createTitle': '新建 MCP 钥匙',
  'mcp.form.namePlaceholder': '给钥匙起个名字，如：研发知识助手',
  'mcp.field.knowledgeDomain': '知识域',
  'mcp.form.domainPlaceholder': '选择该钥匙服务的知识域',
  // 明文一次性展示弹窗（创建 / 轮换共用）
  'mcp.dialog.freshTitle': '钥匙已创建',
  'mcp.dialog.freshAlert': '密钥明文仅此一次显示，关闭后无法再查看。请立即复制并填入你的 Agent 配置。',
  'mcp.action.copy': '复制',
  'mcp.view.freshNote': '连接地址由当前部署地址推导（{endpoint}）；JSON 已预填本次明文。',
  'mcp.action.savedSecret': '我已保存好密钥',
  // 配置抽屉标题
  'mcp.view.configTitle': '钥匙配置',
  'mcp.view.configTitleNamed': '钥匙配置 · {name}',
  // 建钥错误（409 分支的「上限」是与服务端文案比对的匹配数据，留在代码里不入字典——R2）
  'mcp.error.domainNotBound': '你未绑定该知识域，无法创建钥匙',
  'mcp.error.limitReached': '已达 10 把上限，请先吊销不需要的钥匙',
  // 确认框（列表快捷操作与配置抽屉共用；「取消」复用 common.cancel）
  'mcp.confirm.rotateMessage': '轮换后「{name}」的旧密钥立即失效，正在使用它的 Agent 会断连。确定继续？',
  'mcp.dialog.rotateTitle': '轮换密钥',
  'mcp.confirm.revokeMessage': '吊销后「{name}」立即失效且不可恢复，正在使用它的 Agent 会断连。确定吊销？',
  'mcp.dialog.revokeTitle': '吊销钥匙',
  'mcp.msg.keyRevoked': '钥匙已吊销',
  'mcp.confirm.deleteMessage': '删除已吊销钥匙「{name}」的展示记录？旧密钥仍永久失效，此操作不会恢复或影响其他钥匙。',
  'mcp.dialog.deleteTitle': '删除钥匙记录',
  'mcp.msg.keyRecordDeleted': '钥匙记录已删除',
  'mcp.msg.copied': '已复制',
  'mcp.msg.copyFailed': '复制失败，请手动选择复制',
  // components/mcp/McpKeyConfigPanel.vue：基本信息
  'mcp.field.secretId': '密钥标识',
  'mcp.field.lastRotated': '上次轮换',
  'mcp.panel.configTitle': 'Agent 接入配置',
  // 第二句被 <code> 切开 → 按片段入字典（R4：各片段自带空格与标点）
  'mcp.panel.configDesc': '选一种格式，点击复制后粘贴进你的 Agent / MCP 客户端（{endpoint}）。',
  'mcp.panel.configDescPlaceholderPrefix': '平时显示 ',
  'mcp.panel.configDescPlaceholderSuffix': ' 占位——密钥明文只在生成/轮换时显示一次，丢了只能轮换。',
  'mcp.panel.formatGeneric': '通用',
  // 开放的工具（ALL_TOOLS 常量表按 R8 只存 labelKey，渲染时 t()）
  'mcp.panel.openToolsTitle': '开放的工具',
  'mcp.panel.openToolsDesc': '关掉的工具对这把钥匙的 Agent 完全不可见。至少保留一个。',
  'mcp.tool.searchKnowledge': '检索知识证据（domain 单域免传）',
  'mcp.tool.getKnowledge': '深入读取：浏览层级 / 逐层看目录 / 取原文 / 看能力 / 导航 / 查表格',
  'mcp.tool.manageFiles': '管理文件：上传到指定目录 / 替换任意本地文件（zip 自动解压，自动排队挖掘；不提供删除/移动/重命名）',
  // 开放的知识库
  'mcp.panel.openKbsTitle': '开放的知识库（{domain} 域）',
  'mcp.panel.openKbsDesc': '这把钥匙只能访问这里勾选的库。数据源固定为钥匙所属域——与页面顶部的当前域切换无关。',
  'mcp.panel.kbDocCount': '{n} 文档',
  'mcp.panel.openKbsEmpty': '该域没有你能看到的知识库',
  // 开放的知识制品（「已发布」复用 common.docStatus.published）
  'mcp.panel.openArtifactsTitle': '开放的知识制品（{domain} 域）',
  'mcp.panel.openArtifactsDescScope': 'Agent 通过这把钥匙只能查到勾选的知识制品（表格：说明卡与行查询；Wiki：说明卡、搜页、整页读取与链接导航）。',
  'mcp.panel.openArtifactsDescCandidates': '候选=你在本域能看到的已发布制品（公开+你是成员）；不勾=制品通道对这把钥匙关闭。',
  'mcp.artifactStatus.deprecated': '已停用',
  'mcp.artifactStatus.draft': '草稿',
  'mcp.panel.visibilityPublic': ' · 公开',
  'mcp.panel.openArtifactsEmpty': '该域没有你能开放的已发布知识制品',
  // 提示词与工具说明
  'mcp.panel.promptTitle': '提示词与工具说明',
  'mcp.panel.promptDesc': '默认文案已预填，直接在原文上增改；内容与默认一致时按默认下发（默认文案将来更新也能跟随）。',
  'mcp.action.restoreDefaults': '恢复默认值',
  'mcp.panel.promptPlaceholder': '正在加载默认提示词…',
  'mcp.panel.descPlaceholder': '正在加载默认说明…',
  // 工具参数表（「类型」复用 common.type）
  'mcp.panel.paramCount': '参数（{n}）',
  'mcp.panel.paramName': '参数',
  'mcp.panel.paramRequired': '必填',
  'mcp.panel.paramDescription': '说明',
  'mcp.panel.yes': '是',
  'mcp.panel.no': '否',
  // 明文一次性展示（轮换后）：后接 <code>
  'mcp.panel.freshKeyTitle': '新密钥（仅此一次可见）：',
  // 危险操作
  'mcp.panel.rotateButton': '轮换密钥（旧钥立即失效）',
  'mcp.panel.revokeButton': '吊销这把钥匙（不可恢复）',
  'mcp.msg.defaultsRestored': '已填回默认文案，点「保存」生效',
  'mcp.msg.keepOneTool': '至少保留一个工具',
  'mcp.msg.toolsSaved': '工具开关已保存',
  'mcp.msg.openKbsUpdated': '开放库已更新',
  'mcp.msg.artifactsPartiallySaved': '部分制品未保存：{titles}。请刷新页面并检查制品权限后重试。',
  'mcp.msg.artifactsUpdated': '开放制品已更新',
  'mcp.msg.promptSaved': '提示词已保存',
  'mcp.msg.keyRotated': '密钥已轮换；旧密钥立即失效',
} as const
