import React, { useState, useEffect, useRef, forwardRef, useImperativeHandle } from 'react'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import { VIEW_FADE } from './RightPanelViews/viewFade'
import { apiRequest, canCallAuthenticatedApi, getApiErrorMessage } from '../utils/api'
import { dataCache } from '../utils/dataCache'
import useDocumentStore from '../stores/documentStore'
import { useCanCallApi } from '../stores/authStore'
import useRightPanelHistory from '../hooks/useRightPanelHistory'
import useRightPanelHistoryActions from '../hooks/useRightPanelHistoryActions'
import useRightPanelTaskPolling from '../hooks/useRightPanelTaskPolling'
import ViewSwitch from './RightPanelViews/ViewSwitch'
import InterviewDetailView from './RightPanelViews/InterviewDetailView'
import NoteEditorModal from './RightPanelViews/NoteEditorModal'
import RightPanelOverlays from './RightPanelViews/RightPanelOverlays'
import AssetViewerSwitch from './RightPanelViews/AssetViewerSwitch'
import './PPTViewer.css'
import './PPTGeneratorModal.css'
import './LandingPage.css'
import './RightPanel.css'

const apiCall = async (url, options = {}) => {
  try {
    return await apiRequest(url, options)
  } catch (error) {
    if (error.message.includes('登录已过期') || error.message.includes('未授权')) {
      window.location.reload()
    }
    throw error
  }
}

const NOTES_CACHE_TTL = 60 * 1000
const buildNotesCacheKey = (documentId) =>
  documentId ? `notes:${documentId}` : null

const persistNotesToStorage = (documentId, notes) => {
  if (!documentId) return
  const key = buildNotesCacheKey(documentId)
  try {
    const payload = {
      value: notes,
      expiresAt: Date.now() + NOTES_CACHE_TTL
    }
    localStorage.setItem(key, JSON.stringify(payload))
  } catch {
    // ignore storage errors
  }
}

const readNotesFromStorage = (documentId) => {
  if (!documentId) return null
  const key = buildNotesCacheKey(documentId)
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed?.expiresAt && parsed.expiresAt < Date.now()) {
      localStorage.removeItem(key)
      return null
    }
    return Array.isArray(parsed?.value) ? parsed.value : null
  } catch {
    localStorage.removeItem(key)
    return null
  }
}

/**
 * 右侧面板组件
 * 根据视图类型显示不同内容：工作室按钮、流程图、闪卡
 */
const RightPanel = forwardRef(({
  view,
  onViewChange,
  documentId,
  eurekaId,  // 灵感ID（用于灵感对话页）
  disabled = false,  // 是否禁用工作室功能卡片
  // 工作室相关
  onGenerateFlashcards,
  onGenerateQuiz,
  onGenerateMindmap,
  onGenerateHighlights,
  onGenerateReport,
  isGeneratingFlowchart,
  isGeneratingFlashcards,
  isGeneratingQuiz,
  isGeneratingMindmap,
  isGeneratingHighlights,
  isGeneratingReport,
  hasFlashcards,
  hasQuiz,
  hasMindmap,
  hasHighlights,
  hasReport,
  // 流程图相关
  flowchart,
  // 闪卡相关
  flashcards,
  // 测验相关
  quiz,
  // 思维导图相关
  mindmap,
  // 重点提炼相关
  highlights,
  report,
  // AI对话功能
  onSendQuestionToChat,
  onSetChatInput, // New prop
  // 停止所有任务的回调
  // 最大化相关
  isMaximized,
  onToggleMaximize
}, ref) => {
  // 笔记状态
  const [showHistory, setShowHistory] = useState(false)

  const autoLoadAttemptedRef = useRef({}) // Track auto-load attempts to prevent loops
  const historyRefreshTrigger = useDocumentStore(s => s.historyRefreshTrigger ?? { key: 0, types: null })
  // 会话就绪信号：本地版免登录恒可加载；SaaS 刷新页面时需等登录态恢复后再加载各类记录
  const canCallApi = useCanCallApi()
  const {
    flowchartHistory,
    flashcardsHistory,
    quizHistory,
    mindmapHistory,
    highlightsHistory,
    reportHistory,
    interviewHistory,
    pptHistory,
    posterHistory,
    loadingHistory,
    startHistoryLoading,
    finishHistoryLoading,
    invalidateHistoryCache,
    loadFlowchartHistory,
    loadFlashcardsHistory,
    loadQuizHistory,
    loadMindmapHistory,
    loadHighlightsHistory,
    loadReportHistory,
    loadPptHistory,
    loadPosterHistory,
    loadInterviewHistory,
  } = useRightPanelHistory({
    state: {
      documentId,
    }
  })

  // 用户笔记状态
  const [notes, setNotes] = useState([])
  // 当视图切换到笔记时，加载 PPT 历史
  useEffect(() => {
    if ((view === 'notes' || view === 'studio') && documentId) {
      loadPptHistory({ withLoading: false })
    }
    // Reset auto-load attempts when document changes
    autoLoadAttemptedRef.current = {}
  }, [view, documentId, canCallApi])

  // Notes/Highlights Management
  const [loadingNotes, setLoadingNotes] = useState(false)
  const [showNoteDialog, setShowNoteDialog] = useState(false)
  const [noteTitle, setNoteTitle] = useState('')
  const [noteContent, setNoteContent] = useState('')
  const [editingNoteId, setEditingNoteId] = useState(null) // 正在编辑的笔记ID（null表示新建）
  const [isSavingNote, setIsSavingNote] = useState(false)

  // 面试对话框状态
  const [showInterviewDialog, setShowInterviewDialog] = useState(false)
  const [selectedInterviewDetail, setSelectedInterviewDetail] = useState(null)
  const [resumeInterviewData, setResumeInterviewData] = useState(null) // 恢复面试的数据
  const [isPreparingInterview, setIsPreparingInterview] = useState(false) // 面试准备中状态
  const [isResumingInterview, setIsResumingInterview] = useState(false) // 恢复面试中状态
  const [pptModalOpen, setPptModalOpen] = useState(false)
  const [posterModalOpen, setPosterModalOpen] = useState(false) // NEW


  const formatDateTime = (value) => {
    if (!value) return ''
    try {
      const date = new Date(value)
      if (isNaN(date.getTime())) {
        return ''
      }
      return date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      })
    } catch {
      return ''
    }
  }

  // 当前选中的历史版本（从 localStorage 恢复笔记ID）
  const [selectedFlowchartId, setSelectedFlowchartId] = useState(null)
  const [selectedFlashcardsId, setSelectedFlashcardsId] = useState(null)
  const [selectedQuizId, setSelectedQuizId] = useState(null)
  const [, setSelectedMindmapId] = useState(null)
  const [, setSelectedHighlightsId] = useState(null)
  const [selectedNoteId, setSelectedNoteId] = useState(() => {
    try {
      const saved = localStorage.getItem('selectedNoteId')
      return saved ? parseInt(saved) : null
    } catch {
      return null
    }
  })
  const [tempFlowchart, setTempFlowchart] = useState(null)
  const [tempFlashcards, setTempFlashcards] = useState(null)
  const [tempQuiz, setTempQuiz] = useState(null)
  const [tempMindmap, setTempMindmap] = useState(null)
  const [tempHighlights, setTempHighlights] = useState(null)
  const [tempReport, setTempReport] = useState(null)
  const [tempNote, setTempNote] = useState(null)
  const [, setPptId] = useState(null)
  const [tempPpt, setTempPpt] = useState(null)

  // PPT 生成状态提升到 RightPanel
  const [pptTaskId, setPptTaskId] = useState(null)
  const [isGeneratingPpt, setIsGeneratingPpt] = useState(false)
  const [pptProgress, setPptProgress] = useState(0)
  const [, setPptStatusText] = useState('')

  // Poster 生成状态
  const [posterTaskId, setPosterTaskId] = useState(null)
  const [isGeneratingPoster, setIsGeneratingPoster] = useState(false)
  const [posterProgress, setPosterProgress] = useState(0)
  const [, setPosterStatusText] = useState('')
  const [podcastHistory, setPodcastHistory] = useState([]) // Podcast History
  const [isPodcastGenerating, setIsPodcastGenerating] = useState(false) // Track generation status
  const [, setPosterId] = useState(null)
  const [tempPoster, setTempPoster] = useState(null)

  // Podcast State
  const [showPodcastModal, setShowPodcastModal] = useState(false)
  const [activePodcastTaskId, setActivePodcastTaskId] = useState(null)

  // 思维导图节点扩展状态
  const [expandingNodeId, setExpandingNodeId] = useState(null)

  // 保存选中的笔记ID和文档ID到 localStorage
  useEffect(() => {
    if (selectedNoteId && documentId) {
      localStorage.setItem('selectedNoteId', selectedNoteId.toString())
      localStorage.setItem('selectedNoteDocId', documentId.toString())
    } else {
      localStorage.removeItem('selectedNoteId')
      localStorage.removeItem('selectedNoteDocId')
    }
  }, [selectedNoteId, documentId])

  // 展开的节点集合
  const [expandedNodes, setExpandedNodes] = useState(new Set())

  // 错误提示对话框状态
  const [errorDialogState, setErrorDialogState] = useState({
    isOpen: false,
    message: ''
  })

  // 切换节点展开/收起
  const toggleNodeExpand = (nodeId) => {
    setExpandedNodes(prev => {
      const newSet = new Set(prev)
      if (newSet.has(nodeId)) {
        newSet.delete(nodeId)
      } else {
        newSet.add(nodeId)
      }
      return newSet
    })
  }

  const loadPodcastHistory = async () => {
    if (!documentId) return
    try {
      const res = await apiRequest(`/api/podcast/list/${documentId}`)
      if (res.ok) {
        const list = await res.json()
        setPodcastHistory(list || [])
      }
    } catch (e) {
      console.error("Failed to load podcast history", e)
    }
  }

  // 加载面试详情
  const loadInterviewDetail = async (recordId) => {
    try {
      const response = await apiCall(`/api/interview/${recordId}/detail`)

      if (response.ok) {
        const data = await response.json()
        if (data.success) {
          setSelectedInterviewDetail(data.record)
        }
      }
    } catch (err) {
      void err;
    }
  }

  // 恢复中止的面试
  const handleResumeInterview = async (recordId) => {
    setIsResumingInterview(true)
    try {
      const response = await apiCall(`/api/interview/${recordId}/resume`, {
        method: 'POST'
      })

      if (response.ok) {
        const data = await response.json()

        if (data.success) {
          // 打开面试对话框，并传入恢复的会话ID、问题和历史记录
          setResumeInterviewData({
            sessionId: data.session_id,
            firstQuestion: data.next_question,
            previousQA: data.previous_qa || []  // 之前的问答历史
          })
          setShowInterviewDialog(true)
        }
      } else {
        const errorData = await response.json().catch(() => null)
        setErrorDialogState({
          isOpen: true,
          message: `恢复面试失败: ${getApiErrorMessage(errorData, '未知错误')}`
        })
      }
    } catch (err) {
      void err;
      setErrorDialogState({
        isOpen: true,
        message: '恢复面试失败，请重试'
      })
    } finally {
      setIsResumingInterview(false)
    }
  }


  // 加载特定版本的流程图
  const loadFlowchartVersion = async (flowchartId) => {
    if (!documentId) return

    try {
      const response = await apiCall(`/api/document/${documentId}/flowchart/${flowchartId}`, {
        method: 'GET'
      })

      if (response.ok) {
        const data = await response.json()
        if (data.success) {
          setTempFlowchart(data.data)
          setSelectedFlowchartId(flowchartId)
        }
      }
    } catch (err) {
      void err;
    }
  }

  const loadPptVersion = async (pptId) => {
    try {
      // First call status to trigger any self-healing logic (like restoring missing images from COS)
      await apiCall(`/api/ppt/status/${pptId}`)

      const response = await apiCall(`/api/ppt/detail/${pptId}`)
      if (response.ok) {
        const data = await response.json()
        if (data.success) {
          setTempPpt(data.data)
          setPptId(pptId)
        }
      }
    } catch (err) {
      void err
    }
  }

  const loadPosterVersion = async (posterId) => {
    try {
      const response = await apiCall(`/api/poster/status/${posterId}`)
      if (response.ok) {
        const data = await response.json()
        // data 包含 poster_url, preview_url, title 等
        setTempPoster(data)
        setPosterId(posterId)
      }
    } catch (err) {
      void err
    }
  }


  // 加载特定版本的闪卡
  const loadFlashcardsVersion = async (flashcardsId) => {
    if (!documentId) return

    try {
      const response = await apiCall(`/api/document/${documentId}/flashcards/${flashcardsId}`, {
        method: 'GET'
      })

      if (response.ok) {
        const data = await response.json()
        if (data.success) {
          setTempFlashcards(data.data)
          setSelectedFlashcardsId(flashcardsId)
        }
      }
    } catch (err) {
      void err;
    }
  }

  const loadQuizVersion = async (quizId) => {
    if (!documentId) return

    try {
      const response = await apiCall(`/api/document/${documentId}/quiz/${quizId}`, {
        method: 'GET'
      })

      if (response.ok) {
        const data = await response.json()
        if (data.success) {
          setTempQuiz(data.data)
          setSelectedQuizId(quizId)
        }
      }
    } catch (err) {
      void err;
    }
  }

  // 加载笔记版本
  const loadNoteVersion = async (id) => {
    if (!documentId) return

    try {
      const response = await apiCall(`/api/notes/${id}`, {
        method: 'GET'
      })

      if (response.ok) {
        const data = await response.json()
        if (data.success) {
          setTempNote(data.data)
          setSelectedNoteId(id)
        }
      }
    } catch (err) {
      void err;
    }
  }

  const loadReportVersion = async (reportId) => {
    if (!documentId) return

    try {
      const response = await apiCall(`/api/document/${documentId}/report/${reportId}`, {
        method: 'GET'
      })

      if (response.ok) {
        const data = await response.json()
        if (data.success) {
          setTempReport(data.data)
        }
      }
    } catch (err) {
      void err
    }
  }

  // 保存手动笔记
  const handleSaveManualNote = async () => {
    if (!noteTitle.trim() || !noteContent.trim()) {
      alert('请填写标题和内容')
      return
    }

    setIsSavingNote(true)
    try {
      const isEditing = editingNoteId !== null
      // 灵感模式检测
      const isEurekaMode = !documentId && eurekaId

      let endpoint = ''
      let method = isEditing ? 'PUT' : 'POST'
      let body = {}

      if (isEurekaMode) {
        endpoint = isEditing
          ? `/api/eureka/notes/${editingNoteId}`
          : `/api/eureka/${eurekaId}/notes`

        // 灵感笔记Body格式
        body = {
          title: noteTitle.trim(),
          content: noteContent.trim()
        }
      } else {
        endpoint = isEditing
          ? `/api/notes/${editingNoteId}`
          : '/api/notes'

        // 普通笔记Body格式
        body = {
          document_id: documentId,
          title: noteTitle.trim(),
          content: noteContent.trim(),
          type: 'manual'
        }
      }

      const response = await apiCall(endpoint, {
        method,
        body: JSON.stringify(body)
      })

      if (response.ok) {
        const data = await response.json()
        if (data.success) {
          await loadNotes({ forceRefresh: true })

          // 如果是编辑模式，更新tempNote以刷新预览界面
          if (isEditing && tempNote && tempNote.id === editingNoteId) {
            setTempNote({
              ...tempNote,
              title: noteTitle.trim(),
              content: noteContent.trim()
            })
          }

          setShowNoteDialog(false)
          setNoteTitle('')
          setNoteContent('')
          setEditingNoteId(null)
          // 不再弹窗提示成功
        }
      } else {
        throw new Error('保存失败')
      }
    } catch (err) {
      void err;
      alert('保存笔记失败，请重试')
    } finally {
      setIsSavingNote(false)
    }
  }


  // 加载用户笔记
  const loadNotes = async ({ forceRefresh = false } = {}) => {
    // 灵感模式或文档模式都可以加载笔记
    const isEurekaMode = !documentId && eurekaId

    if (!documentId && !eurekaId) return
    // 本地版没有 token 也能加载；SaaS 会话未建立时先跳过（就绪后由 effect 重新触发）
    if (!canCallAuthenticatedApi()) {
      setNotes([])
      setLoadingNotes(false)
      return
    }

    const cacheKey = isEurekaMode ? `notes_eureka_${eurekaId}` : buildNotesCacheKey(documentId)

    if (!forceRefresh) {
      const cached = dataCache.get(cacheKey)
      if (cached) {
        setNotes(cached)
        setLoadingNotes(false)
        return
      }

      if (!isEurekaMode) {
        const stored = readNotesFromStorage(documentId)
        if (stored) {
          setNotes(stored)
          dataCache.set(cacheKey, stored, NOTES_CACHE_TTL)
          setLoadingNotes(false)
          return
        }
      }
    }

    setLoadingNotes(true)
    try {
      const timestamp = new Date().getTime()
      // 灵感模式：加载特定灵感的笔记
      // 文档模式：加载特定文档的笔记
      const apiUrl = isEurekaMode
        ? `/api/eureka/${eurekaId}/notes?_t=${timestamp}`
        : `/api/notes?document_id=${documentId}&_t=${timestamp}`

      const response = await apiCall(apiUrl, {
        cache: 'no-store'
      })

      if (response.ok) {
        const data = await response.json()
        if (data.success) {
          setNotes(data.data)
          dataCache.set(cacheKey, data.data, NOTES_CACHE_TTL)
          if (!isEurekaMode) {
            persistNotesToStorage(documentId, data.data)
          }
        }
      }
    } catch (err) {
      void err;
    } finally {
      setLoadingNotes(false)
    }
  }

  const {
    openMenuId,
    setOpenMenuId,
    menuPosition,
    setMenuPosition,
    renamingItem,
    setRenamingItem,
    newName,
    setNewName,
    isDeletingItem,
    setIsDeletingItem,
    deleteConfirmState,
    setDeleteConfirmState,
    executeDeleteHistory,
    handleDeleteHistory,
    startRename,
    confirmRename,
    cancelRename,
  } = useRightPanelHistoryActions({
    state: {
      documentId,
      eurekaId,
      notes,
    },
    actions: {
      apiCall,
      setNotes,
      invalidateHistoryCache,
    },
    loaders: {
      loadNotes,
      loadFlowchartHistory,
      loadFlashcardsHistory,
      loadQuizHistory,
      loadMindmapHistory,
      loadHighlightsHistory,
      loadReportHistory,
      loadPptHistory,
      loadPosterHistory,
      loadPodcastHistory,
      loadInterviewHistory,
    }
  })

  const { handleStartInterview } = useRightPanelTaskPolling({
    state: {
      documentId,
      pptTaskId,
      posterTaskId,
    },
    actions: {
      setPptTaskId,
      setIsGeneratingPpt,
      setPptProgress,
      setPptStatusText,
      setPosterTaskId,
      setIsGeneratingPoster,
      setPosterProgress,
      setPosterStatusText,
      setErrorDialogState,
      invalidateHistoryCache,
      setIsPreparingInterview,
      setResumeInterviewData,
      setShowInterviewDialog,
    },
    loaders: {
      loadPptHistory,
      loadPosterHistory,
      loadInterviewHistory,
    }
  })

  // 当文档ID改变时，清理所有临时状态
  useEffect(() => {
    if (documentId) {
      // 清理所有临时选中的版本
      setSelectedFlowchartId(null)
      setSelectedFlashcardsId(null)
      setSelectedQuizId(null)
      setSelectedMindmapId(null)
      setSelectedHighlightsId(null)
      setSelectedNoteId(null)

      // 清理所有临时数据
      setTempFlowchart(null)
      setTempFlashcards(null)
      setTempQuiz(null)
      setTempMindmap(null)
      setTempHighlights(null)
      setTempNote(null)

      // 清理其他状态
      setShowHistory(false)
      setRenamingItem(null)
      setIsDeletingItem(null)

      // 清理 localStorage 中的笔记状态（但不强制修改视图）
      localStorage.removeItem('selectedNoteId')
      localStorage.removeItem('selectedNoteDocId')
    }
  }, [documentId])

  // 当视图切换到工作室时，并行加载各类历史记录
  useEffect(() => {
    // 灵感模式（有 eurekaId）或文档模式（有 documentId）都需要加载数据
    if (view !== 'studio' || (!documentId && !eurekaId)) {
      return
    }

    const loadStudioData = async () => {
      if (!canCallAuthenticatedApi()) {
        return
      }
      startHistoryLoading()

      try {
        // 灵感模式只加载笔记，不加载其他工作室数据
        if (eurekaId && !documentId) {
          await loadNotes()
        } else {
          await Promise.all([
            loadFlowchartHistory({ withLoading: false }),
            loadFlashcardsHistory({ withLoading: false }),
            loadQuizHistory({ withLoading: false }),
            loadMindmapHistory({ withLoading: false }),
            loadHighlightsHistory({ withLoading: false }),
            loadReportHistory({ withLoading: false }),
            loadInterviewHistory({ withLoading: false }),
            loadPptHistory({ withLoading: false }),
            loadPosterHistory({ withLoading: false }),
            loadNotes()
          ])
        }
      } catch (err) {
        void err;
      } finally {
        finishHistoryLoading()
      }
    }

    loadStudioData()
  }, [view, documentId, eurekaId, canCallApi])

  // 监听 historyRefreshTrigger，对话生成完成后实时刷新对应类型的记录
  useEffect(() => {
    if (historyRefreshTrigger.key === 0 || !documentId) return

    const refreshMap = {
      flashcards: () => loadFlashcardsHistory({ withLoading: false, forceRefresh: true }),
      quiz: () => loadQuizHistory({ withLoading: false, forceRefresh: true }),
      highlights: () => loadHighlightsHistory({ withLoading: false, forceRefresh: true }),
      flowchart: () => loadFlowchartHistory({ withLoading: false, forceRefresh: true }),
      mindmap: () => loadMindmapHistory({ withLoading: false, forceRefresh: true }),
      interview: () => loadInterviewHistory({ withLoading: false, forceRefresh: true }),
      report: () => loadReportHistory({ withLoading: false, forceRefresh: true }),
      ppt: () => loadPptHistory({ withLoading: false, forceRefresh: true }),
      poster: () => loadPosterHistory({ withLoading: false, forceRefresh: true }),
    }

    const { types } = historyRefreshTrigger
    if (!types) {
      // types 为 null 时刷新全部
      Object.values(refreshMap).forEach((fn) => fn())
      loadNotes()
    } else {
      const list = Array.isArray(types) ? types : [types]
      list.forEach((type) => {
        if (refreshMap[type]) refreshMap[type]()
      })
    }
  }, [historyRefreshTrigger?.key])

  // 页面加载时，如果视图是笔记且有保存的笔记ID，自动加载
  useEffect(() => {
    if (view === 'note' && documentId && !tempNote) {
      const savedNoteId = localStorage.getItem('selectedNoteId')
      const savedDocId = localStorage.getItem('selectedNoteDocId')

      // 只在文档ID匹配时才恢复笔记
      if (savedNoteId && savedDocId && savedDocId === documentId.toString()) {
        const noteId = parseInt(savedNoteId)
        loadNoteVersion(noteId)
      } else if (savedDocId && savedDocId !== documentId.toString()) {
        onViewChange('studio')
      }
    }
  }, [view, documentId])


  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    refreshNotes: loadNotes
  }))

  // 当视图切换到流程图时，加载历史
  useEffect(() => {
    if (view === 'flowchart' && documentId) {
      loadFlowchartHistory()
      // 不清空 tempFlowchart
      // setSelectedFlowchartId(null)
      // setTempFlowchart(null)
    }
  }, [view, documentId, canCallApi])

  // 当视图切换到闪卡时，加载历史
  useEffect(() => {
    if (view === 'flashcards' && documentId) {
      loadFlashcardsHistory()
      // 不清空 tempFlashcards
      // setSelectedFlashcardsId(null)
      // setTempFlashcards(null)
    }
  }, [view, documentId, canCallApi])

  // 当视图切换到测验时，加载历史
  useEffect(() => {
    if (view === 'quiz' && documentId) {
      loadQuizHistory()
      // 不清空 tempQuiz
      // setSelectedQuizId(null)
      // setTempQuiz(null)
    }
  }, [view, documentId, canCallApi])

  // 当视图切换到思维导图时，加载历史
  useEffect(() => {
    if (view === 'mindmap' && documentId) {
      loadMindmapHistory()
      // 注意：不要清空 tempMindmap，因为点击卡片时已经设置了数据
      // setSelectedMindmapId(null)  // 也不清空这个
      // setTempMindmap(null)  // 删除这行！
    }
  }, [view, documentId, canCallApi])

  // 当视图切换到重点提炼时，加载历史
  useEffect(() => {
    if (view === 'highlights' && documentId) {
      loadHighlightsHistory()
      // 注意：不要清空 tempHighlights
      // setSelectedHighlightsId(null)
      // setTempHighlights(null)
    }
  }, [view, documentId, canCallApi])

  useEffect(() => {
    if (view === 'report' && documentId) {
      loadReportHistory()
    }
  }, [view, documentId, canCallApi])

  // Load podcast history
  useEffect(() => {
    if (documentId && canCallApi) {
      loadPodcastHistory()
    }
  }, [documentId, canCallApi])

  // 工作室按钮视图（包含笔记列表）
  // 对话生成状态（必须在组件顶层调用 hook）
  const isChatGenerating = useDocumentStore(s => s.isChatGenerating)

  // 点击外部关闭菜单
  useEffect(() => {
    const handleClickOutside = () => {
      if (openMenuId) setOpenMenuId(null)
    }

    document.addEventListener('click', handleClickOutside)
    return () => document.removeEventListener('click', handleClickOutside)
  }, [openMenuId])

  // 处理思维导图节点点击扩展
  const handleMindmapNodeClick = async (node) => {
    if (!documentId) {
      return
    }

    if (expandingNodeId) {
      return
    }

    const displayMindmap = tempMindmap || mindmap
    if (!displayMindmap) {
      return
    }

    try {
      // 设置加载状态
      setExpandingNodeId(node.id)

      const response = await apiCall(`/api/document/${documentId}/expand-mindmap-node`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          node_id: node.id,
          current_mindmap: displayMindmap
        })
      })

      if (response.ok) {
        const data = await response.json()

        if (data.success && data.new_nodes) {
          // 找到目标节点并添加新的子节点
          const updateMindmap = (mindmapData) => {
            const findAndUpdate = (currentNode) => {
              if (currentNode.id === node.id) {
                // 找到目标节点，添加或更新子节点
                const existingChildren = currentNode.children || []
                const newChildren = [...existingChildren, ...data.new_nodes]
                return { ...currentNode, children: newChildren }
              }

              if (currentNode.children && currentNode.children.length > 0) {
                return {
                  ...currentNode,
                  children: currentNode.children.map(child => findAndUpdate(child))
                }
              }

              return currentNode
            }

            return {
              ...mindmapData,
              root: findAndUpdate(mindmapData.root)
            }
          }

          const updatedMindmap = updateMindmap(displayMindmap)

          // 更新显示
          if (tempMindmap) {
            setTempMindmap(updatedMindmap)
          } else {
            // 如果是从props来的mindmap，需要通过回调更新父组件
            // 这里暂时使用tempMindmap来显示
            setTempMindmap(updatedMindmap)
          }
        }
      } else {
        const errorData = await response.json().catch(() => null)
        throw new Error(getApiErrorMessage(errorData, '扩展节点失败'))
      }
    } catch (error) {
      alert(`扩展节点失败：${error.message}`)
    } finally {
      // 清除加载状态
      setExpandingNodeId(null)
    }
  }

  // 处理重点要点的详细解释
  const handleExplainHighlight = (point) => {
    if (onSendQuestionToChat) {
      const question = `请详细解释以下要点：

        **${point.title}**

        ${point.content}

        请从以下几个方面详细说明：
        1. 这个要点的核心含义
        2. 为什么这是一个重要的观点
        3. 实际应用或影响
        4. 相关的背景知识或延伸内容`

      // 发送问题到对话框，但不切换视图，用户可以继续查看重点提炼
      onSendQuestionToChat(question)
    }
  }

  // 处理学习路径节点的详细解释
  const handleExplainPathNode = (node, index) => {
    if (onSendQuestionToChat) {
      const question = `请详细解释学习路径中的第${index + 1}个节点：

        **${node.title}**

        ${node.description}

        请从以下几个方面详细说明：
        1. 这个学习节点的核心内容
        2. 具体的学习要点和重点
        3. 如何与前后节点衔接`

      // 发送问题到对话框，但不切换视图
      onSendQuestionToChat(question)
    }
  }

  const studioProps = {
    disabled,
    eurekaId,
    isMaximized,
    onToggleMaximize,
    isChatGenerating,
    isGeneratingFlowchart,
    isGeneratingFlashcards,
    isGeneratingQuiz,
    isGeneratingMindmap,
    isGeneratingHighlights,
    isGeneratingReport,
    isPreparingInterview,
    isResumingInterview,
    isGeneratingPpt,
    isGeneratingPoster,
    isPodcastGenerating,
    hasFlashcards,
    hasQuiz,
    hasMindmap,
    hasHighlights,
    hasReport,
    flowchartHistory,
    flashcardsHistory,
    quizHistory,
    mindmapHistory,
    highlightsHistory,
    reportHistory,
    interviewHistory,
    pptHistory,
    posterHistory,
    podcastHistory,
    notes,
    openMenuId,
    setOpenMenuId,
    menuPosition,
    setMenuPosition,
    renamingItem,
    newName,
    setNewName,
    isDeletingItem,
    loadingNotes,
    loadingHistory,
    formatDateTime,
    startRename,
    confirmRename,
    cancelRename,
    handleDeleteHistory,
    loadNoteVersion,
    loadFlowchartVersion,
    loadFlashcardsVersion,
    loadQuizVersion,
    loadMindmapHistory,
    loadHighlightsHistory,
    loadReportVersion,
    loadInterviewDetail,
    loadPptVersion,
    loadPosterVersion,
    loadPodcastHistory,
    onViewChange,
    onGenerateFlashcards,
    onGenerateQuiz,
    onGenerateMindmap,
    onGenerateHighlights,
    onGenerateReport,
    handleStartInterview,
    onSetChatInput,
    setSelectedNoteId,
    setTempNote,
    setSelectedMindmapId,
    setTempMindmap,
    setSelectedHighlightsId,
    setTempHighlights,
    setTempPoster,
    setPosterId,
    setActivePodcastTaskId,
    setEditingNoteId,
    setNoteTitle,
    setNoteContent,
    setShowNoteDialog,
    pptModalOpen,
    setPptModalOpen,
    setPptTaskId,
    documentId,
    posterModalOpen,
    setPosterModalOpen,
    setPosterTaskId,
    setErrorDialogState,
    showPodcastModal,
    setShowPodcastModal,
    activePodcastTaskId,
    setIsPodcastGenerating,
    pptProgress,
    posterProgress,
  }

  const viewState = {
    flowchart,
    tempFlowchart,
    showHistory,
    loadingHistory,
    flowchartHistory,
    selectedFlowchartId,
    expandedNodes,
    flashcards,
    tempFlashcards,
    selectedFlashcardsId,
    quiz,
    tempQuiz,
    selectedQuizId,
    isGeneratingQuiz,
    mindmap,
    tempMindmap,
    isGeneratingMindmap,
    expandingNodeId,
    highlights,
    tempHighlights,
    isGeneratingHighlights,
    report,
    tempReport,
    isGeneratingReport,
    tempNote,
  }

  const actions = {
    onViewChange,
    setShowHistory,
    loadFlowchartVersion,
    toggleNodeExpand,
    onExplainPathNode: handleExplainPathNode,
    loadFlashcardsVersion,
    loadQuizVersion,
    onSendQuestionToChat,
    setTempMindmap,
    setSelectedMindmapId,
    handleMindmapNodeClick,
    setTempHighlights,
    setSelectedHighlightsId,
    onExplainHighlight: handleExplainHighlight,
    setTempReport,
    setEditingNoteId,
    setNoteTitle,
    setNoteContent,
    setShowNoteDialog,
  }

  return (
    <div className="right-panel-container">
      <AnimatePresence mode="wait">
        <ViewSwitch view={view} studioProps={studioProps} viewState={viewState} actions={actions} />

        {view === 'interview-detail' && selectedInterviewDetail && (
          <Motion.div
            key="interview-detail"
            {...VIEW_FADE}
            style={{ height: '100%', width: '100%' }}
          >
            <InterviewDetailView
              selectedInterviewDetail={selectedInterviewDetail}
              isResumingInterview={isResumingInterview}
              onBack={() => {
                setSelectedInterviewDetail(null)
                onViewChange('studio')
              }}
              onResumeInterview={handleResumeInterview}
            />
          </Motion.div>
        )}

        {(view === 'ppt' || view === 'poster') && (
          <Motion.div
            key={view}
            {...VIEW_FADE}
            style={{ height: '100%', width: '100%' }}
          >
            <AssetViewerSwitch
              view={view}
              tempPpt={tempPpt}
              tempPoster={tempPoster}
              onClosePpt={() => {
                onViewChange('studio')
                setPptId(null)
                setTempPpt(null)
              }}
              onClosePoster={() => {
                onViewChange('studio')
                setPosterId(null)
                setTempPoster(null)
              }}
            />
          </Motion.div>
        )}
      </AnimatePresence>

      <RightPanelOverlays
        deleteConfirmState={deleteConfirmState}
        onDeleteConfirm={(dontRemind) => {
          if (dontRemind) {
            localStorage.setItem('dontShowDeleteConfirm', 'true')
          }
          executeDeleteHistory(deleteConfirmState.type, deleteConfirmState.id)
          setDeleteConfirmState(prev => ({ ...prev, isOpen: false }))
        }}
        onDeleteCancel={() => setDeleteConfirmState(prev => ({ ...prev, isOpen: false }))}
        errorDialogState={errorDialogState}
        onErrorClose={() => setErrorDialogState({ isOpen: false, message: '' })}
        showInterviewDialog={showInterviewDialog}
        documentId={documentId}
        resumeInterviewData={resumeInterviewData}
        onInterviewClose={(shouldRefresh) => {
          setShowInterviewDialog(false)
          setResumeInterviewData(null)
          if (shouldRefresh) {
            loadInterviewHistory()
          }
        }}
      />
      <NoteEditorModal
        open={showNoteDialog}
        editingNoteId={editingNoteId}
        isSavingNote={isSavingNote}
        noteTitle={noteTitle}
        noteContent={noteContent}
        setNoteTitle={setNoteTitle}
        setNoteContent={setNoteContent}
        onOverlayClose={() => setShowNoteDialog(false)}
        onCancel={() => {
          setShowNoteDialog(false)
          setNoteTitle('')
          setNoteContent('')
          setEditingNoteId(null)
        }}
        onSave={handleSaveManualNote}
      />

    </div>
  );
})

export default RightPanel
