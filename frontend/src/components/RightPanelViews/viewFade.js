/**
 * 右侧面板切换视图（工作室 / 闪卡 / 测验 / 思维导图…）时的过渡。
 *
 * 不做淡出、也不从完全透明淡入：外层 AnimatePresence 是 mode="wait"，
 * 原来「旧视图淡出 0.2s → 新视图从 0 淡入 0.2s」中间面板整块空白，每次点击都像闪了一下。
 */
export const VIEW_FADE = {
  initial: { opacity: 0.6 },
  animate: { opacity: 1 },
  transition: { duration: 0.12 },
}
