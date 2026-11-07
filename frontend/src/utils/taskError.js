import { isLocalRuntime } from './api'

export const INSUFFICIENT_CREDITS_MESSAGE = '积分不足，当前任务无法执行。请前往充值后重试。'

// 本地版不计费：「余额不足」只可能来自用户自己配置的模型服务商
const PROVIDER_BALANCE_HINT = '模型服务商返回余额或额度不足，请检查对应服务商账户余额，或在设置中更换 API Key 后重试。'

const isObject = (value) => value && typeof value === 'object' && !Array.isArray(value)

const flattenDetail = (detail) => {
  if (!detail) return ''
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map(item => {
        if (typeof item === 'string') return item
        if (isObject(item)) return item.msg || item.message || item.detail || ''
        return ''
      })
      .filter(Boolean)
      .join('；')
  }
  if (isObject(detail)) {
    return detail.details || detail.message || detail.detail || ''
  }
  return String(detail)
}

/**
 * 是否为本产品（SaaS）的积分不足错误。
 * 只认明确的积分文案——「余额」「402」这类字样也会出现在模型服务商的报错里
 * （如 DeepSeek 的 "Insufficient Balance"），不能一概当成积分不足。
 */
export const isInsufficientCreditsText = (text = '') => {
  if (!text || isLocalRuntime()) return false
  const normalized = String(text).toLowerCase()
  return normalized.includes('积分不足') || normalized.includes('insufficient credit')
}

/** 是否像模型服务商的余额 / 额度不足报错（仅用于本地版给出可操作的提示） */
const isProviderBalanceText = (text = '') => {
  const normalized = String(text).toLowerCase()
  return (
    normalized.includes('余额') ||
    normalized.includes('insufficient balance') ||
    normalized.includes('insufficient_quota') ||
    normalized.includes('insufficient quota') ||
    normalized.includes('exceeded your current quota') ||
    normalized.includes('payment required')
  )
}

export const normalizeTaskErrorMessage = (message, fallback = '请求失败，请稍后重试') => {
  const msg = (message || '').toString().trim()
  if (!msg) return fallback
  if (isLocalRuntime()) {
    return isProviderBalanceText(msg) ? `${PROVIDER_BALANCE_HINT}（${msg}）` : msg
  }
  if (isInsufficientCreditsText(msg)) {
    return INSUFFICIENT_CREDITS_MESSAGE
  }
  return msg
}

export async function parseTaskErrorResponse(response, fallback = '请求失败，请稍后重试') {
  let payload = null
  try {
    payload = await response.clone().json()
  } catch {
    payload = null
  }

  const detailFromPayload = payload?.detail
  const messageFromPayload = payload?.message
  const detailsFromPayload = payload?.details

  const rawText = [
    flattenDetail(detailFromPayload),
    flattenDetail(messageFromPayload),
    flattenDetail(detailsFromPayload),
  ]
    .filter(Boolean)
    .join('；')

  if (isLocalRuntime()) {
    // 本地版没有积分体系：402 / 余额不足来自模型服务商
    if (response.status === 402 || isProviderBalanceText(rawText)) {
      return rawText ? `${PROVIDER_BALANCE_HINT}（${rawText}）` : PROVIDER_BALANCE_HINT
    }
    return rawText || fallback
  }

  if (response.status === 402 || isInsufficientCreditsText(rawText)) {
    return INSUFFICIENT_CREDITS_MESSAGE
  }

  if (rawText) return rawText
  return fallback
}
