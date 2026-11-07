import { create } from 'zustand'

/**
 * 界面语言（目前用于设置页）。
 *
 * 首次访问跟随浏览器语言，用户切换后记在 localStorage。
 * 隐私模式或禁用存储时读写会抛错，此时退回浏览器语言、只在本次会话内生效。
 */

const STORAGE_KEY = 'uninotebook.locale'
export const LOCALES = ['zh', 'en']

const browserLocale = () => {
  const language = (typeof navigator !== 'undefined' && navigator.language) || 'zh'
  return language.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

const initialLocale = () => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (LOCALES.includes(saved)) return saved
  } catch {
    // 存储不可用：沿用浏览器语言
  }
  return browserLocale()
}

const useLocaleStore = create((set) => ({
  locale: initialLocale(),
  setLocale: (locale) => {
    if (!LOCALES.includes(locale)) return
    try {
      localStorage.setItem(STORAGE_KEY, locale)
    } catch {
      // 同上：不持久化也能切换
    }
    set({ locale })
  },
}))

export default useLocaleStore
