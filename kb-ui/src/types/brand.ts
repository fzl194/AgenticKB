/** 站点品牌配置（部署级，整站统一）。来自 main_control 的 system/ui.yaml `site` 块。 */
export interface BrandConfig {
  /** 浏览器标签 <title> + 页头兜底名 */
  title: string
  /** 侧边栏 logo 主名 */
  name: string
  /** 侧边栏 logo 副标题 */
  badge: string
  /** 无图标时渐变方框里的字母标记 */
  logoText: string
  /** data URI 或 http(s) URL；空 → 回落 /favicon.svg + logoText */
  icon: string
  /** 管理员联系方式（工号/姓名等），登录失败「联系管理员」提示用。空则不显示。 */
  adminContact: string
}
