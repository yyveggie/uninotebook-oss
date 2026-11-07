import { useCallback, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { TASK_POLLING_INTERVAL_MS, MAX_UPLOAD_FILE_SIZE_BYTES, MAX_UPLOAD_FILE_SIZE_MB } from '../utils/config'

// 摘要补写的最长等待：后端摘要总时限 60 秒，超过仍未拿到就停止轮询（文档照常可用）
const SUMMARY_WAIT_LIMIT_MS = 2 * 60 * 1000
import { apiRequest, canCallAuthenticatedApi, isLocalRuntime } from '../utils/api'
import { dataCache } from '../utils/dataCache'
import { fetchDocumentStatus } from '../utils/documentStatusCache'
import useAuthStore, { useCanCallApi } from '../stores/authStore'
import useDocumentStore from '../stores/documentStore'
import useUIStore from '../stores/uiStore'

/**
 * 文档生命周期 hook：上传、选择、会话管理、重命名、返回首页等
 * 所有函数名/参数名与原 App.jsx 完全一致
 */
export default function useDocumentActions() {
  const navigate = useNavigate()

  // ========== Refs ==========
  const activeDocumentIdRef = useRef(
    useDocumentStore.getState().currentDocument?.document_id ?? null
  )
  const documentUpdatesEnabledRef = useRef(true)
  const sessionEnsureInFlightRef = useRef(new Set())
  const pollingIntervalRef = useRef(null)
  // 文档已可用、摘要仍在补写时开始等待的时间（0 表示未在等待）
  const summaryWaitStartedRef = useRef(0)

  // ========== Store selectors (stable via getState in handlers) ==========
  const getDoc = () => useDocumentStore.getState()
  const getAuth = () => useAuthStore.getState()
  const getUI = () => useUIStore.getState()

  // ========== 包装 clearDocumentStates (需要操作 ref) ==========
  const clearDocumentStates = useCallback((clearDocument = true) => {
    if (clearDocument) {
      activeDocumentIdRef.current = null
    }
    getDoc().clearDocumentStates(clearDocument)
  }, [])

  const stopDocumentStatusPolling = useCallback(() => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current)
      pollingIntervalRef.current = null
    }
    const storePollId = useDocumentStore.getState()._pollIntervalId
    if (storePollId) {
      clearInterval(storePollId)
      useDocumentStore.setState({ _pollIntervalId: null })
    }
  }, [])

  // ========== 缓存失效 ==========
  const invalidateHistoryCache = useCallback(() => {
    const user = getAuth().user
    if (user?.id) {
      dataCache.invalidateByPrefix(`history:documents:${user.id}`)
    }
  }, [])

  // ========== ensureSessionExists ==========
  const ensureSessionExists = async (documentId, sessionId, title = '新对话') => {
    if (!documentId || !sessionId) return

    // 本地版没有 token 但可直接调用；SaaS 需会话已建立
    const authState = getAuth()
    if (!canCallAuthenticatedApi() || !authState.user || authState.isCheckingAuth) {
      return
    }

    const requestKey = `${documentId}:${sessionId}`
    if (sessionEnsureInFlightRef.current.has(requestKey)) {
      return
    }

    sessionEnsureInFlightRef.current.add(requestKey)
    try {
      const response = await apiRequest(`/api/document/${documentId}/sessions`, {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId, title })
      })

      if (response.status === 401) {
        return
      }

      if (response.ok) {
        getDoc().incrementSessionsRefreshKey()
      }
    } catch (err) {
      void err
    } finally {
      sessionEnsureInFlightRef.current.delete(requestKey)
    }
  }

  // ========== validatePersistedDocument ==========
  const validatePersistedDocument = useCallback(async () => {
    if (!documentUpdatesEnabledRef.current) return
    if (!canCallAuthenticatedApi()) return

    const saved = localStorage.getItem('currentDocument')
    if (!saved) return

    let restoredDocument
    try {
      restoredDocument = JSON.parse(saved)
    } catch (err) {
      void err
      clearDocumentStates()
      return
    }

    if (!restoredDocument || !restoredDocument.document_id) {
      clearDocumentStates()
      return
    }

    try {
      const statusResult = await fetchDocumentStatus(restoredDocument.document_id, { useCache: true })
      if (!statusResult.ok) {
        if (statusResult.status === 404 || statusResult.status === 403) {
          clearDocumentStates()
        }
        return
      }

      if (!documentUpdatesEnabledRef.current) return

      const payload = statusResult.data
      if (payload?.success && payload.data) {
        documentUpdatesEnabledRef.current = true
        activeDocumentIdRef.current = payload.data.document_id
        getDoc().setCurrentDocument(prev => {
          if (!documentUpdatesEnabledRef.current) return prev
          const baseDocument = prev && prev.document_id === payload.data.document_id ? prev : restoredDocument
          return { ...baseDocument, ...payload.data }
        })
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : ''
      if (message.includes('未授权') || message.includes('登录')) {
        clearDocumentStates()
      }
    }
  }, [clearDocumentStates])

  // ========== handleAuthSuccess ==========
  const handleAuthSuccess = async () => {
    // 先清除 auth URL（/signin 等），防止 App.jsx 的 URL 监听重新打开弹窗
    navigate('/', { replace: true })

    try {
      await getAuth().handleAuthSuccess()
    } catch (err) {
      console.error('Auth success handler failed:', err)
    }

    void validatePersistedDocument()
  }

  // ========== handleLogout ==========
  const handleLogout = () => {
    const activeDocId = getDoc().currentDocument?.document_id
    clearDocumentStates()
    invalidateHistoryCache()
    getDoc().invalidateDocumentHistoryCache(activeDocId)
    getAuth().logout()
    navigate('/')
    getDoc().setShowHome(true)
    getDoc().setError(null)
  }

  // ========== pollDocumentStatus ==========
  const pollDocumentStatus = async (documentId) => {
    if (activeDocumentIdRef.current !== documentId) return true
    try {
      // Polling must always hit latest status to avoid stale UI state.
      const statusResult = await fetchDocumentStatus(documentId, { useCache: false })

      if (!statusResult.ok) {
        if (statusResult.status === 404) {
          getDoc().setError('文档处理失败，已自动清理')
          getDoc().setIsLoading(false)
          getDoc().setShowHome(true)
          getDoc().setCurrentDocument(null)
          return true
        }
        return false
      }

      if (activeDocumentIdRef.current !== documentId) {
        getDoc().setIsLoading(false)
        return true
      }

      const payload = statusResult.data
      if (!payload?.success || !payload.data) return false

      const { processing_status, processing_progress = 0 } = payload.data

      if (!documentUpdatesEnabledRef.current) {
        getDoc().setIsLoading(false)
        return true
      }

      getDoc().setProcessingProgress(processing_progress)

      if (processing_progress < 100) {
        getDoc().setProcessingStage('正在处理中...٩(๑•̀ω•́๑)۶')
      } else {
        getDoc().setProcessingStage('处理完成')
      }

      if (!documentUpdatesEnabledRef.current || activeDocumentIdRef.current !== documentId) {
        getDoc().setIsLoading(false)
        return true
      }

      getDoc().setCurrentDocument(prev => ({ ...prev, ...payload.data }))

      if (processing_status === 'completed') {
        // 向量化完成即可开始使用：先退出加载页；后端仍在补写摘要时继续轮询（有上限），
        // 摘要随 payload 合并进当前文档后自然显示
        if (!summaryWaitStartedRef.current) {
          getDoc().setIsLoading(false)
          invalidateHistoryCache()
        }
        if (payload.data.summary_pending) {
          summaryWaitStartedRef.current = summaryWaitStartedRef.current || Date.now()
          return Date.now() - summaryWaitStartedRef.current > SUMMARY_WAIT_LIMIT_MS
        }
        summaryWaitStartedRef.current = 0
        return true
      }

      if (processing_status === 'failed') {
        const errorMsg = payload.data.processing_error || '文档处理失败，请重试'
        getDoc().setError(errorMsg)

        try {
          await apiRequest(`/api/document/${documentId}`, { method: 'DELETE' })
        } catch {
          console.warn('Failed to delete failed document')
        }

        getDoc().setIsLoading(false)
        getDoc().setShowHome(false)
        getDoc().setCurrentDocument(null)
        return true
      }

      return false
    } catch (err) {
      void err
      return false
    }
  }

  const startDocumentStatusPolling = useCallback((documentId, sessionId) => {
    if (!documentId) return

    stopDocumentStatusPolling()
    summaryWaitStartedRef.current = 0

    const pollInterval = setInterval(async () => {
      const isCompleted = await pollDocumentStatus(documentId, sessionId)
      if (isCompleted) {
        stopDocumentStatusPolling()
      }
    }, TASK_POLLING_INTERVAL_MS)

    pollingIntervalRef.current = pollInterval
    useDocumentStore.setState({ _pollIntervalId: pollInterval })

    // Do one immediate check so users do not wait for the first interval tick.
    void (async () => {
      const isCompleted = await pollDocumentStatus(documentId, sessionId)
      if (isCompleted) {
        stopDocumentStatusPolling()
      }
    })()
  }, [stopDocumentStatusPolling])

  // ========== handleBackHome ==========
  const handleBackHome = () => {
    documentUpdatesEnabledRef.current = false
    activeDocumentIdRef.current = null
    getDoc().setIsLoading(false)
    getDoc().setShowHome(true)
    getDoc().setSelectedNode(null)
    clearDocumentStates()
    getDoc().setError(null)
    // 触发主页刷新，但由于 HistoryCards 已经修改为静默刷新，不会再出现骨架屏
    getDoc().incrementHomeRefreshKey()
    // 留下历史记录：回到首页后按浏览器后退，能回到刚才的文档
    if (window.location.pathname !== '/' || window.location.search) {
      navigate('/')
    }
  }

  // ========== handleNewChat ==========
  const handleNewChat = async () => {
    const { currentDocument } = getDoc()
    getDoc().setPendingInitialMessage(null)
    getDoc().setDocumentFlowchart(null)
    getDoc().setIsGeneratingFlowchart(false)
    const newSessionId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`

    if (currentDocument) {
      getDoc().setSessionInfo({ id: newSessionId, docId: currentDocument.document_id })
      localStorage.setItem(`document_${currentDocument.document_id}_sessionId`, newSessionId)
      await ensureSessionExists(currentDocument.document_id, newSessionId, '新对话')
    }

    getDoc().incrementChatSessionKey()
  }

  // ========== executeDeleteChat ==========
  const executeDeleteChat = async () => {
    const { currentDocument, sessionInfo } = getDoc()
    if (!currentDocument || !sessionInfo.id) return

    try {
      const response = await apiRequest(
        `/api/document/${currentDocument.document_id}/history?session_id=${sessionInfo.id}`,
        { method: 'DELETE' }
      )

      if (response.ok) {
        const newSessionId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`

        if (currentDocument) {
          getDoc().setSessionInfo({ id: newSessionId, docId: currentDocument.document_id })
          localStorage.setItem(`document_${currentDocument.document_id}_sessionId`, newSessionId)
        }

        getDoc().incrementChatSessionKey()
        getDoc().incrementSessionsRefreshKey()
      } else {
        alert('删除对话失败，请重试')
      }
    } catch (err) {
      void err
      alert('删除对话失败，请重试')
    }
  }

  // ========== handleDeleteChat ==========
  const handleDeleteChat = () => {
    const { currentDocument, sessionInfo } = getDoc()
    if (!currentDocument || !sessionInfo.id) return

    const dontShowAgain = localStorage.getItem('dontShowDeleteConfirm') === 'true'

    if (dontShowAgain) {
      executeDeleteChat()
    } else {
      getUI().setDeleteConfirmState({ isOpen: true, type: 'chat' })
    }
  }

  // ========== handleLoadSession ==========
  const handleLoadSession = (sessionId) => {
    const { currentDocument } = getDoc()
    if (currentDocument) {
      getDoc().setSessionInfo({ id: sessionId, docId: currentDocument.document_id })
      localStorage.setItem(`document_${currentDocument.document_id}_sessionId`, sessionId)
    }
    getDoc().setDocumentFlowchart(null)
    getDoc().setIsGeneratingFlowchart(false)
    getDoc().incrementChatSessionKey()
  }

  // ========== handleSaveDocName ==========
  const handleSaveDocName = async () => {
    const { currentDocument, editedDocName } = getDoc()
    if (!currentDocument || !editedDocName.trim()) {
      getDoc().setIsEditingDocName(false)
      return
    }

    try {
      const response = await apiRequest(`/api/document/${currentDocument.document_id}/rename`, {
        method: 'PUT',
        body: JSON.stringify({ new_name: editedDocName.trim() })
      })

      if (response.ok) {
        const data = await response.json()
        if (data.success && documentUpdatesEnabledRef.current) {
          getDoc().setCurrentDocument((prev) => {
            if (!documentUpdatesEnabledRef.current) return prev
            return { ...prev, display_name: editedDocName.trim() }
          })
          getDoc().setIsEditingDocName(false)
          invalidateHistoryCache()
          getDoc().invalidateDocumentHistoryCache(currentDocument.document_id)
        }
      } else {
        alert('重命名失败，请重试')
      }
    } catch (err) {
      void err
      alert('重命名失败，请重试')
    }
  }

  // ========== handleStartEditDocName ==========
  const handleStartEditDocName = () => {
    const { currentDocument } = getDoc()
    getDoc().setEditedDocName(currentDocument.display_name || currentDocument.filename)
    getDoc().setIsEditingDocName(true)
  }

  // ========== handleCancelEditDocName ==========
  const handleCancelEditDocName = () => {
    getDoc().setIsEditingDocName(false)
    getDoc().setEditedDocName('')
  }

  // ========== handleDocumentUploadFromHome ==========
  const handleDocumentUploadFromHome = async ({ type, file, content, url, teamId }) => {
    const user = getAuth().user
    // 本地版免登录（user 为本地单用户），只有 SaaS 未登录时才引导登录
    if (!user && !isLocalRuntime()) {
      getAuth().setShowAuthModal(true)
      return
    }

    if (type === 'file' && file && file.size > MAX_UPLOAD_FILE_SIZE_BYTES) {
      getDoc().setError(`文件过大：当前 ${Math.ceil(file.size / 1024 / 1024)}MB，最大支持 ${MAX_UPLOAD_FILE_SIZE_MB}MB`)
      getDoc().setShowHome(true)
      return
    }

    documentUpdatesEnabledRef.current = true
    // 🔧 先渲染 loading 遮罩（position:fixed 覆盖全屏），再延迟卸载首页，
    //    这样 loading 层先盖住首页，下一帧才移除首页 DOM，彻底消除闪烁。
    useDocumentStore.setState({
      isLoading: true,
      loadingType: 'document',
      processingProgress: 0,
      processingStage: '正在处理中...٩(๑•̀ω•́๑)۶',
      error: null,
    })
    // 下一帧再卸载首页，确保 loading 遮罩已经绘制
    requestAnimationFrame(() => {
      useDocumentStore.setState({ showHome: false })
    })

    try {
      let response

      if (type === 'url') {
        response = await apiRequest('/api/document/upload-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url })
        })
      } else {
        const formData = new FormData()

        if (type === 'file') {
          formData.append('file', file)
        } else if (type === 'text') {
          formData.append('text', content)
          formData.append('filename', '粘贴的文本.txt')
        }

        if (teamId) {
          formData.append('team_id', teamId)
        }

        response = await apiRequest('/api/document/upload', {
          method: 'POST',
          body: formData
        })
      }

      if (response.status === 401) {
        // 本地版不存在登录：401 只可能是后端配置异常，不能提示「请先登录」
        getDoc().setError(isLocalRuntime() ? '本地服务拒绝了请求（401），请检查后端配置后重试' : '请先登录后使用此功能')
        getDoc().setShowHome(true)
        getAuth().setShowAuthModal(true)
        return
      }

      if (response.status === 413) {
        throw new Error(`文件过大，最大支持 ${MAX_UPLOAD_FILE_SIZE_MB}MB，请压缩后重试`)
      }

      if (!response.ok) {
        throw new Error('上传失败')
      }

      const data = await response.json()
      if (!documentUpdatesEnabledRef.current) {
        getDoc().setIsLoading(false)
        return
      }
      if (data.success) {
        clearDocumentStates(false)

        const { document_id, processing_status } = data.data

        activeDocumentIdRef.current = document_id
        getDoc().setCurrentDocument(data.data)
        getDoc().setShowHome(false)
        invalidateHistoryCache()
        getDoc().invalidateDocumentHistoryCache(document_id)

        // 同步 URL 避免 App.jsx 路由同步 effect 误判为首页并跳回；留下历史记录，后退能回到首页
        navigate(`/?document_id=${document_id}`)

        const newSessionId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
        getDoc().setSessionInfo({ id: newSessionId, docId: document_id })
        localStorage.setItem(`document_${document_id}_sessionId`, newSessionId)

        await ensureSessionExists(document_id, newSessionId, '新对话')

        if (processing_status === 'pending' || processing_status === 'processing') {
          startDocumentStatusPolling(document_id, newSessionId)
        } else {
          stopDocumentStatusPolling()
          getDoc().setIsLoading(false)
        }
      } else {
        throw new Error(data.message || '上传失败')
      }
    } catch (err) {
      let errorMessage = err.message || '上传文档失败，请重试'

      if (err.name === 'TypeError' && err.message === 'Failed to fetch') {
        errorMessage = '网络连接失败，请检查后端服务是否运行'
      } else if (err.message?.includes('ERR_HTTP2') || err.message?.includes('PING_FAILED')) {
        errorMessage = '连接超时，请稍后重试'
      } else if (err.message?.includes('NetworkError') || err.message?.includes('network')) {
        errorMessage = '网络错误，请检查网络连接'
      }

      // 🔧 合并为单次 set()，避免中间状态闪烁
      useDocumentStore.setState({
        error: errorMessage,
        isLoading: false,
        showHome: false,
      })
    }
  }

  // ========== handleDocumentHistorySelect ==========
  const handleDocumentHistorySelect = (documentData) => {
    const fallbackName = documentData?.display_name
      || documentData?.filename
      || documentData?.extracted_title
      || (documentData?.document_id ? `文档 ${documentData.document_id}` : '未命名文档')

    const normalizedDocument = {
      ...documentData,
      display_name: documentData?.display_name || documentData?.extracted_title || documentData?.filename || fallbackName,
      filename: documentData?.filename || documentData?.display_name || documentData?.extracted_title || fallbackName,
    }

    const { currentDocument, sessionInfo } = getDoc()
    const isSameDocument = currentDocument?.document_id === normalizedDocument.document_id

    // 先同步 URL，避免 state 先更新导致短暂处于 "/ 且无 document_id" 的窗口，
    // 被 App 的路由同步 effect 误判为首页。
    const currentSearchParams = new URLSearchParams(window.location.search)
    if (currentSearchParams.get('document_id') !== String(normalizedDocument.document_id)) {
      navigate(`/?document_id=${normalizedDocument.document_id}`)
    }

    if (!isSameDocument) {
      clearDocumentStates(false)
    }

    if (normalizedDocument?.document_id) {
      activeDocumentIdRef.current = normalizedDocument.document_id
    }
    documentUpdatesEnabledRef.current = true
    // 🔧 合并为单次 set()，避免中间状态闪烁
    useDocumentStore.setState({
      currentDocument: normalizedDocument,
      showHome: false,
      error: null,
    })

    const savedSessionId = localStorage.getItem(`document_${normalizedDocument.document_id}_sessionId`)

    let targetSessionId = savedSessionId
    if (!targetSessionId) {
      targetSessionId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
      localStorage.setItem(`document_${normalizedDocument.document_id}_sessionId`, targetSessionId)
    }

    if (sessionInfo.docId !== normalizedDocument.document_id || sessionInfo.id !== targetSessionId) {
      getDoc().setSessionInfo({ id: targetSessionId, docId: normalizedDocument.document_id })
      ensureSessionExists(normalizedDocument.document_id, targetSessionId, '新对话')
    }

    if (normalizedDocument.processing_status === 'pending' || normalizedDocument.processing_status === 'processing') {
      useDocumentStore.setState({
        isLoading: true,
        loadingType: 'document',
        processingProgress: normalizedDocument.processing_progress || 0,
        processingStage: '正在处理中...٩(๑•̀ω•́๑)۶',
      })
      startDocumentStatusPolling(normalizedDocument.document_id, targetSessionId)
    } else {
      stopDocumentStatusPolling()
      getDoc().setIsLoading(false)
    }
  }

  // ========== Effects ==========

  // activeDocumentIdRef 同步
  const currentDocId = useDocumentStore(s => s.currentDocument?.document_id)
  useEffect(() => {
    activeDocumentIdRef.current = currentDocId ?? null
  }, [currentDocId])

  useEffect(() => {
    return () => {
      stopDocumentStatusPolling()
    }
  }, [stopDocumentStatusPolling])

  // session 从 localStorage 同步
  // canCallApi 作为依赖：首帧会话尚未就绪时 ensureSessionExists 会直接返回，就绪后需重跑一次登记会话
  const currentDocForSession = useDocumentStore(s => s.currentDocument)
  const canCallApi = useCanCallApi()
  useEffect(() => {
    if (!currentDocForSession?.document_id) {
      getDoc().setSessionInfo({ id: null, docId: null })
      return
    }

    let isCancelled = false

    const syncSessionFromStorage = async () => {
      const storageKey = `document_${currentDocForSession.document_id}_sessionId`
      let storedSessionId = null

      try {
        storedSessionId = localStorage.getItem(storageKey)
      } catch {
        storedSessionId = null
      }

      if (!storedSessionId) {
        storedSessionId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
        try {
          localStorage.setItem(storageKey, storedSessionId)
        } catch {
          // 忽略本地存储写入错误
        }
      }

      if (!isCancelled) {
        getDoc().setSessionInfo(prev => {
          if (prev.id === storedSessionId && prev.docId === currentDocForSession.document_id) {
            return prev
          }
          return { id: storedSessionId, docId: currentDocForSession.document_id }
        })
      }

      await ensureSessionExists(
        currentDocForSession.document_id,
        storedSessionId,
        currentDocForSession.display_name
          ? `${currentDocForSession.display_name} 对话`
            : '新对话'
      )
    }

    void syncSessionFromStorage()

    return () => { isCancelled = true }
  }, [currentDocForSession?.document_id, currentDocForSession?.display_name, canCallApi])

  // 登录后验证持久化文档
  const user = useAuthStore(s => s.user)
  useEffect(() => {
    if (!user) return
    validatePersistedDocument()
  }, [user, validatePersistedDocument])

  return {
    // Refs (供 App.jsx 和其他 hook 使用)
    activeDocumentIdRef,
    documentUpdatesEnabledRef,
    // Functions
    clearDocumentStates,
    invalidateHistoryCache,
    ensureSessionExists,
    validatePersistedDocument,
    handleAuthSuccess,
    handleLogout,
    handleBackHome,
    handleNewChat,
    executeDeleteChat,
    handleDeleteChat,
    handleLoadSession,
    handleSaveDocName,
    handleStartEditDocName,
    handleCancelEditDocName,
    handleDocumentUploadFromHome,
    handleDocumentHistorySelect,
  }
}
