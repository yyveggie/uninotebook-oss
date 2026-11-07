import { apiRequest } from './api'
import { dataCache } from './dataCache'

const STATUS_CACHE_TTL = 30 * 1000
const FINAL_STATUSES = new Set(['completed', 'failed'])

const buildStatusCacheKey = (documentId) => {
  if (!documentId) return null
  return `doc:${documentId}:status`
}

export const readDocumentStatusCache = (documentId) => {
  const key = buildStatusCacheKey(documentId)
  if (!key) return null
  return dataCache.get(key)
}

export const invalidateDocumentStatusCache = (documentId) => {
  const key = buildStatusCacheKey(documentId)
  if (!key) return
  dataCache.invalidate(key)
}

export const fetchDocumentStatus = async (documentId, options = {}) => {
  if (!documentId) {
    throw new Error('documentId is required')
  }

  const { useCache = true } = options
  const cacheKey = buildStatusCacheKey(documentId)

  if (useCache && cacheKey) {
    const cached = dataCache.get(cacheKey)
    if (cached) {
      return {
        ok: true,
        status: 200,
        data: cached,
        fromCache: true,
      }
    }
  }

  const response = await apiRequest(`/api/document/${documentId}/status`, {
    method: 'GET',
  }, {
    key: `GET /api/document/${documentId}/status`,
  })

  let parsed = null
  try {
    parsed = await response.json()
  } catch {
    parsed = null
  }

  if (response.ok && parsed?.success && FINAL_STATUSES.has(parsed.data?.processing_status)) {
    if (cacheKey) {
      dataCache.set(cacheKey, parsed, STATUS_CACHE_TTL)
    }
  } else if (cacheKey) {
    dataCache.invalidate(cacheKey)
  }

  return {
    ok: response.ok,
    status: response.status,
    data: parsed,
    fromCache: false,
  }
}

