import { create } from 'zustand'
import { apiGet, apiRequest, createApiError, setAccessToken, onSessionExpired, resetSessionExpired } from '../utils/api'
import { startTokenRefresh, stopTokenRefresh } from '../utils/tokenRefresh'
import { dataCache } from '../utils/dataCache'
import useDocumentStore from './documentStore'
import useRuntimeStore from './runtimeStore'
import useUIStore from './uiStore'

/** 当前是否为开源本地版（runtimeStore 不反向依赖本模块，无循环引用风险） */
const isLocalMode = () => useRuntimeStore.getState().deployMode === 'local'

/**
 * 本地版的兜底身份，与后端 runtime/identity.py 的固定单用户一致。
 * 本地版没有登录界面可退：bootstrap 失败时仍以它进入工作区，只提示连接问题。
 */
const LOCAL_USER_FALLBACK = {
  id: 1,
  email: 'local@uninotebook.local',
  username: '本地用户',
  full_name: '本地用户',
  is_active: true,
}

/** 拉取首屏聚合数据；非 2xx 抛出 ApiError（含后端给出的原因） */
const fetchBootstrap = async () => {
  const response = await apiRequest('/api/bootstrap', {
    method: 'GET',
    cache: 'no-store',
    headers: {
      'Cache-Control': 'no-cache',
      Pragma: 'no-cache',
    },
  })
  if (!response.ok) {
    throw await createApiError(response, `首屏数据加载失败（HTTP ${response.status}）`)
  }
  const payload = await response.json()
  // bootstrap 同样上报部署模式：能力清单拿不到时，靠它校正模式
  useRuntimeStore.getState().setDeployMode(payload?.deploy_mode)
  return payload
}

const describeConnectError = (error) => {
  if (!error) return '首屏数据返回异常'
  if (error.name === 'TypeError') return '网络连接失败'
  return error.message || String(error)
}

/**
 * 清理与账号绑定的本地缓存（SaaS 登出时调用）。
 * 文档全文、笔记、会话 ID 都按文档存放在 localStorage 中，
 * 共用电脑时不应留给下一个登录的人；界面偏好（布局宽度、删除免确认）予以保留。
 */
const USER_SCOPED_STORAGE_KEYS = ['currentDocument', 'selectedNoteId', 'selectedNoteDocId', 'pending_invite_code']
const USER_SCOPED_STORAGE_PATTERN = /^(notes:|notes_eureka_|document_\d+_|eureka_\d+_|annotation_closed_)/

const clearUserScopedStorage = () => {
  try {
    USER_SCOPED_STORAGE_KEYS.forEach((key) => localStorage.removeItem(key))
    const keys = []
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i)
      if (key && USER_SCOPED_STORAGE_PATTERN.test(key)) keys.push(key)
    }
    keys.forEach((key) => localStorage.removeItem(key))
  } catch {
    // 忽略存储不可用（隐私模式等）
  }
}

const useAuthStore = create((set) => ({
  // === State ===
  user: null,
  bootstrapPayload: null,
  showAuthModal: false,
  authInitialMode: 'login', // 'login' | 'register' | 'forgotPassword'
  showUserMenu: false,
  isCheckingAuth: true, // 初始为 true，避免闪烁

  // === Actions ===
  setUser: (user) => set({ user }),
  setBootstrapPayload: (payload) => set({ bootstrapPayload: payload }),
  // 本地版没有登录/注册：任何路径（未登录兜底、401 等）都不应弹出登录框
  setShowAuthModal: (show) => set({ showAuthModal: Boolean(show) && !isLocalMode() }),
  setAuthInitialMode: (mode) => set({ authInitialMode: mode }),
  setShowUserMenu: (show) => set({ showUserMenu: show }),

  // 检查登录状态
  checkAuth: async () => {
    set({ isCheckingAuth: true })

    // 先确定部署模式，再决定进落地页、弹登录还是直接进工作区——
    // 不能只依赖构建期的 VITE_DEPLOY_MODE（开源版用户自行构建时未必设置）。
    // 两个请求并行发出：能力清单失败时，bootstrap 的 deploy_mode 仍能校正模式。
    // 走聚合接口：bootstrap 会自动通过 Cookie 中的 refresh_token 恢复登录态
    // 这是跨子域跳转（www → space）后恢复认证的核心路径
    const runtime = useRuntimeStore.getState()
    const [, bootstrapResult] = await Promise.all([
      runtime.modeResolved ? null : runtime.fetchCapabilities(),
      fetchBootstrap().then(
        (payload) => ({ payload }),
        (error) => ({ error }),
      ),
    ])
    const bootstrap = bootstrapResult.payload

    if (isLocalMode()) {
      // 本地版免登录：没有 token、不启动刷新定时器，也绝不回落到 /api/auth/*
      stopTokenRefresh()
      const localUser = bootstrap?.success ? bootstrap?.data?.user : null
      if (localUser) {
        set({ user: localUser, bootstrapPayload: bootstrap.data, isCheckingAuth: false })
        return
      }
      // bootstrap 失败也照常进入工作区（本地版不存在登录页可退），只给出不阻断的连接提示
      set({ user: LOCAL_USER_FALLBACK, bootstrapPayload: null, isCheckingAuth: false })
      useUIStore.getState().showToast(
        `无法连接本地服务（${describeConnectError(bootstrapResult.error)}），部分数据可能无法加载。请确认后端仍在运行后刷新页面。`,
        { type: 'error', duration: 0 },
      )
      return
    }

    if (bootstrap?.success && bootstrap?.authenticated && bootstrap?.data?.user) {
      // 如果 bootstrap 通过 Cookie 恢复了登录态，会返回新的 access_token
      if (bootstrap.access_token) {
        setAccessToken(bootstrap.access_token)
      }
      resetSessionExpired() // 重置熔断器
      set({
        user: bootstrap.data.user,
        bootstrapPayload: bootstrap.data,
        isCheckingAuth: false,
      })
      startTokenRefresh()
      return
    }

    // bootstrap 明确返回 authenticated: false → 用户确实未登录
    if (bootstrap?.success && bootstrap?.authenticated === false) {
      set({ user: null, bootstrapPayload: null, isCheckingAuth: false })
      return
    }

    // bootstrap 接口本身异常（网络错误/500等），降级尝试 /auth/me
    try {
      const userData = await apiGet('/api/auth/me')
      set({ user: userData, isCheckingAuth: false })
      startTokenRefresh()
    } catch (err) {
      if (err.message.includes('登录') || err.message.includes('未授权')) {
        set({ user: null, bootstrapPayload: null, isCheckingAuth: false })
        stopTokenRefresh()
      } else {
        set({ isCheckingAuth: false })
      }
    }
  },

  // 登录成功后的处理
  handleAuthSuccess: async () => {
    // 本地版不存在登录流程
    if (isLocalMode()) return

    resetSessionExpired() // 登录成功，重置熔断器
    try {
      // 登录后优先走轻量用户接口，尽快完成 UI 登录态切换
      const userData = await apiGet('/api/auth/me')
      set({ user: userData, bootstrapPayload: null })

      startTokenRefresh()

      // 首页聚合数据改为后台刷新，避免阻塞登录首屏
      ;(async () => {
        try {
          const bootstrap = await apiGet('/api/bootstrap', {
            cacheMode: 'no-cache',
          })
          if (bootstrap?.success && bootstrap?.authenticated && bootstrap?.data) {
            if (bootstrap.access_token) {
              setAccessToken(bootstrap.access_token)
            }
            set({ bootstrapPayload: bootstrap.data })
          }
        } catch (error) {
          void error
        }
      })()
    } catch (err) {
      // 获取用户信息失败时清理 token，避免"有 token 但无 user"的不一致状态
      console.error('Failed to fetch user after login:', err)
      setAccessToken(null)
      set({ user: null, bootstrapPayload: null })
      stopTokenRefresh()
    }
  },

  // 登出
  logout: async () => {
    // 本地版是固定单用户，不存在可退出的账号。
    // 若照常清空 user，界面会退到落地页，而那里没有任何登录入口——
    // 用户就此卡死，只能刷新页面自愈。除了菜单入口（已隐藏），
    // 401 熔断等路径也可能调到这里，因此在此处兜底为空操作。
    if (isLocalMode()) {
      set({ showUserMenu: false })
      return
    }

    try {
      // 调用后端登出接口清除 Cookie
      await apiRequest('/api/auth/logout', { method: 'POST', credentials: 'include', skipAutoRefresh: true })
    } catch (e) {
      console.error('Logout API failed', e)
    }

    setAccessToken(null)
    dataCache.clear()
    clearUserScopedStorage()
    set({
      user: null,
      bootstrapPayload: null,
      showUserMenu: false,
    })
    stopTokenRefresh()
  },
}))

/**
 * 「能否调用需鉴权接口」的响应式版本，供组件放进 effect 依赖：
 * 首屏检查完成后，本地版恒为 true（免登录），SaaS 在会话建立（user 就绪）后为 true。
 * 刷新页面时各加载器可能先于 bootstrap 执行，依赖它即可在会话就绪后自动重新加载。
 */
export function useCanCallApi() {
  const isLocal = useRuntimeStore((s) => s.deployMode === 'local')
  const hasUser = useAuthStore((s) => Boolean(s.user))
  const isCheckingAuth = useAuthStore((s) => s.isCheckingAuth)
  return !isCheckingAuth && (isLocal || hasUser)
}

// 全局监听会话过期事件
// 当 refresh_token 失效时，api.js 熔断器触发此回调
// 集中处理：清理状态 + 弹出登录框，避免 10 个组件各自报错
onSessionExpired(() => {
  // 本地版没有会话可过期：鉴权在后端被短路为固定单用户，
  // 也不存在登录界面。若在这里执行登出，用户会被踢回落地页却无处登录——
  // 形成无法恢复的死路（只能清 localStorage 或重启）。
  // 因此本地模式下整套会话过期机制一律失效。
  if (isLocalMode()) {
    console.warn('[Auth] 本地模式：忽略会话过期事件（无登录体系）')
    return
  }

  const store = useAuthStore.getState()
  // 已经是未登录状态则不重复处理
  if (!store.user) return

  stopTokenRefresh()
  dataCache.clear()

  // 清除文档状态，避免 currentDocument useEffect 和浏览器后退按钮"打架"
  // （useEffect 会在 URL 变化时把用户推回文档页，导致后退无效）
  useDocumentStore.getState().clearDocumentStates?.()
  useDocumentStore.setState({
    currentDocument: null,
    showHome: true,
  })
  localStorage.removeItem('currentDocument')

  useAuthStore.setState({
    user: null,
    bootstrapPayload: null,
    showUserMenu: false,
    showAuthModal: true,
    authInitialMode: 'login',
  })
  console.warn('[Auth] 会话已过期，请重新登录')
})

export default useAuthStore
