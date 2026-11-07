import { create } from 'zustand'
import { apiGet, apiPut, getApiErrorMessage, getBuildTimeDeployMode, setRuntimeDeployMode } from '../utils/api'
import { dataCache } from '../utils/dataCache'
import { stopTokenRefresh } from '../utils/tokenRefresh'

/**
 * 运行时状态：部署模式 + 功能可用性清单
 *
 * 本地部署下用户可能什么 API Key 都没配、也没装可选依赖，因此界面必须知道
 * 「此刻哪些功能真的能用」，把用不了的入口置灰并说明原因，而不是让用户点下去撞 500。
 *
 * 数据源是后端的 /api/system/capabilities（免登录可访问——首次启动时
 * 用户还什么都没配，前端需要先拿到这份清单才知道该引导他做什么）。
 */

// 构建期默认值，用于首帧渲染；随后由接口返回值校正
const BUILD_TIME_MODE = getBuildTimeDeployMode()

const CAPABILITIES_ENDPOINT = '/api/system/capabilities'
const CREDENTIALS_ENDPOINT = '/api/system/credentials'

// 进行中的能力清单请求：启动流程与设置页并发调用时共享同一个请求
let capabilitiesRequest = null
// 请求序号：保存凭据后发起的新请求优先，丢弃更早发出、更晚返回的旧结果
let capabilitiesSeq = 0
let credentialsSeq = 0

const normalizeMode = (mode) => (mode === 'local' || mode === 'saas' ? mode : null)

/** 由单项能力的描述计算「为什么不可用」，供 tooltip / 提示条展示 */
const buildBlockReason = (entry) => {
  const label = entry?.label || ''
  if (entry?.missing_keys?.length) {
    const names = entry.missing_keys.map((k) => PROVIDER_LABELS[k] || k)
    // 多个候选表示「任一即可」
    const joined = names.length > 1 ? names.join(' 或 ') : names[0]
    return { type: 'key', label, text: `需要配置 ${joined} 的 API Key`, providers: entry.missing_keys }
  }
  if (entry?.missing_packages?.length) {
    const extra = entry.install_extra
    return {
      type: 'package',
      label,
      text: `需要安装依赖：${entry.missing_packages.join('、')}`,
      command: entry.install_command || (extra ? `pip install -e ".[${extra}]"` : null),
    }
  }
  if (entry?.missing_commands?.length) {
    return { type: 'package', label, text: `需要安装系统工具：${entry.missing_commands.join('、')}` }
  }
  return { type: 'unknown', label, text: '当前不可用' }
}

const useRuntimeStore = create((set, get) => ({
  deployMode: BUILD_TIME_MODE,
  // 部署模式是否已由服务端确认（或确认失败、回落到构建期默认值）
  modeResolved: false,
  capabilities: {},
  credentials: {},
  loaded: false,
  loading: false,

  isLocalMode: () => get().deployMode === 'local',

  /** 按服务端上报的模式校正（来源：能力清单或 /api/bootstrap 的 deploy_mode） */
  setDeployMode: (mode) => {
    const normalized = normalizeMode(mode)
    if (!normalized) return
    setRuntimeDeployMode(normalized)
    set({ deployMode: normalized, modeResolved: true })
    // 本地版没有 token 可刷新，若定时器已在模式确定前启动则立即停掉
    if (normalized === 'local') stopTokenRefresh()
  },

  /** 某项功能当前是否可用；清单尚未加载时一律放行，避免误置灰 */
  isAvailable: (key) => {
    const { loaded, capabilities } = get()
    if (!loaded) return true
    const entry = capabilities[key]
    return entry ? entry.available : true
  },

  /**
   * 取得某项功能不可用的原因，供 tooltip 展示；可用时返回 null。
   *
   * ⚠️ 本函数每次都返回**新对象**，因此禁止写成
   *      useRuntimeStore(s => s.getBlockReason(key))
   * 那会让 useSyncExternalStore 认为快照持续变化，触发无限重渲染并白屏。
   * 组件内请使用下方的 useCapability(key)：它订阅清单本身，清单加载后会自动重渲染。
   */
  getBlockReason: (key) => {
    const { loaded, capabilities } = get()
    if (!loaded) return null
    const entry = capabilities[key]
    if (!entry || entry.available) return null
    return buildBlockReason(entry)
  },

  /**
   * 拉取能力清单（启动时及配置变更后调用）。
   * 返回 Promise，可 await 以确保部署模式已确定后再决定首屏走向。
   */
  fetchCapabilities: ({ force = false } = {}) => {
    if (capabilitiesRequest && !force) return capabilitiesRequest

    const seq = ++capabilitiesSeq
    set({ loading: true })

    const request = (async () => {
      try {
        // 不走 apiGet 的 60 秒缓存：改完 Key 后必须立即看到新状态
        const res = await apiGet(CAPABILITIES_ENDPOINT, { cacheMode: 'no-cache' })
        if (!res?.success || !res.data) {
          throw new Error(getApiErrorMessage(res, '能力清单返回异常'))
        }
        if (seq !== capabilitiesSeq) return

        const data = res.data
        const mode = normalizeMode(data.deploy_mode) || get().deployMode
        setRuntimeDeployMode(mode)
        set({
          deployMode: mode,
          modeResolved: true,
          capabilities: data.capabilities || {},
          loaded: true,
          loading: false,
        })

        // 运行时兜底：本地版没有 token 可刷新，定时器只会每分钟打一次 401。
        if (mode === 'local') {
          stopTokenRefresh()
        }
      } catch (err) {
        if (seq !== capabilitiesSeq) return
        // 拿不到清单时保持「全部可用」的乐观态度：
        // 宁可让用户点下去看到真实报错，也不要把能用的功能误置灰。
        // 模式暂以构建期默认值为准，bootstrap 返回的 deploy_mode 还会再校正一次。
        console.warn('无法获取能力清单，按全部可用处理', err)
        set({ loading: false, modeResolved: true })
      } finally {
        if (capabilitiesRequest === request) capabilitiesRequest = null
      }
    })()

    capabilitiesRequest = request
    return request
  },

  /** 拉取凭据配置状态（仅本地版，返回值已打码） */
  fetchCredentials: async () => {
    if (!get().isLocalMode()) return
    const seq = ++credentialsSeq
    try {
      const res = await apiGet(CREDENTIALS_ENDPOINT, { cacheMode: 'no-cache' })
      if (seq !== credentialsSeq) return
      if (res?.success) {
        set({ credentials: res.data || {} })
      } else {
        console.warn('无法获取凭据配置', getApiErrorMessage(res))
      }
    } catch (err) {
      console.warn('无法获取凭据配置', err)
    }
  },

  /** 作废能力清单与凭据状态的接口缓存（配置变更后调用） */
  invalidateRuntimeCache: () => {
    dataCache.invalidate(`GET:${CAPABILITIES_ENDPOINT}`)
    dataCache.invalidate(`GET:${CREDENTIALS_ENDPOINT}`)
  },

  /** 配置变更后（保存 / 清除 / 测试连接）以服务端为准重新拉取两份状态 */
  refreshRuntimeState: () => {
    get().invalidateRuntimeCache()
    return Promise.all([
      get().fetchCapabilities({ force: true }),
      get().fetchCredentials(),
    ])
  },

  /**
   * 保存某个 provider 的凭据。
   * 后端在同一响应里连同新的能力清单一起返回，因此填完 Key 可立即解锁功能，
   * 无需刷新页面。失败时抛出 ApiError（message 为后端给出的原因），由设置页展示。
   */
  saveCredential: async (provider, { apiKey, baseUrl, model, dimensions, authRequired, useForChat } = {}) => {
    const res = await apiPut(CREDENTIALS_ENDPOINT, {
      provider,
      api_key: apiKey,
      base_url: baseUrl,
      model,
      dimensions,
      auth_required: authRequired,
      use_for_chat: useForChat,
    })
    const data = res?.data || {}
    // 让进行中的旧请求作废，避免它们晚到时把刚保存的状态覆盖回去
    capabilitiesSeq += 1
    credentialsSeq += 1
    set({
      credentials: data.credentials || get().credentials,
      capabilities: data.capabilities || get().capabilities,
      loaded: true,
      loading: false,
    })
    // 作废接口缓存并以服务端为准再拉一次：重新打开设置页、上传文档时都能读到新状态
    void get().refreshRuntimeState()
    return data
  },
}))

/**
 * 订阅单项能力的可用性：`const { available, reason } = useCapability('podcast')`
 *
 * 选择器只取 capabilities[key] 这一稳定引用（清单更新时才变化），原因对象在渲染时计算——
 * 因此清单加载 / 保存 Key 后组件会正确重渲染，也不会因选择器返回新对象而无限循环。
 * 清单尚未加载时视为可用。
 */
export function useCapability(key) {
  const entry = useRuntimeStore((s) => (s.loaded ? s.capabilities[key] : undefined))
  const available = !entry || Boolean(entry.available)
  return { available, reason: available ? null : buildBlockReason(entry) }
}

/** provider 键 → 展示名 */
export const PROVIDER_LABELS = {
  qwen: '通义千问 Qwen',
  moonshot: '月之暗面 Kimi',
  deepseek: 'DeepSeek',
  openai: 'OpenAI 兼容端点',
  dashscope: '向量模型（OpenAI 兼容）',
  image_gen: '图像生成',
  dashscope_native: '百炼语音与联网研究',
}

/** provider 的申请地址与说明，供设置页展示 */
export const PROVIDER_META = {
  qwen: {
    label: '通义千问 Qwen',
    url: 'https://bailian.console.aliyun.com/',
    hint: '阿里云百炼的通义千问；也可改 Base URL 连接其他提供 Qwen 模型的兼容网关。',
    defaultBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  },
  moonshot: {
    label: '月之暗面 Kimi',
    url: 'https://platform.moonshot.cn/',
    hint: '对话模型。默认用于问答与文档解析。',
    defaultBaseUrl: 'https://api.moonshot.cn/v1',
  },
  deepseek: {
    label: 'DeepSeek',
    url: 'https://platform.deepseek.com/',
    hint: '对话模型。性价比高，适合长文本生成。',
    defaultBaseUrl: 'https://api.deepseek.com/v1',
  },
  openai: {
    label: 'OpenAI 兼容端点',
    url: 'https://platform.openai.com/api-keys',
    hint: '任意支持工具调用的 OpenAI 兼容服务：OpenAI，或本地的 Ollama、LM Studio 等。',
    defaultBaseUrl: 'https://api.openai.com/v1',
  },
  dashscope: {
    label: '向量模型（OpenAI 兼容）',
    url: 'https://bailian.console.aliyun.com/',
    hint: '支持百炼或本地兼容端点。修改模型或端点后，请在下方重建文档索引。',
    defaultBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  },
  image_gen: {
    label: '图像生成',
    url: 'https://bailian.console.aliyun.com/',
    hint: '论文转 PPT 与学术海报功能需要。',
    defaultBaseUrl: 'https://dashscope.aliyuncs.com/api/v1',
  },
  dashscope_native: {
    label: '百炼语音与联网研究',
    url: 'https://bailian.console.aliyun.com/',
    hint: '可选云端服务，使用百炼 API Key；与本地或其他厂商的向量模型分别配置。',
  },
}

/**
 * 是否运行在本地版（可在 React 组件之外调用）
 *
 * 初值来自构建期的 VITE_DEPLOY_MODE，拉到能力清单后会被服务端上报的真实模式校正。
 * 供 store 外部的模块（如 authStore 的会话过期回调）判断模式使用。
 */
export function isLocalDeployMode() {
  return useRuntimeStore.getState().deployMode === 'local'
}

export default useRuntimeStore
