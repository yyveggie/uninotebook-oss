import { useRef, useState } from 'react'
import { apiGet, canCallAuthenticatedApi } from '../utils/api'
import { dataCache } from '../utils/dataCache'

const HISTORY_CACHE_TTL = 60 * 1000

export default function useRightPanelHistory({ state }) {
  const { documentId } = state
  const [flowchartHistory, setFlowchartHistory] = useState([])
  const [flashcardsHistory, setFlashcardsHistory] = useState([])
  const [quizHistory, setQuizHistory] = useState([])
  const [mindmapHistory, setMindmapHistory] = useState([])
  const [highlightsHistory, setHighlightsHistory] = useState([])
  const [reportHistory, setReportHistory] = useState([])
  const [interviewHistory, setInterviewHistory] = useState([])
  const [pptHistory, setPptHistory] = useState([])
  const [posterHistory, setPosterHistory] = useState([])
  const [loadingHistory, setLoadingHistory] = useState(false)

  const historyLoadingCountRef = useRef(0)

  const startHistoryLoading = () => {
    historyLoadingCountRef.current += 1
    if (historyLoadingCountRef.current === 1) {
      setLoadingHistory(true)
    }
  }

  const finishHistoryLoading = () => {
    historyLoadingCountRef.current = Math.max(0, historyLoadingCountRef.current - 1)
    if (historyLoadingCountRef.current === 0) {
      setLoadingHistory(false)
    }
  }

  const invalidateHistoryCache = (types) => {
    if (!documentId) return
    const prefix = `doc:${documentId}:history:`
    if (!types) {
      dataCache.invalidateByPrefix(prefix)
      return
    }
    const list = Array.isArray(types) ? types : [types]
    list.forEach((type) => {
      dataCache.invalidate(`${prefix}${type}`)
    })
  }

  const fetchHistoryData = async (type, endpoint, options = {}) => {
    if (!documentId) return null
    const { withLoading = true, forceRefresh = false } = options

    // 本地版没有 token 也可加载；SaaS 会话未建立时跳过（调用方的 effect 会在就绪后重跑）
    if (!canCallAuthenticatedApi()) {
      return null
    }

    const cacheKey = `doc:${documentId}:history:${type}`
    if (forceRefresh) {
      dataCache.invalidate(cacheKey)
    }

    if (withLoading) {
      startHistoryLoading()
    }

    try {
      return await apiGet(endpoint, {
        cacheKey,
        cacheTTL: HISTORY_CACHE_TTL,
        cacheMode: forceRefresh ? 'no-cache' : 'default',
        requestConfig: { key: `GET ${endpoint}` },
      })
    } catch {
      return null
    } finally {
      if (withLoading) {
        finishHistoryLoading()
      }
    }
  }

  const loadFlowchartHistory = async (options = {}) => {
    const data = await fetchHistoryData('flowchart', `/api/document/${documentId}/flowchart-history`, options)
    if (data?.success) {
      setFlowchartHistory(data.data || [])
    }
  }

  const loadFlashcardsHistory = async (options = {}) => {
    const data = await fetchHistoryData('flashcards', `/api/document/${documentId}/flashcards-history`, options)
    if (data?.success) {
      setFlashcardsHistory(data.data || [])
    }
  }

  const loadQuizHistory = async (options = {}) => {
    const data = await fetchHistoryData('quiz', `/api/document/${documentId}/quiz-history`, options)
    if (data?.success) {
      setQuizHistory(data.data || [])
    }
  }

  const loadMindmapHistory = async (options = {}) => {
    const data = await fetchHistoryData('mindmap', `/api/document/${documentId}/mindmap-history`, options)
    if (data?.success) {
      setMindmapHistory(data.data || [])
    }
  }

  const loadHighlightsHistory = async (options = {}) => {
    const data = await fetchHistoryData('highlights', `/api/document/${documentId}/highlights-history`, options)
    if (data?.success) {
      setHighlightsHistory(data.data || [])
    }
  }

  const loadReportHistory = async (options = {}) => {
    const data = await fetchHistoryData('report', `/api/document/${documentId}/report-history`, options)
    if (data?.success) {
      setReportHistory(data.data || [])
    }
  }

  const loadPptHistory = async (options = {}) => {
    const data = await fetchHistoryData('ppt', `/api/ppt/document/${documentId}/history`, options)
    if (data?.success) {
      setPptHistory(data.data || [])
    }
  }

  const loadPosterHistory = async (options = {}) => {
    const data = await fetchHistoryData('poster', `/api/poster/document/${documentId}/history`, options)
    if (data?.success) {
      setPosterHistory(data.data || [])
    }
  }

  const loadInterviewHistory = async (options = {}) => {
    const data = await fetchHistoryData('interview', `/api/document/${documentId}/interview/history`, options)
    if (data?.success) {
      setInterviewHistory(data.history || [])
    }
  }

  return {
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
  }
}
