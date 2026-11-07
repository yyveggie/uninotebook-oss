import { apiGet } from '../../utils/api'

// 默认最多等 5 分钟：AI 整理在概念很多时要分几批调用模型，2 分钟常常不够
export const pollCeleryTask = (taskId, { onSuccess, onError, maxAttempts = 150, interval = 2000 }) => {
  let attempts = 0
  let cancelled = false
  let timer = null
  const poll = async () => {
    if (cancelled) return
    attempts++
    try {
      const statusRes = await apiGet(`/api/generation-task/${taskId}/status`, { cacheMode: 'no-cache' })
      if (cancelled) return
      if (statusRes.status === 'completed' && statusRes.success) {
        const payload = statusRes.data
        // 任务本身跑完了，但业务失败（模型报错、节点不足等）：按失败处理，而不是当成结果展示
        if (payload && payload.success === false) {
          onError(payload.message || '任务失败')
        } else {
          onSuccess(payload?.data || payload)
        }
      } else if (statusRes.status === 'failed') {
        onError(statusRes.error || '任务失败')
      } else if (attempts < maxAttempts) {
        timer = setTimeout(poll, interval)
      } else {
        onError('任务超时，请稍后重试')
      }
    } catch (err) {
      if (cancelled) return
      if (attempts < maxAttempts) {
        timer = setTimeout(poll, interval)
      } else {
        onError(err.message || '请求失败')
      }
    }
  }
  poll()
  return () => {
    cancelled = true
    clearTimeout(timer)
  }
}

export const SKELETON_WIDTHS = [
  98, 142, 76, 121, 155, 88, 130, 67, 110, 148,
  84, 136, 72, 115, 160, 93, 125, 80, 105, 144,
  90, 138, 70, 118,
]

// 中文引号用 \u 转义书写：直接写成弯引号容易被编辑器换成直引号，导致中文引号不再被去掉
const OUTER_QUOTES_START = /^[\s`'"\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f\uff02\uff07]+/
const OUTER_QUOTES_END = /[\s`'"\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f\uff02\uff07]+$/

export const stripOuterQuotes = (text) => {
  if (!text) return ''
  return String(text)
    .replace(OUTER_QUOTES_START, '')
    .replace(OUTER_QUOTES_END, '')
}

export const formatDisplayLabel = (raw) => {
  const { en, zh } = splitTitleLabel(raw)
  if (en && zh) return `${en} / ${zh}`
  return en || zh || ''
}

export const splitTitleLabel = (raw) => {
  if (!raw) return { en: '', zh: '' }
  const str = String(raw)
  const parts = str.split(/\s*\/\s*/)
  if (parts.length >= 2) {
    const en = stripOuterQuotes((parts[0] || '').trim())
    const zh = stripOuterQuotes(parts.slice(1).join(' / ').trim())
    return { en, zh }
  }
  const match = str.match(/^([^\u4e00-\u9fff]*)([\s\S]*)$/)
  if (!match) {
    const single = stripOuterQuotes(str.trim())
    return { en: single, zh: '' }
  }
  const en = stripOuterQuotes(match[1].trim())
  const zh = stripOuterQuotes(match[2].trim())
  if (en && zh) {
    return { en, zh }
  }
  return { en: en || zh, zh: '' }
}

export const stripSummaryPrefix = (raw) => {
  if (!raw) return ''
  let s = String(raw).trimStart()
  s = s.replace(/^##\s*.*?核心内容\s*/u, '')
  return s.trimStart()
}

export const filterNodesByQuery = (nodes, query) => {
  if (!query) return []
  const lowerQuery = query.toLowerCase().trim()
  if (!lowerQuery) return []

  return nodes.filter((n) => {
    const label = (n.label || '').toLowerCase()
    return label.includes(lowerQuery)
  })
}
