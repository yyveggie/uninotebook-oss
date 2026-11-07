import { requestScheduler } from './requestScheduler'
import { dataCache } from './dataCache'

/**
 * API请求工具
 * 自动携带Token，处理401错误，并通过调度器串行化同一路径请求
 */

export const API_BASE_URL = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/$/, '') || ''

// ---- 部署模式（运行时单一来源） ----
// 构建期的 VITE_DEPLOY_MODE 只作首帧默认值：开源版用户自行构建时未必会设置它。
// 真实模式由 runtimeStore 从 /api/system/capabilities（及 /api/bootstrap 的 deploy_mode）
// 拿到后写回这里。放在本模块是为了让 tokenRefresh、taskError 等底层模块也能读取，
// 且不与 store 形成循环依赖。
const BUILD_TIME_DEPLOY_MODE = import.meta.env?.VITE_DEPLOY_MODE === 'local' ? 'local' : 'saas'
let runtimeDeployMode = BUILD_TIME_DEPLOY_MODE

export const getBuildTimeDeployMode = () => BUILD_TIME_DEPLOY_MODE

export const setRuntimeDeployMode = (mode) => {
  runtimeDeployMode = mode === 'local' ? 'local' : 'saas'
}

/** 当前是否运行在开源本地版（免登录、单用户、不计费） */
export const isLocalRuntime = () => runtimeDeployMode === 'local'

const CACHE_POLICY = {
  REALTIME: 'realtime',
  SHORT: 'short',
  SWR: 'swr',
  DEFAULT: 'default',
}

const SHORT_TTL_MS = 15 * 1000
const FIRST_SCREEN_DEDUPE_WINDOW_MS = 500

const normalizeEndpointPath = (endpoint = '') => {
  const [path] = String(endpoint).split('?')
  return path.startsWith('/') ? path : `/${path}`
}

const resolveAutoCachePolicy = (endpoint = '') => {
  const path = normalizeEndpointPath(endpoint)

  const firstScreenDedupe = {
    dedupeWindowMs: FIRST_SCREEN_DEDUPE_WINDOW_MS,
    dedupeKey: `FIRST_SCREEN:${path}`,
  }

  // 账号态必须实时，避免显示过期用户信息
  if (/^\/api\/auth\/me$/.test(path) || /^\/api\/bootstrap$/.test(path)) {
    return {
      policy: CACHE_POLICY.REALTIME,
      requestConfig: firstScreenDedupe,
    }
  }

  // 积分等轻量状态可短缓存，降低频繁请求抖动
  if (/^\/api\/user\/credits$/.test(path)) {
    return {
      policy: CACHE_POLICY.SHORT,
      ttl: SHORT_TTL_MS,
      requestConfig: firstScreenDedupe,
    }
  }

  // 文档列表、历史列表使用 SWR：秒开 + 后台刷新
  if (/^\/api\/documents$/.test(path) || /^\/api\/history\/documents$/.test(path)) {
    return {
      policy: CACHE_POLICY.SWR,
      maxAge: 30 * 1000,
      staleWhileRevalidate: 5 * 60 * 1000,
      requestConfig: firstScreenDedupe,
    }
  }

  // 知识网络主图谱与会话列表：SWR 提升二次访问速度
  if (
    /^\/api\/knowledge-graph\/nodes-edges$/.test(path) ||
    /^\/api\/knowledge-graph\/eureka\/\d+$/.test(path) ||
    /^\/api\/knowledge-graph\/eureka\/\d+\/sessions$/.test(path) ||
    /^\/api\/knowledge-graph\/eureka\/list$/.test(path)
  ) {
    return {
      policy: CACHE_POLICY.SWR,
      maxAge: 45 * 1000,
      staleWhileRevalidate: 5 * 60 * 1000,
      requestConfig: firstScreenDedupe,
    }
  }

  return { policy: CACHE_POLICY.DEFAULT }
}

export const buildApiUrl = (path = '') => {
  if (!path) return API_BASE_URL
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`
}

// ---- 统一错误 ----

/**
 * 接口错误。status 为 HTTP 状态码；code / message / details 取自后端统一错误体
 * `{code, message, details}`，FastAPI 原生的 `{detail: ...}`（含 422 数组）也会被展开成可读文案。
 */
export class ApiError extends Error {
  constructor({ status = 0, code = '', message = '请求失败，请稍后重试', details = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

const flattenErrorItems = (items) => items
  .map((item) => {
    if (typeof item === 'string') return item
    if (item && typeof item === 'object') return item.msg || item.message || ''
    return ''
  })
  .filter(Boolean)
  .join('；')

/**
 * 从错误响应体中取出可直接展示给用户的文案
 * @param {any} payload 已解析的响应体
 * @param {string} fallback 无法提取时的兜底文案
 */
export const getApiErrorMessage = (payload, fallback = '请求失败，请稍后重试') => {
  if (typeof payload === 'string') return payload.trim() || fallback
  if (!payload || typeof payload !== 'object') return fallback

  const { message, detail, details, error } = payload
  // 统一错误体的 422 只有一句「请求参数校验失败」，具体原因在 details 数组里
  const itemsText = Array.isArray(details) ? flattenErrorItems(details) : ''

  if (typeof message === 'string' && message.trim()) {
    return itemsText ? `${message.trim()}：${itemsText}` : message.trim()
  }
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail)) {
    const detailText = flattenErrorItems(detail)
    if (detailText) return detailText
  }
  if (detail && typeof detail === 'object' && typeof detail.message === 'string' && detail.message.trim()) {
    return detail.message.trim()
  }
  if (typeof error === 'string' && error.trim()) return error.trim()
  return itemsText || fallback
}

/** 由失败的 Response 构造 ApiError（不消费原始 body，调用方仍可自行读取） */
export const createApiError = async (response, fallback) => {
  let payload = null
  try {
    payload = await response.clone().json()
  } catch {
    payload = null
  }
  return new ApiError({
    status: response.status,
    code: payload?.code || payload?.detail?.code || `HTTP_${response.status}`,
    message: getApiErrorMessage(payload, fallback || `请求失败（HTTP ${response.status}）`),
    details: payload?.details ?? payload?.detail ?? null,
  })
}

// 无需登录即可调用的接口：会话过期熔断后仍须放行，401 也不应触发 token 刷新——
// 否则会话一旦过期，连注册、发送验证码、找回密码都会被前端直接拦下，只能刷新页面自救。
const PUBLIC_ENDPOINT_PATTERNS = [
  /^\/api\/auth\/(login|register|logout|refresh|reset-password|send-verification-code|send-password-reset-code|send-sms-code)$/,
  /^\/api\/bootstrap$/,
  /^\/api\/system\/capabilities$/,
]

const isPublicEndpoint = (endpoint) => {
  const path = normalizeEndpointPath(endpoint)
  return PUBLIC_ENDPOINT_PATTERNS.some((pattern) => pattern.test(path))
}

// 内存中存储 Access Token
let memoryAccessToken = null

// ---- 会话过期熔断器 ----
// 当 refresh 失败时进入"已过期"状态，阻止后续请求雪崩式报错
let _sessionExpired = false
let _sessionExpiredCallbacks = []

export const onSessionExpired = (callback) => {
  _sessionExpiredCallbacks.push(callback)
  return () => {
    _sessionExpiredCallbacks = _sessionExpiredCallbacks.filter(cb => cb !== callback)
  }
}

const _triggerSessionExpired = () => {
  if (_sessionExpired) return  // 只触发一次
  _sessionExpired = true
  setAccessToken(null)
  // 异步触发回调，避免在请求链中同步执行 UI 操作
  Promise.resolve().then(() => {
    _sessionExpiredCallbacks.forEach(cb => {
      try { cb() } catch (e) { void e }
    })
  })
}

// 会话恢复时（登录成功后）重置熔断器
export const resetSessionExpired = () => {
  _sessionExpired = false
}

export const setAccessToken = (token) => {
  memoryAccessToken = token
  // 拿到新 token（登录 / 刷新 / bootstrap 恢复成功）即视为会话已恢复，解除熔断
  if (token) {
    _sessionExpired = false
  }
}

export const getAccessToken = () => {
  return memoryAccessToken
}

/**
 * 能否调用需要鉴权的接口——各类数据加载器应以此代替「有没有 token」判断：
 * - 本地版：后端鉴权被短路为固定单用户，永远没有 token，但始终可以调用；
 * - SaaS：会话建立后（bootstrap 恢复或登录成功）内存中才会有 access token。
 * 组件内请改用 authStore 的 useCanCallApi()，以便会话就绪后自动重新加载。
 */
export const canCallAuthenticatedApi = () => isLocalRuntime() || Boolean(memoryAccessToken)

let refreshPromise = null

const buildHeaders = (options = {}) => {
  const headers = { ...(options.headers || {}) }
  const isFormData = options.body instanceof FormData

  if (!isFormData && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  if (memoryAccessToken) {
    headers['Authorization'] = `Bearer ${memoryAccessToken}`
  }

  return headers
}

const scheduleFetch = (url, options = {}, config = {}) => {
  return requestScheduler.schedule(url, options, config)
}

const refreshAccessToken = async () => {
  // 本地版免登录，没有可刷新的会话，也不应请求 /api/auth/*
  if (isLocalRuntime()) {
    throw new Error('本地模式无需刷新登录状态')
  }

  // 熔断器：会话已过期，不再尝试刷新
  if (_sessionExpired) {
    throw new Error('会话已过期')
  }

  if (refreshPromise) {
    return refreshPromise
  }

  refreshPromise = (async () => {
    const response = await scheduleFetch(buildApiUrl('/api/auth/refresh'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include'
    })

    if (!response.ok) {
      // 刷新失败时，尝试通过 bootstrap 自愈（处理并发 refresh 竞态）
      const bootstrapRes = await scheduleFetch(buildApiUrl('/api/bootstrap'), {
        method: 'GET',
        credentials: 'include',
        headers: {
          'Cache-Control': 'no-cache',
          Pragma: 'no-cache',
        },
      })

      if (bootstrapRes.ok) {
        const bootstrap = await bootstrapRes.json()
        if (bootstrap?.success && bootstrap?.authenticated && bootstrap?.access_token) {
          setAccessToken(bootstrap.access_token)
          resetSessionExpired()
          return { access_token: bootstrap.access_token, token_type: 'bearer' }
        }
      }

      _triggerSessionExpired()
      throw new Error('登录已过期，请重新登录')
    }

    const data = await response.json()
    if (data.access_token) {
      setAccessToken(data.access_token)
    }
    return data
  })()

  try {
    return await refreshPromise
  } finally {
    refreshPromise = null
  }
}

/**
 * 发送API请求
 * @param {string} endpoint - API端点（不带基础URL）
 * @param {object} options - fetch选项
 * @param {object} config  - 调度器配置
 * @returns {Promise<Response>}
 */
export async function apiRequest(endpoint, options = {}, config = {}) {
  const publicEndpoint = isPublicEndpoint(endpoint)

  // 熔断器：会话已过期，直接拒绝（不发网络请求，不打印错误）；免登录接口不受影响
  if (_sessionExpired && !options.skipAutoRefresh && !publicEndpoint) {
    throw new Error('会话已过期')
  }

  const requestOptions = {
    ...options,
    headers: buildHeaders(options),
    credentials: options.credentials || 'include',
  }

  const url = buildApiUrl(endpoint)

  let response = await scheduleFetch(url, requestOptions, config)

  // 本地版没有登录体系，401 无法靠刷新 token 解决；免登录接口的 401（如密码错误）同理
  if (response.status === 401 && !options.skipAutoRefresh && !publicEndpoint && !isLocalRuntime()) {
    await refreshAccessToken()

    const retryOptions = {
      ...options,
      headers: buildHeaders(options),
      credentials: options.credentials || 'include',
    }

    const retryScheduleConfig = config
      ? { ...config, dedupeWindowMs: 0, dedupeKey: undefined }
      : {}
    response = await scheduleFetch(url, retryOptions, retryScheduleConfig)

    if (response.status === 401) {
      _triggerSessionExpired()
      throw new Error('会话已过期')
    }
  }

  return response
}

/**
 * GET请求
 */
export async function apiGet(endpoint, config = {}) {
  const {
    cacheKey,
    cacheTTL,
    cacheMode = 'default',
    requestConfig = {},
    useAutoCachePolicy = true,
  } = config

  const canApplyAutoPolicy =
    useAutoCachePolicy &&
    cacheMode === 'default' &&
    cacheKey === undefined &&
    cacheTTL === undefined

  if (canApplyAutoPolicy) {
    const autoPolicy = resolveAutoCachePolicy(endpoint)
    const mergedRequestConfig = {
      ...requestConfig,
      ...(autoPolicy.requestConfig || {}),
    }

    if (autoPolicy.policy === CACHE_POLICY.REALTIME) {
      return apiGet(endpoint, {
        ...config,
        cacheMode: 'no-cache',
        requestConfig: mergedRequestConfig,
        useAutoCachePolicy: false,
      })
    }

    if (autoPolicy.policy === CACHE_POLICY.SHORT) {
      return apiGet(endpoint, {
        ...config,
        cacheTTL: autoPolicy.ttl,
        requestConfig: mergedRequestConfig,
        useAutoCachePolicy: false,
      })
    }

    if (autoPolicy.policy === CACHE_POLICY.SWR) {
      return apiGetSWR(endpoint, {
        cacheKey: `SWR:${endpoint}`,
        maxAge: autoPolicy.maxAge,
        staleWhileRevalidate: autoPolicy.staleWhileRevalidate,
        requestConfig: mergedRequestConfig,
      })
    }
  }

  const resolvedKey = cacheKey ?? `GET:${endpoint}`
  const skipRead = cacheMode === 'no-cache'
  const skipWrite = cacheMode === 'no-store'
  const cachedBeforeRequest =
    resolvedKey && !skipRead ? dataCache.get(resolvedKey) : null

  if (resolvedKey && !skipRead) {
    if (cachedBeforeRequest !== null) {
      return cachedBeforeRequest
    }
  }

  const response = await apiRequest(
    endpoint,
    {
      method: 'GET',
      credentials: 'include', // 确保 GET 请求携带 Cookie
      cache: skipRead ? 'no-store' : 'default',
      headers: skipRead
        ? {
          'Cache-Control': 'no-cache',
          Pragma: 'no-cache',
        }
        : undefined,
    },
    requestConfig
  )

  if (response.status === 304) {
    if (cachedBeforeRequest !== null) {
      return cachedBeforeRequest
    }
    throw new Error(`HTTP 304: endpoint=${endpoint} lacks local cached payload`)
  }

  const data = await response.json()

  if (response.ok && resolvedKey && !skipWrite) {
    dataCache.set(resolvedKey, data, cacheTTL)
  }

  return data
}

/**
 * 🆕 SWR 模式 GET 请求
 * 
 * 使用 Stale-While-Revalidate 策略：
 * - 有缓存时立即返回（即使过期），后台刷新
 * - 无缓存时同步请求
 * - 请求失败时返回旧缓存（如果有）
 * 
 * 适用场景：
 * - 文档列表、聊天历史等需要快速响应的数据
 * - 数据不需要实时精确，但用户体验优先
 * 
 * @param {string} endpoint - API 端点
 * @param {Object} config - 配置项
 * @param {number} config.maxAge - 数据新鲜期 (毫秒)，默认 60 秒
 * @param {number} config.staleWhileRevalidate - 过期后仍可返回的时间窗口 (毫秒)，默认 5 分钟
 * @param {string} config.cacheKey - 自定义缓存键
 */
export async function apiGetSWR(endpoint, config = {}) {
  const {
    maxAge = 60 * 1000,  // 1 分钟新鲜期
    staleWhileRevalidate = 5 * 60 * 1000,  // 5 分钟 stale 窗口
    cacheKey,
    requestConfig = {},
  } = config

  const resolvedKey = cacheKey ?? `SWR:${endpoint}`

  const fetchFn = async () => {
    const response = await apiRequest(
      endpoint,
      { method: 'GET', credentials: 'include' },
      requestConfig
    )
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    return response.json()
  }

  return dataCache.getSWR(resolvedKey, fetchFn, { maxAge, staleWhileRevalidate })
}

/**
 * 🆕 检查某个 SWR 缓存是否正在后台刷新
 */
export function isRevalidating(endpoint, cacheKey = null) {
  const resolvedKey = cacheKey ?? `SWR:${endpoint}`
  return dataCache.isRevalidating(resolvedKey)
}


/**
 * POST请求
 * 非 2xx 响应抛出 ApiError（message 为后端返回的原因），调用方无需再检查错误体
 */
export async function apiPost(endpoint, data) {
  const response = await apiRequest(endpoint, {
    method: 'POST',
    credentials: 'include',
    body: JSON.stringify(data),
  })
  if (!response.ok) {
    throw await createApiError(response)
  }
  return response.json()
}

/**
 * PUT请求（非 2xx 抛出 ApiError）
 */
export async function apiPut(endpoint, data) {
  const response = await apiRequest(endpoint, {
    method: 'PUT',
    credentials: 'include',
    body: JSON.stringify(data),
  })
  if (!response.ok) {
    throw await createApiError(response)
  }
  return response.json()
}

/**
 * DELETE请求（非 2xx 抛出 ApiError）
 */
export async function apiDelete(endpoint) {
  const response = await apiRequest(endpoint, { method: 'DELETE', credentials: 'include' })
  if (!response.ok) {
    throw await createApiError(response)
  }
  return response.json()
}
/**
 * 每日签到
 */
export async function checkin() {
  return await apiPost('api/user/daily-checkin')
}

/**
 * 获取签到状态
 */
export async function getCheckinStatus() {
  return await apiGet('api/user/checkin-status', {
    cacheMode: 'no-cache'
  })
}
