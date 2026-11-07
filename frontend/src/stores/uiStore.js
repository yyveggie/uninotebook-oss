import { create } from 'zustand'

const useUIStore = create((set) => ({
  // === Delete Confirm Dialog ===
  deleteConfirmState: {
    isOpen: false,
    type: null, // 'chat'
  },
  setDeleteConfirmState: (state) => {
    if (typeof state === 'function') {
      set((s) => ({ deleteConfirmState: state(s.deleteConfirmState) }))
    } else {
      set({ deleteConfirmState: state })
    }
  },

  // === Prompt Templates ===
  promptTemplates: [],
  showPromptModal: false,
  editingTemplate: null, // null = 创建, object = 编辑
  setPromptTemplates: (templates) => set({ promptTemplates: templates }),
  setShowPromptModal: (show) => set({ showPromptModal: show }),
  setEditingTemplate: (template) => set({ editingTemplate: template }),

  // === Toast（非阻断提示）===
  // 后台任务失败、本地服务连接异常等不应盖住整个工作区的提示统一走这里
  toasts: [],
  /**
   * @param {string} message
   * @param {{ type?: 'error' | 'info', duration?: number }} [options] duration 为 0 时需手动关闭
   */
  showToast: (message, { type = 'error', duration = 6000 } = {}) => {
    if (!message) return null
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    // 同一文案只保留一条，避免轮询类失败刷屏
    set((s) => ({ toasts: [...s.toasts.filter((t) => t.message !== message), { id, message, type }] }))
    if (duration > 0) {
      setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
      }, duration)
    }
    return id
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  /* Team State Removed */
}))

export default useUIStore
