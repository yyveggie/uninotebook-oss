import { create } from 'zustand'
import { invalidateDocumentStatusCache } from '../utils/documentStatusCache'
import { dataCache } from '../utils/dataCache'

const useDocumentStore = create((set, get) => ({
  // === Document State ===
  currentDocument: null,
  showHome: true,
  isLoading: false,
  loadingType: '', // 'topic' | 'document' | 'flowchart' | ''
  error: null,
  cacheHit: false,
  processingProgress: 0,
  processingStage: '',
  pendingInitialMessage: null,

  // === Session State ===
  sessionInfo: { id: null, docId: null },
  chatSessionKey: 0,
  sessionsRefreshKey: 0,
  homeRefreshKey: 0,

  // === Doc Name Editing ===
  isEditingDocName: false,
  editedDocName: '',

  // === Node/Tree State ===
  selectedNode: null,
  completedNodes: new Set(),
  queryParams: {
    topic: '',
    currentLevel: 'beginner',
    learningGoal: '',
    languagePreference: 'english',
  },
  expandDialogNode: null,
  aiDialogNode: null,
  expandingNode: null,

  // === Generation State ===
  documentFlowchart: null,
  flashcards: null,
  quiz: null,
  mindmap: null,
  highlights: null,
  report: null,
  isGeneratingFlowchart: false,
  isGeneratingFlashcards: false,
  isGeneratingQuiz: false,
  isGeneratingMindmap: false,
  isGeneratingHighlights: false,
  isGeneratingReport: false,
  isGeneratingProsCons: false,
  isGeneratingSimpleExample: false,
  isChatGenerating: false, // 对话闪卡/测验/提炼生成中

  // === History Refresh Trigger ===
  historyRefreshTrigger: { key: 0, types: null },

  // === Right Panel ===
  rightPanelView: 'studio', // 'studio' | 'flowchart' | 'flashcards' | 'quiz' | 'mindmap' | 'highlights' | 'report' | 'note'

  // === Layout ===
  maximizedPanel: 'none', // 'none' | 'left' | 'center' | 'right'

  // === Internal ===
  _pollIntervalId: null,

  // === Actions ===
  setCurrentDocument: (doc) => {
    if (typeof doc === 'function') {
      set((state) => ({ currentDocument: doc(state.currentDocument) }))
    } else {
      set({ currentDocument: doc })
    }
  },
  setShowHome: (show) => set({ showHome: show }),
  setIsLoading: (loading) => set({ isLoading: loading }),
  setLoadingType: (type) => set({ loadingType: type }),
  setError: (error) => set({ error }),
  setCacheHit: (hit) => set({ cacheHit: hit }),
  setProcessingProgress: (progress) => set({ processingProgress: progress }),
  setProcessingStage: (stage) => set({ processingStage: stage }),
  setPendingInitialMessage: (msg) => set({ pendingInitialMessage: msg }),

  setSessionInfo: (info) => {
    if (typeof info === 'function') {
      set((state) => ({ sessionInfo: info(state.sessionInfo) }))
    } else {
      set({ sessionInfo: info })
    }
  },
  incrementChatSessionKey: () => set((s) => ({ chatSessionKey: s.chatSessionKey + 1 })),
  incrementSessionsRefreshKey: () => set((s) => ({ sessionsRefreshKey: s.sessionsRefreshKey + 1 })),
  incrementHomeRefreshKey: () => set((s) => ({ homeRefreshKey: s.homeRefreshKey + 1 })),

  setIsEditingDocName: (editing) => set({ isEditingDocName: editing }),
  setEditedDocName: (name) => set({ editedDocName: name }),

  setSelectedNode: (node) => set({ selectedNode: node }),
  setCompletedNodes: (nodes) => {
    if (typeof nodes === 'function') {
      set((state) => ({ completedNodes: nodes(state.completedNodes) }))
    } else {
      set({ completedNodes: nodes })
    }
  },
  setQueryParams: (params) => set({ queryParams: params }),
  setExpandDialogNode: (node) => set({ expandDialogNode: node }),
  setAIDialogNode: (node) => set({ aiDialogNode: node }),
  setExpandingNode: (node) => set({ expandingNode: node }),

  // Generation setters
  setDocumentFlowchart: (data) => set({ documentFlowchart: data }),
  setFlashcards: (data) => set({ flashcards: data }),
  setQuiz: (data) => set({ quiz: data }),
  setMindmap: (data) => set({ mindmap: data }),
  setHighlights: (data) => set({ highlights: data }),
  setReport: (data) => set({ report: data }),
  setIsGeneratingFlowchart: (v) => set({ isGeneratingFlowchart: v }),
  setIsGeneratingFlashcards: (v) => set({ isGeneratingFlashcards: v }),
  setIsGeneratingQuiz: (v) => set({ isGeneratingQuiz: v }),
  setIsGeneratingMindmap: (v) => set({ isGeneratingMindmap: v }),
  setIsGeneratingHighlights: (v) => set({ isGeneratingHighlights: v }),
  setIsGeneratingReport: (v) => set({ isGeneratingReport: v }),
  setIsGeneratingProsCons: (v) => set({ isGeneratingProsCons: v }),
  setIsGeneratingSimpleExample: (v) => set({ isGeneratingSimpleExample: v }),
  setIsChatGenerating: (v) => set({ isChatGenerating: v }),

  setRightPanelView: (view) => set({ rightPanelView: view }),
  setMaximizedPanel: (panel) => set({ maximizedPanel: panel }),
  toggleMaximize: (panel) => set((s) => ({
    maximizedPanel: s.maximizedPanel === panel ? 'none' : panel,
  })),

  // 清除文档相关的所有状态
  clearDocumentStates: (clearDocument = true) => {
    const state = get()
    if (clearDocument && state.currentDocument?.document_id) {
      invalidateDocumentStatusCache(state.currentDocument.document_id)
    }

    const updates = {
      documentFlowchart: null,
      isGeneratingFlowchart: false,
      flashcards: null,
      isGeneratingFlashcards: false,
      quiz: null,
      isGeneratingQuiz: false,
      mindmap: null,
      isGeneratingMindmap: false,
      highlights: null,
      isGeneratingHighlights: false,
      report: null,
      isGeneratingReport: false,
      isChatGenerating: false,
      rightPanelView: 'studio',
    }

    if (clearDocument) {
      updates.currentDocument = null
      localStorage.removeItem('currentDocument')
    }

    // 清理轮询定时器
    const pollId = get()._pollIntervalId
    if (pollId) {
      clearInterval(pollId)
      updates._pollIntervalId = null
    }

    set(updates)
  },

  // 停止所有生成任务
  stopAllGenerations: () => set({
    isGeneratingFlowchart: false,
    isGeneratingFlashcards: false,
    isGeneratingQuiz: false,
    isGeneratingMindmap: false,
    isGeneratingHighlights: false,
    isGeneratingReport: false,
  }),

  invalidateStatusCache: (docId) => {
    const state = get()
    const targetId = docId ?? state.currentDocument?.document_id
    if (!targetId) return
    invalidateDocumentStatusCache(targetId)
  },

  invalidateDocumentHistoryCache: (docId, types) => {
    const state = get()
    const targetId = docId ?? state.currentDocument?.document_id
    if (!targetId) return

    const prefix = `doc:${targetId}:history:`
    if (!types) {
      dataCache.invalidateByPrefix(prefix)
      invalidateDocumentStatusCache(targetId)
    } else {
      const list = Array.isArray(types) ? types : [types]
      list.forEach((type) => {
        dataCache.invalidate(`${prefix}${type}`)
      })
    }

    // 递增 trigger 通知 RightPanel 实时刷新对应类型
    set((s) => ({
      historyRefreshTrigger: {
        key: s.historyRefreshTrigger.key + 1,
        types: types ?? null,
      },
    }))
  },
}))

// 初始化: 从 localStorage 恢复文档状态
const initDocumentFromStorage = () => {
  try {
    const pathname = window.location.pathname
    const isSpecialRoute =
      pathname === '/knowledgegraph' ||
      pathname.startsWith('/eureka-chat/') ||
      pathname === '/legal/user-service-agreement' ||
      pathname === '/legal/terms-of-service' ||
      pathname === '/legal/privacy-policy'

    const saved = localStorage.getItem('currentDocument')

    if (isSpecialRoute) {
      useDocumentStore.setState({ showHome: true, currentDocument: saved ? JSON.parse(saved) : null })
      return
    }

    if (saved) {
      const doc = JSON.parse(saved)
      const params = new URLSearchParams(window.location.search)
      const urlId = params.get('document_id') || params.get('doc')

      if (urlId && String(doc.document_id) !== String(urlId)) {
        // URL 指定了不同的 document_id，不恢复缓存
        useDocumentStore.setState({ showHome: true, currentDocument: null })
      } else {
        useDocumentStore.setState({ showHome: false, currentDocument: doc })
      }
    } else {
      useDocumentStore.setState({ showHome: true })
    }
  } catch {
    useDocumentStore.setState({ showHome: true })
  }
}

initDocumentFromStorage()

export default useDocumentStore
