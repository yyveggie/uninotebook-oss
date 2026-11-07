import React, { useState, useEffect, useRef, useCallback, useMemo, Suspense, lazy } from 'react'
import { Routes, Route, useNavigate, useLocation, useParams } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary'
import Navbar from './components/Navbar'
import ResizableLayout from './components/ResizableLayout'
import PromptTemplateModal from './components/PromptTemplateModal'
const PdfPreviewPanel = lazy(() => import('./components/PdfViewer'))
import { clearExpiredCache } from './utils/cache'
import { fetchDocumentStatus } from './utils/documentStatusCache'
import { getAccessToken } from './utils/api'
import useAuthStore, { useCanCallApi } from './stores/authStore'
import useDocumentStore from './stores/documentStore'
import useUIStore from './stores/uiStore'
import useDocumentActions from './hooks/useDocumentActions'
import useGenerationActions from './hooks/useGenerationActions'
import usePromptActions from './hooks/usePromptActions'
import useRuntimeStore from './stores/runtimeStore'
import './App.css'

import RotatingLoadingText from './components/RotatingLoadingText'

const AuthModal = lazy(() => import('./components/AuthModal'))
const LandingPage = lazy(() => import('./components/LandingPage'))
const ConfirmDialog = lazy(() => import('./components/ConfirmDialog'))
const MiniNeuralNetwork = lazy(() => import('./components/MiniNeuralNetwork'))
const DocumentChat = lazy(() => import('./components/DocumentChat'))
const DocumentInfo = lazy(() => import('./components/DocumentInfo'))
const WorkspaceDashboard = lazy(() => import('./components/WorkspaceDashboard'))
const KnowledgeGraphPage = lazy(() => import('./components/KnowledgeGraphPage'))
const EurekaChatPage = lazy(() => import('./components/EurekaChatPage'))
const RightPanel = lazy(() => import('./components/RightPanel'))
const ServiceAgreement = lazy(() => import('./components/ServiceAgreement'))
const DataPolicy = lazy(() => import('./components/DataPolicy'))
const StudioPage = lazy(() => import('./components/CreativeStudio/StudioPage'))
const RechargePage = lazy(() => import('./components/RechargePage'))
const SettingsPage = lazy(() => import('./components/SettingsPage'))
const PolishPage = lazy(() => import('./components/PolishPage'))

// EurekaChatPage 包装组件：从 URL 参数获取 eurekaId
function EurekaChatRoute({ onBack, onLogout, onSelectRecharge }) {
  const { id } = useParams()
  const user = useAuthStore(s => s.user)
  return (
    <EurekaChatPage
      eurekaId={id}
      onBack={onBack}
      user={user}
      onLogout={onLogout}
      onSelectRecharge={onSelectRecharge}
    />
  )
}

function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const forceLandingForHash = location.pathname === '/' && Boolean(location.hash)

  const navigateByDomain = useCallback((path, options = {}) => {
    const { replace = false } = options
    navigate(path, { replace })
  }, [navigate])

  // ========== Zustand Stores ==========
  const user = useAuthStore(s => s.user)
  const storeCheckAuth = useAuthStore(s => s.checkAuth)
  const showUserMenu = useAuthStore(s => s.showUserMenu)
  const setShowUserMenu = useAuthStore(s => s.setShowUserMenu)
  const setShowAuthModal = useAuthStore(s => s.setShowAuthModal)
  const setAuthInitialMode = useAuthStore(s => s.setAuthInitialMode)
  const isCheckingAuth = useAuthStore(s => s.isCheckingAuth)

  // 运行时模式与能力清单（开源本地版据此隐藏商业入口、置灰缺条件的功能）
  const isLocalMode = useRuntimeStore(s => s.deployMode === 'local')
  const fetchCapabilities = useRuntimeStore(s => s.fetchCapabilities)

  // 会话就绪：部署模式与登录态都已确定，且可以调用需鉴权的接口
  // （本地版恒可调用；SaaS 需已登录）。工作区及各加载器以此为准，
  // 避免刷新页面时在 bootstrap 完成前就发请求、或在本地版因「没有 token」而永不加载。
  const sessionReady = useCanCallApi()

  const {
    currentDocument,
    showHome, setShowHome,
    isLoading,
    error, setError,
    pendingInitialMessage,
    sessionInfo,
    sessionsRefreshKey, incrementSessionsRefreshKey,
    homeRefreshKey,
    isEditingDocName,
    editedDocName, setEditedDocName,
    setSelectedNode,
    setCompletedNodes,
    setExpandDialogNode,
    setAIDialogNode,
    documentFlowchart, flashcards, quiz, mindmap, highlights, report,
    isGeneratingFlowchart, isGeneratingFlashcards,
    isGeneratingQuiz, isGeneratingMindmap, isGeneratingHighlights, isGeneratingReport,
    rightPanelView, setRightPanelView,
    maximizedPanel, toggleMaximize,
    invalidateDocumentHistoryCache,
  } = useDocumentStore()

  const {
    deleteConfirmState, setDeleteConfirmState,
    promptTemplates,
    showPromptModal, setShowPromptModal,
    editingTemplate, setEditingTemplate,
    toasts, dismissToast,
  } = useUIStore()

  // ========== 派生状态 ==========
  const homeView = useMemo(() => {
    const path = location.pathname
    if (path === '/knowledgegraph') return 'knowledgeGraph'
    if (path.startsWith('/eureka-chat/')) return 'eurekaChat'
    if (path === '/legal/user-service-agreement' || path === '/legal/terms-of-service') return 'terms'
    if (path === '/legal/privacy-policy') return 'privacy'
    if (path === '/studio') return 'creativeStudio'
    if (path === '/polish') return 'polish'
    if (path === '/recharge') return 'recharge'
    if (path === '/settings') return 'settings'
    // 本地版免登录：打开即进入工作区，任何情况下都不展示落地页
    return (user || isLocalMode) ? 'workspace' : 'landing'
  }, [location.pathname, user, isLocalMode])

  // SaaS 未登录时即使恢复出了上次打开的文档，也回到首页（落地页）而非渲染空壳工作区
  const showHomeView = showHome || (!isCheckingAuth && !sessionReady)

  // ========== 本地状态 (非共享) ==========
  const [initialUrlDocumentId] = useState(() => {
    try {
      const params = new URLSearchParams(window.location.search)
      const raw = params.get('document_id') || params.get('doc')
      if (!raw) return null
      const id = parseInt(raw, 10)
      return Number.isNaN(id) ? null : id
    } catch {
      return null
    }
  })
  const [initialUrlHandled, setInitialUrlHandled] = useState(false)
  const [, setLeftSidebarWidth] = useState(250)
  const [, setRightSidebarWidth] = useState(420)
  const [isResizingLeft, setIsResizingLeft] = useState(false)
  const [isResizingRight, setIsResizingRight] = useState(false)
  const [selectedSourceIds, setSelectedSourceIds] = useState([])
  const [leftPanelTab, setLeftPanelTab] = useState('info') // 'info' | 'pdf'

  // Reset PDF tab when switching documents
  useEffect(() => { setLeftPanelTab('info') }, [currentDocument?.document_id])

  useEffect(() => {
    setSelectedSourceIds([])
  }, [currentDocument?.document_id])

  // ========== Refs ==========
  const expandAbortControllerRef = useRef(null)
  const rightPanelRef = useRef(null)
  const documentChatRef = useRef(null)
  const rechargeReturnRef = useRef({ fromPath: '/', fromShowHome: true })

  // ========== Custom Hooks ==========
  const {
    clearDocumentStates,
    handleAuthSuccess, handleLogout, handleBackHome,
    handleNewChat, executeDeleteChat, handleDeleteChat,
    handleLoadSession, handleSaveDocName, handleStartEditDocName, handleCancelEditDocName,
    handleDocumentUploadFromHome, handleDocumentHistorySelect,
  } = useDocumentActions()

  const {
    handleGenerateFlowchart, handleGenerateFlashcards, handleGenerateQuiz,
    handleGenerateMindmap, handleGenerateHighlights, handleGenerateReport,
    handleStopAllGenerations,
  } = useGenerationActions(rightPanelRef)

  const {
    handleTemplateClick, handleTemplateEdit, handleTemplateSave, handleTemplateDelete,
    handleQuickKeyPointsLogic, handleQuickCentralIdea, handleQuickProsCons, handleQuickSimpleExample,
  } = usePromptActions(documentChatRef)

  // ========== Effects ==========

  // 启动时拉取运行时能力清单：界面据此把「缺 Key / 缺依赖」的功能入口置灰。
  // 该接口免登录可访问——本地版首次启动时用户还什么都没配。
  useEffect(() => {
    fetchCapabilities()
  }, [fetchCapabilities])

  // 保存右侧面板视图状态到 localStorage
  useEffect(() => {
    localStorage.setItem('rightPanelView', rightPanelView)
  }, [rightPanelView])

  const urlSyncedDocumentIdRef = useRef(null)
  const urlDocumentLoadRef = useRef(null)

  // 保存当前文档状态到 localStorage，并同步 showHome 和 URL
  // 当 showHome=true（用户在首页/知识网络等页面）时，仅保存 localStorage，不执行跳转
  useEffect(() => {
    if (currentDocument) {
      localStorage.setItem('currentDocument', JSON.stringify(currentDocument))

      // 如果当前处于首页视图（知识网络、创作空间等），不要强制跳转到文档页面
      if (showHome) return

      setShowHome(false)

      if (initialUrlDocumentId && !initialUrlHandled && String(currentDocument.document_id) !== String(initialUrlDocumentId)) {
        return
      }

      // 用户未登录时不操作 URL，避免和浏览器后退按钮"打架"
      if (!user) return

      // 只在切换文档时补写地址。浏览器后退时地址先变、showHome 后变，
      // 若每次都比较，这里会把地址改回文档页，后退就失效了
      if (urlSyncedDocumentIdRef.current === currentDocument.document_id) return
      urlSyncedDocumentIdRef.current = currentDocument.document_id

      const currentSearchParams = new URLSearchParams(window.location.search)
      if (currentSearchParams.get('document_id') !== String(currentDocument.document_id)) {
        // 使用 replace: true 避免在历史记录中留下多余的记录，导致后退按钮需要点多次
        navigate(`/?document_id=${currentDocument.document_id}`, { replace: true })
      }
    } else {
      urlSyncedDocumentIdRef.current = null
      localStorage.removeItem('currentDocument')
    }
  }, [
    currentDocument,
    showHome,
    user,
    initialUrlDocumentId,
    initialUrlHandled,
    location.search,
    navigate,
  ])

  // 检查 URL 是否需要自动打开认证模态框
  // 用 ref 防止同一路径重复触发（避免与 modal 的路由操作形成循环）
  const authUrlHandledRef = useRef('')
  useEffect(() => {
    // 部署模式与登录态确定之前不做判断：否则本地版（或已登录用户）会闪出登录框
    if (isCheckingAuth) return

    const params = new URLSearchParams(location.search)
    const authPaths = { '/signin': 'login', '/signup': 'register', '/forgot-password': 'forgotPassword' }

    // 本地版没有登录/注册/邀请：旧链接直接回到工作区
    if (isLocalMode) {
      if (authPaths[location.pathname] || params.get('invite_code')) {
        navigate('/', { replace: true })
      }
      return
    }

    if (user || getAccessToken()) return

    // 检查是否有邀请码
    const inviteCode = params.get('invite_code')
    if (inviteCode) {
      setAuthInitialMode('register')
      setShowAuthModal(true)
      // 将邀请码存入 localStorage 供 AuthModal 使用
      localStorage.setItem('pending_invite_code', inviteCode)
      // 清理 URL
      navigate('/', { replace: true })
      return
    }

    const mode = authPaths[location.pathname]
    if (mode && authUrlHandledRef.current !== location.pathname) {
      authUrlHandledRef.current = location.pathname
      setAuthInitialMode(mode)
      setShowAuthModal(true)
      setTimeout(() => navigate('/', { replace: true }), 0)
    }
  }, [location.pathname, location.search, navigate, user, isCheckingAuth, isLocalMode])

  // 如果 URL 中携带 document_id，则在初次加载时直接打开对应文档
  // 须等会话就绪：本地版永远没有 token，SaaS 刷新时 bootstrap 也可能尚未完成——
  // 以 sessionReady 为依赖，就绪后会自动重跑，而不是首帧判空后再也不处理
  useEffect(() => {
    if (!initialUrlDocumentId || initialUrlHandled) return
    if (!sessionReady) return

    const openFromUrl = async () => {
      try {
        const statusResult = await fetchDocumentStatus(initialUrlDocumentId, { useCache: false })
        if (!statusResult.ok || !statusResult.data?.success || !statusResult.data.data) {
          return
        }
        const payload = statusResult.data.data
        const documentData = {
          document_id: payload.document_id,
          filename: payload.filename,
          display_name: payload.display_name || payload.filename,
          summary: payload.summary,
          file_size: payload.file_size,
          file_type: payload.file_type,
          chunk_count: payload.chunk_count,
          created_at: payload.created_at,
        }
        handleDocumentHistorySelect(documentData)
      } catch (err) {
        void err;
      } finally {
        setInitialUrlHandled(true)
      }
    }

    void openFromUrl()
  }, [initialUrlDocumentId, initialUrlHandled, sessionReady])

  // 清理过期缓存
  useEffect(() => {
    clearExpiredCache()
  }, [])

  // 检查登录状态
  useEffect(() => {
    storeCheckAuth()
  }, [])

  // React Router 自动处理 popstate，当浏览器前进/后退时同步 showHome
  useEffect(() => {
    // 地址一律读浏览器当前值：navigate() 已同步改写地址栏，路由状态稍后才更新，
    // 而文档 store 同步更新。读路由状态会在这一瞬间误判页面（见下方 document_id 分支的说明）
    const path = window.location.pathname
    const hash = window.location.hash

    // 前进 / 后退到某篇文档的地址时打开它；文档已被删除则回到首页
    const openDocumentFromUrl = async (documentId) => {
      if (urlDocumentLoadRef.current === documentId) return
      urlDocumentLoadRef.current = documentId
      try {
        const statusResult = await fetchDocumentStatus(documentId, { useCache: true })
        if (statusResult.ok && statusResult.data?.success && statusResult.data.data) {
          const payload = statusResult.data.data
          handleDocumentHistorySelect({
            document_id: payload.document_id,
            filename: payload.filename,
            display_name: payload.display_name || payload.filename,
            summary: payload.summary,
            file_size: payload.file_size,
            file_type: payload.file_type,
            chunk_count: payload.chunk_count,
            created_at: payload.created_at,
          })
        } else {
          setShowHome(true)
          navigate('/', { replace: true })
        }
      } catch (err) {
        console.error('Failed to load document from history', err)
        setShowHome(true)
      } finally {
        urlDocumentLoadRef.current = null
      }
    }

    const scrollToHashTarget = () => {
      if (!hash || hash.length <= 1) return
      const targetId = decodeURIComponent(hash.slice(1))
      if (!targetId) return

      let attempts = 0
      const maxAttempts = 8

      const tryScroll = () => {
        const target = document.getElementById(targetId)
        if (target) {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' })
          return
        }
        attempts += 1
        if (attempts < maxAttempts) {
          window.setTimeout(tryScroll, 60)
        }
      }

      window.setTimeout(tryScroll, 0)
    }

    // 当后退/前进到 hash 历史项（如 /#showcase）时，优先切回首页视图
    // 避免 URL 变化但仍停留在文档详情页，导致“页面看起来没变化”
    if (path === '/' && hash) {
      setShowHome(true)
      scrollToHashTarget()
      return
    }

    if (path === '/knowledgegraph' || path.startsWith('/eureka-chat/') ||
      path === '/studio' || path === '/polish' || path === '/settings' || path.startsWith('/legal/')) {
      setShowHome(true)
    } else if (path === '/' && !currentDocument && !isLoading) {
      // 从首页后退到文档地址：此时文档状态已清空，需要按地址重新打开。
      // 首次打开页面时带 document_id 的情况由下方的 openFromUrl 处理
      const documentIdInUrl = new URLSearchParams(window.location.search).get('document_id')
      if (documentIdInUrl && (!initialUrlDocumentId || initialUrlHandled)) {
        openDocumentFromUrl(documentIdInUrl)
      } else {
        setShowHome(true)
      }
    } else if (path === '/' && currentDocument) {
      // 如果在文档页，且 URL 中没有 document_id，说明可能是后退到了首页。
      // 这里若读 location.search，打开文档的一瞬间会误判为「后退到首页」：
      // 首页被重新挂载后又切回文档页，整屏闪一下。
      const currentSearchParams = new URLSearchParams(window.location.search)
      const docIdInUrl = currentSearchParams.get('document_id')
      if (!docIdInUrl) {
        setShowHome(true)
      } else if (docIdInUrl !== String(currentDocument.document_id)) {
        // 如果 URL 中的 document_id 和当前文档不一致，说明用户通过前进/后退切换了文档
        // 触发重新加载文档
        openDocumentFromUrl(docIdInUrl)
      } else {
        // URL 与当前文档一致时，确保进入文档详情视图
        setShowHome(false)
      }
    }
  }, [location.pathname, location.search, location.hash, currentDocument, isLoading, initialUrlHandled])

  // 点击外部关闭用户菜单
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (showUserMenu && !event.target.closest('.user-menu') && !event.target.closest('.home-user-menu')) {
        setShowUserMenu(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [showUserMenu])

  // ========== Node / Panel Handlers ==========

  const handleToggleNodeComplete = useCallback((nodeId) => {
    setCompletedNodes(prev => {
      const newSet = new Set(prev)
      if (newSet.has(nodeId)) {
        newSet.delete(nodeId)
      } else {
        newSet.add(nodeId)
      }
      return newSet
    })
  }, [])

  const handleMaximize = (panel) => {
    toggleMaximize(panel)
  }

  const handleSendQuestionToChat = (question) => {
    if (documentChatRef.current && documentChatRef.current.sendQuestion) {
      documentChatRef.current.sendQuestion(question)
    }
  }

  const handleSetChatInput = (data, mode = 'edit') => {
    if (documentChatRef.current) {
      if (mode === 'reference' && documentChatRef.current.setReferenceContent) {
        documentChatRef.current.setReferenceContent(data)
      } else if (documentChatRef.current.setInputText) {
        documentChatRef.current.setInputText(typeof data === 'string' ? data : (data.content || ''))
      }
    }
  }

  const handleSelectRecharge = useCallback(() => {
    rechargeReturnRef.current = {
      fromPath: `${location.pathname}${location.search || ''}`,
      fromShowHome: showHome,
    }

    setShowHome(true)
    navigate('/recharge', {
      state: rechargeReturnRef.current,
    })
  }, [location.pathname, location.search, showHome, navigate, setShowHome])

  const handleCloseRecharge = useCallback((payload = {}) => {
    const fromPath = payload.fromPath || rechargeReturnRef.current.fromPath || '/'
    const fromShowHome = typeof payload.fromShowHome === 'boolean'
      ? payload.fromShowHome
      : rechargeReturnRef.current.fromShowHome

    setShowHome(fromShowHome)
    navigate(fromPath, { replace: true })
  }, [navigate, setShowHome])

  // 将函数暴露给全局，供D3节点按钮调用
  useEffect(() => {
    window.toggleNodeComplete = handleToggleNodeComplete
    window.openExpandDialog = (node, position) => {
      if (expandAbortControllerRef.current) {
        expandAbortControllerRef.current.abort()
      }

      setSelectedNode(null)
      setAIDialogNode(null)
      setExpandDialogNode({
        ...node,
        clickPosition: position
      })
    }
    window.openAIDialog = (node, position) => {
      if (expandAbortControllerRef.current) {
        expandAbortControllerRef.current.abort()
      }

      setSelectedNode(null)
      setExpandDialogNode(null)
      setAIDialogNode({
        ...node,
        clickPosition: position
      })
    }

    return () => {
      delete window.toggleNodeComplete
      delete window.openExpandDialog
      delete window.openAIDialog
    }
  }, [handleToggleNodeComplete])

  // 处理左侧边栏拖拽调整大小
  useEffect(() => {
    const handleMouseMove = (e) => {
      if (isResizingLeft) {
        const newWidth = e.clientX
        if (newWidth >= 250 && newWidth <= 500) {
          setLeftSidebarWidth(newWidth)
        }
      }
      if (isResizingRight) {
        const newWidth = window.innerWidth - e.clientX
        if (newWidth >= 300 && newWidth <= 600) {
          setRightSidebarWidth(newWidth)
        }
      }
    }

    const handleMouseUp = () => {
      setIsResizingLeft(false)
      setIsResizingRight(false)
    }

    if (isResizingLeft || isResizingRight) {
      document.addEventListener('mousemove', handleMouseMove)
      document.addEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'

      return () => {
        document.removeEventListener('mousemove', handleMouseMove)
        document.removeEventListener('mouseup', handleMouseUp)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }
  }, [isResizingLeft, isResizingRight])

  // ========== Render ==========
  return (
    <div className="app">
      {/* 统一首页 */}
      {/* 🆕 防止闪烁：在检查 Auth 期间显示加载中，不渲染首页或 LandingPage */}
      {isCheckingAuth ? (
        <div className="loading-container loading-container-auth-check">
          <div className="loading-content">
            <div className="neural-spinner-wrapper">
              <Suspense fallback={<div className="mini-neural-fallback-small" />}>
                <MiniNeuralNetwork width={120} height={120} />
              </Suspense>
            </div>
            <div className="auth-checking-text">正在进入...请稍等</div>
          </div>
        </div>
      ) : showHomeView && (
        <div className="home-page">
          {/* EurekaChatPage 使用自定义 Navbar，其他页面使用默认 Navbar */}
          {homeView !== 'eurekaChat' && (
            <Navbar
              onLogout={handleLogout}
              onLogoClick={() => {
                setShowHome(true)
                navigateByDomain('/', { forceSubdomain: user ? 'space' : 'www' })
              }}
              activeNav={homeView === 'knowledgeGraph' ? 'knowledgeGraph' : homeView === 'creativeStudio' ? 'creativeStudio' : homeView === 'polish' ? 'polish' : homeView === 'recharge' ? 'recharge' : 'workspace'}
              onSelectWorkspace={() => {
                setShowHome(true)
                navigateByDomain('/', { forceSubdomain: user ? 'space' : 'www' })
              }}
              onSelectKnowledgeGraph={() => {
                setShowHome(true)
                navigateByDomain('/knowledgegraph')
              }}
              onSelectPolish={() => {
                setShowHome(true)
                navigateByDomain('/polish')
              }}
              onSelectCreativeStudio={() => {
                setShowHome(true)
                navigate('/studio')
              }}
              onSelectRecharge={handleSelectRecharge}
              onSelectSettings={() => navigate('/settings')}
              hideLoginButton={!user && homeView === 'landing'}
            />
          )}
          <ErrorBoundary resetKey={location.pathname}>
          <Suspense fallback={<div className="loading-container"><div className="loading-content" /></div>}>
            <Routes>
              {/* 服务条款类页面。本地部署是用户自己运行的软件，
                  不存在服务提供方与数据收集方，这些条款不适用。 */}
              {!isLocalMode && (
                <>
                  <Route path="/legal/user-service-agreement" element={<ServiceAgreement />} />
                  <Route path="/legal/terms-of-service" element={<ServiceAgreement />} />
                  <Route path="/legal/privacy-policy" element={<DataPolicy />} />
                </>
              )}
              <Route path="/knowledgegraph" element={
                <KnowledgeGraphPage onOpenDocument={handleDocumentHistorySelect} />
              } />
              <Route path="/eureka-chat/:id" element={
                <EurekaChatRoute
                  onBack={() => navigate('/knowledgegraph')}
                  onLogout={handleLogout}
                  onSelectRecharge={handleSelectRecharge}
                  onSelectSettings={() => navigate('/settings')}
                />
              } />
              <Route path="/studio" element={
                <StudioPage
                  onBack={() => navigateByDomain('/', { forceSubdomain: user ? 'space' : 'www' })}
                />
              } />
              <Route path="/polish" element={
                <PolishPage
                  onBack={() => navigateByDomain('/', { forceSubdomain: user ? 'space' : 'www' })}
                />
              } />
              {/* 计费页：本地部署不计费，故不注册该路由 */}
              {!isLocalMode && (
                <Route path="/recharge" element={
                  <RechargePage onClose={handleCloseRecharge} />
                } />
              )}
              {/* API Key 设置页（仅本地版） */}
              {isLocalMode && (
                <Route path="/settings" element={
                  <SettingsPage onClose={() => navigate('/')} />
                } />
              )}
              <Route path="*" element={
                // 本地版：没有落地页，打开即是工作区
                (isLocalMode || (!forceLandingForHash && user)) ? (
                  <WorkspaceDashboard
                    onDocumentUpload={handleDocumentUploadFromHome}
                    onDocumentHistorySelect={handleDocumentHistorySelect}
                    refreshKey={homeRefreshKey}
                  />
                ) : (
                  <LandingPage
                    onStartLearning={() => navigate('/')}
                  />
                )
              } />
            </Routes>
          </Suspense>
          </ErrorBoundary>
        </div>
      )}

      {!showHome && sessionReady && !isLoading && !currentDocument && (
        <Navbar
          onLogout={handleLogout}
          onLogoClick={handleBackHome}
          onSelectRecharge={handleSelectRecharge}
          onSelectSettings={() => navigate('/settings')}
        />
      )}

      {isLoading && (
        <div className="loading-container">
          <div className="loading-content">
            <>
              <div className="neural-spinner-wrapper">
                <Suspense fallback={<div className="mini-neural-fallback" />}>
                  <MiniNeuralNetwork width={360} height={360} />
                </Suspense>
              </div>

              {/* [2026-02-05] 三段文字轮换显示 */}
              <RotatingLoadingText />

            </>
          </div>
        </div>
      )}

      {error && (
        <div className="error-container">
          <div className="processing-error-container">
            <div className="processing-error-icon">🪐</div>
            <h3 className="processing-error-title">无法完成解析</h3>
            <p className="processing-error-message">
              {error}
            </p>
            <button
              className="processing-error-back-btn"
              onClick={() => setError(null)}
            >
              返回
            </button>
          </div>
        </div>
      )}

      {/* 文档概览页面 - 上传后显示，点击生成流程图 */}
      {/* 也允许在选中团队但无文档时显示（空状态可上传） */}
      {/* 会话就绪后才挂载工作区：否则刷新页面时聊天、来源、工作室会在登录态恢复前发请求并空载 */}
      {!showHome && sessionReady && currentDocument && !isLoading && !error && (

        <>
          <Navbar
            onLogout={handleLogout}
            onLogoClick={handleBackHome}
            onSelectRecharge={handleSelectRecharge}
            onSelectSettings={() => navigate('/settings')}
            titleComponent={
              isEditingDocName ? (
                <div className="nav-title-edit">
                  <input
                    type="text"
                    className="doc-name-input"
                    value={editedDocName}
                    onChange={(e) => setEditedDocName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleSaveDocName()
                      if (e.key === 'Escape') handleCancelEditDocName()
                    }}
                    autoFocus
                    onBlur={handleSaveDocName}
                  />
                </div>
              ) : currentDocument ? (
                <h2 className="nav-title editable" onClick={handleStartEditDocName} title="点击编辑文档名称">
                  {currentDocument.display_name || currentDocument.filename}
                </h2>
              ) : null
            }
          />


          {/* 文档学习布局：可调整大小的三栏布局。
              单独包一层错误边界：工作区里任何组件渲染出错，只替换这一块并提供重试，
              顶栏仍可用来返回首页，而不是整页白屏 */}
          <ErrorBoundary resetKey={currentDocument.document_id}>
          <div className="document-layout">
            <ResizableLayout
              maximizedPanel={maximizedPanel}
              onMaximize={handleMaximize}
              leftPanel={
                currentDocument ? (
                  leftPanelTab === 'pdf' ? (
                    <div className="left-panel-pdf-wrapper">
                      <div className="info-header left-panel-pdf-header">
                        <button className="back-btn" onClick={() => setLeftPanelTab('info')}>
                          <svg className="back-btn-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#111827" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                        </button>
                        <h3 className="info-title">PDF 原文</h3>
                      </div>
                      <div style={{ flex: 1, overflow: 'hidden' }}>
                        <Suspense fallback={<div className="loading-content" />}>
                          <PdfPreviewPanel
                            documentId={currentDocument.document_id}
                            onSendToChat={(payload) => {
                              if (documentChatRef.current?.setReferenceContent) {
                                documentChatRef.current.setReferenceContent(payload)
                              }
                            }}
                          />
                        </Suspense>
                      </div>
                    </div>
                  ) : (
                    <Suspense fallback={<div className="loading-content" />}>
                      <DocumentInfo
                        document={currentDocument}
                        onLoadSession={handleLoadSession}
                        currentSessionId={sessionInfo.id}
                        refreshKey={sessionsRefreshKey}
                        onDocumentDeleted={() => {
                          invalidateDocumentHistoryCache()
                          clearDocumentStates()
                          setShowHome(true)
                        }}
                        isMaximized={maximizedPanel === 'left'}
                        onToggleMaximize={() => handleMaximize('left')}
                        selectedSourceIds={selectedSourceIds}
                        onSelectedSourcesChange={setSelectedSourceIds}
                        onOpenPdfPreview={(currentDocument.file_type || '').toLowerCase() === 'pdf' ? () => setLeftPanelTab('pdf') : null}
                      />
                    </Suspense>
                  )
                ) : (
                  <div className="document-info-panel document-info-panel-empty">
                    <div className="info-header">
                      <h3 className="info-title">来源</h3>
                    </div>
                  </div>
                )
              }


              centerPanel={
                <div className="chat-panel-wrapper">
                  <div className="chat-header">
                    <div className="chat-title-wrapper">
                      <h3 className="chat-title">对话</h3>
                    </div>

                    {/* 新建和删除按钮 */}
                    <div className="chat-control-buttons">
                      <button
                        className="chat-control-btn"
                        onClick={handleNewChat}
                        title="新建对话"
                      >
                        新建
                      </button>
                      <button
                        className="chat-control-btn delete"
                        onClick={handleDeleteChat}
                        title="删除当前对话"
                      >
                        删除
                      </button>
                    </div>

                    <div className="quick-action-buttons">
                      {/* 如果模板已加载，显示动态模板；否则显示默认按钮 */}
                      {promptTemplates.length > 0 ? (
                        <>
                          {promptTemplates.map(template => (
                            <div key={template.id} className="quick-action-btn-wrapper">
                              <button
                                className="quick-action-btn"
                                onClick={() => handleTemplateClick(template)}
                                title={template.title}
                              >
                                {template.icon} {template.title}
                              </button>
                              <button
                                className="quick-action-edit-btn"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleTemplateEdit(template)
                                }}
                                title="编辑此模板"
                              >
                                🔧
                              </button>
                            </div>
                          ))}
                          {promptTemplates.length < 6 && (
                            <button
                              className="quick-action-btn add-template-btn"
                              onClick={() => {
                                setEditingTemplate(null)
                                setShowPromptModal(true)
                              }}
                              title="添加自定义提示词模板"
                            >
                              +
                            </button>
                          )}
                        </>
                      ) : (
                        <>
                          {/* 默认按钮（未登录或模板未加载时）- 使用相同的wrapper结构 */}
                          <div className="quick-action-btn-wrapper">
                            <button
                              className="quick-action-btn"
                              onClick={handleQuickKeyPointsLogic}
                              title="梳理关键观点并串联论证逻辑"
                            >
                              🧠 观点逻辑
                            </button>
                            <span className="quick-action-edit-btn quick-action-edit-btn-disabled">🔧</span>
                          </div>
                          <div className="quick-action-btn-wrapper">
                            <button
                              className="quick-action-btn"
                              onClick={handleQuickCentralIdea}
                              title="提取作者想传达的核心观点和主旨"
                            >
                              🎯 中心思想
                            </button>
                            <span className="quick-action-edit-btn quick-action-edit-btn-disabled">🔧</span>
                          </div>
                          <div className="quick-action-btn-wrapper">
                            <button
                              className="quick-action-btn"
                              onClick={handleQuickProsCons}
                              title="快速生成优缺点分析"
                            >
                              ⚖️ 优点与缺点
                            </button>
                            <span className="quick-action-edit-btn quick-action-edit-btn-disabled">🔧</span>
                          </div>
                          <div className="quick-action-btn-wrapper">
                            <button
                              className="quick-action-btn"
                              onClick={handleQuickSimpleExample}
                              title="快速生成通俗举例"
                            >
                              💡 通俗举例子
                            </button>
                            <span className="quick-action-edit-btn quick-action-edit-btn-disabled">🔧</span>
                          </div>
                          {user && (
                            <button
                              className="quick-action-btn add-template-btn"
                              onClick={() => {
                                setEditingTemplate(null)
                                setShowPromptModal(true)
                              }}
                              title="添加自定义提示词模板"
                            >
                              +
                            </button>
                          )}
                        </>
                      )}
                    </div>

                    <button
                      className="panel-embedded-maximize-btn"
                      onClick={() => handleMaximize('center')}
                      title={maximizedPanel === 'center' ? '还原' : '最大化'}
                    >
                      {maximizedPanel === 'center' ? (
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3" />
                        </svg>
                      ) : (
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                        </svg>
                      )}
                    </button>
                  </div>
                  <main className="document-chat-main">
                    {/* 对话区域 - 仅在有文档时显示 */}
                    {currentDocument ? (
                      <Suspense fallback={<div className="loading-content" />}>
                        <DocumentChat
                          ref={documentChatRef}
                          documentId={currentDocument.document_id}
                          documentName={currentDocument.filename}
                          summary={currentDocument.summary}
                          flowchart={currentDocument.flowchart}
                          sessionId={sessionInfo.id}
                          initialMessage={pendingInitialMessage}
                          onNewChat={handleNewChat}
                          onDeleteChat={handleDeleteChat}
                          onNoteSaved={() => {
                            setRightPanelView('studio')
                            if (rightPanelRef.current) {
                              rightPanelRef.current.refreshNotes({ forceRefresh: true })
                            }
                          }}
                          onHistoryUpdated={() => incrementSessionsRefreshKey()}
                          eurekaId={currentDocument.eureka_id}
                          eurekaData={currentDocument.eureka_data}
                          selectedSourceIds={selectedSourceIds}
                        />
                      </Suspense>
                    ) : (
                      // 空状态占位符
                      <div className="chat-placeholder app-chat-placeholder-empty">
                        <div className="app-chat-placeholder-icon">💬</div>
                        <div>上传文档后开始对话</div>
                      </div>
                    )}
                  </main>
                </div>
              }
              rightPanel={
                currentDocument ? (
                  <Suspense fallback={<div className="loading-content" />}>
                    <RightPanel
                      ref={rightPanelRef}
                      view={rightPanelView}
                      onViewChange={setRightPanelView}
                      documentId={currentDocument.document_id}
                      onGenerateFlowchart={handleGenerateFlowchart}
                      onGenerateFlashcards={handleGenerateFlashcards}
                      onGenerateQuiz={handleGenerateQuiz}
                      onGenerateMindmap={handleGenerateMindmap}
                      onGenerateHighlights={handleGenerateHighlights}
                      onGenerateReport={handleGenerateReport}
                      isGeneratingFlowchart={isGeneratingFlowchart}
                      isGeneratingFlashcards={isGeneratingFlashcards}
                      isGeneratingQuiz={isGeneratingQuiz}
                      isGeneratingMindmap={isGeneratingMindmap}
                      isGeneratingHighlights={isGeneratingHighlights}
                      isGeneratingReport={isGeneratingReport}
                      hasFlowchart={!!documentFlowchart}
                      hasFlashcards={!!flashcards}
                      hasQuiz={!!quiz}
                      hasMindmap={!!mindmap}
                      hasHighlights={!!highlights}
                      hasReport={!!report}
                      flowchart={documentFlowchart}
                      flashcards={flashcards}
                      quiz={quiz}
                      mindmap={mindmap}
                      highlights={highlights}
                      report={report}
                      onSendQuestionToChat={handleSendQuestionToChat}
                      onSetChatInput={handleSetChatInput}
                      onStopAllGenerations={handleStopAllGenerations}
                      isMaximized={maximizedPanel === 'right'}
                      onToggleMaximize={() => handleMaximize('right')}
                      eurekaId={currentDocument.eureka_id}
                    />
                  </Suspense>
                ) : (
                  // 空状态占位符
                  <div className="right-panel-placeholder app-right-panel-placeholder-empty">
                    <div className="app-right-panel-placeholder-icon">🛠️</div>
                    <div>工作室</div>
                  </div>
                )
              }

            />
          </div>
          </ErrorBoundary>
        </>
      )}



      {/* 认证模态框（本地版免登录，不渲染；模式确定前也不加载，避免本地版闪出登录框） */}
      {!isLocalMode && !isCheckingAuth && (
        <Suspense fallback={null}>
          <AuthModal
            onAuthSuccess={handleAuthSuccess}
          />
        </Suspense>
      )}

      {/* 非阻断提示：后台生成失败、本地服务连接异常等，不替换整个工作区 */}
      {toasts.length > 0 && (
        <div className="app-toast-stack">
          {toasts.map((toast) => (
            <div
              key={toast.id}
              className={`app-toast app-toast--${toast.type}`}
              role={toast.type === 'error' ? 'alert' : 'status'}
            >
              <span className="app-toast-message">{toast.message}</span>
              <button
                type="button"
                className="app-toast-close"
                onClick={() => dismissToast(toast.id)}
                aria-label="关闭提示"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {/* 提示词模板弹窗 */}
      <PromptTemplateModal
        isOpen={showPromptModal}
        onClose={() => {
          setShowPromptModal(false)
          setEditingTemplate(null)
        }}
        onSave={handleTemplateSave}
        onDelete={handleTemplateDelete}
        template={editingTemplate}
        existingCount={promptTemplates.length}
        maxCount={6}
      />

      {/* 删除确认对话框 */}
      <Suspense fallback={null}>
        <ConfirmDialog
          isOpen={deleteConfirmState.isOpen}
          title="确认删除"
          message="确定要删除当前对话吗？此操作不可恢复。"
          confirmLabel="删除"
          isDangerous={true}
          onConfirm={(dontRemind) => {
            if (dontRemind) {
              localStorage.setItem('dontShowDeleteConfirm', 'true')
            }
            if (deleteConfirmState.type === 'chat') {
              executeDeleteChat()
            }
            setDeleteConfirmState(prev => ({ ...prev, isOpen: false }))
          }}
          onCancel={() => setDeleteConfirmState(prev => ({ ...prev, isOpen: false }))}
        />
      </Suspense>





    </div >
  )
}

export default App
