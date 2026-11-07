import { useEffect, useRef } from 'react'
import { apiRequest } from '../utils/api'
import { parseTaskErrorResponse, normalizeTaskErrorMessage } from '../utils/taskError'
import useDocumentStore from '../stores/documentStore'
import useAuthStore from '../stores/authStore'
import useUIStore from '../stores/uiStore'

/**
 * AI 生成任务 hook：流程图、闪卡、测验、思维导图、重点提炼、优缺点、通俗举例
 * 所有函数名/参数名与原 App.jsx 完全一致
 *
 * 生成任务都在后台轮询，期间用户可能切换文档、登出：
 * - 结果只写回发起任务的那个文档（切走后只让该文档的历史缓存失效，回来时能看到新记录）；
 * - 失败用 toast 提示，而不是 setError——后者会让 App 用全屏错误层替换整个工作区，
 *   连带卸载对话、工作室和未保存的笔记；
 * - 登出或卸载时取消所有轮询。
 */
export default function useGenerationActions(rightPanelRef) {
  const getDoc = () => useDocumentStore.getState()

  // 进行中的轮询（取消函数集合）
  const activePollsRef = useRef(new Set())

  useEffect(() => {
    const polls = activePollsRef.current
    const cancelAll = () => {
      Array.from(polls).forEach((cancel) => cancel())
      polls.clear()
    }
    // 登出（user 由有变无）：结果属于上一个账号，会话也已失效
    const unsubscribe = useAuthStore.subscribe((state, prevState) => {
      if (prevState.user && !state.user) cancelAll()
    })
    return () => {
      unsubscribe()
      cancelAll()
    }
  }, [])

  const isCurrentDoc = (documentId) => getDoc().currentDocument?.document_id === documentId

  const notifyFailure = (message, fallback) => {
    useUIStore.getState().showToast(normalizeTaskErrorMessage(message, fallback))
  }

  const throwTaskResponseError = async (response, fallbackMessage) => {
    throw new Error(await parseTaskErrorResponse(response, fallbackMessage))
  }

  // ========== pollGenerationTask (内部工具) ==========
  // 返回取消函数；被取消时不再回调 onComplete / onError，而是调用可选的 onCancel
  const pollGenerationTask = (taskId, onComplete, onError, onCancel) => {
    const maxAttempts = 90
    let attempts = 0
    let cancelled = false
    let timer = null
    const polls = activePollsRef.current

    const cancel = () => {
      if (cancelled) return
      cancelled = true
      if (timer) clearTimeout(timer)
      polls.delete(cancel)
      onCancel?.()
    }
    polls.add(cancel)

    const retryLater = () => {
      if (!cancelled) timer = setTimeout(poll, 2000)
    }

    const finish = (callback, value) => {
      if (cancelled) return
      polls.delete(cancel)
      callback(value)
    }

    const poll = async () => {
      if (cancelled) return
      attempts++
      try {
        const response = await apiRequest(`/api/generation-task/${taskId}/status`)
        if (cancelled) return

        if (!response.ok) {
          if (attempts < maxAttempts) {
            retryLater()
            return
          }
          finish(onError, '任务状态查询失败')
          return
        }

        const result = await response.json()
        if (cancelled) return

        if (result.status === 'completed' && result.success) {
          finish(onComplete, result.data)
        } else if (result.status === 'failed') {
          finish(onError, result.error || '生成失败')
        } else {
          if (attempts < maxAttempts) {
            retryLater()
          } else {
            finish(onError, '任务超时，请稍后重试')
          }
        }
      } catch (err) {
        if (cancelled) return
        if (attempts < maxAttempts) {
          retryLater()
        } else {
          finish(onError, err.message || '网络错误')
        }
      }
    }

    poll()
    return cancel
  }

  // ========== handleGenerateFlowchart ==========
  const handleGenerateFlowchart = async () => {
    const { currentDocument, isGeneratingFlowchart } = getDoc()
    if (!currentDocument || isGeneratingFlowchart) return
    const documentId = currentDocument.document_id

    getDoc().setIsGeneratingFlowchart(true)
    getDoc().setError(null)

    try {
      const response = await apiRequest(`/api/document/${documentId}/generate-path`, {
        method: 'POST'
      })

      if (!response.ok) {
        await throwTaskResponseError(response, '生成学习路径失败')
      }

      const data = await response.json()

      if (data.success && data.data) {
        getDoc().invalidateDocumentHistoryCache(documentId, 'flowchart')
        if (isCurrentDoc(documentId)) {
          getDoc().setDocumentFlowchart(data.data)
          getDoc().setRightPanelView('flowchart')
        }
      } else {
        throw new Error(data.message || '生成失败')
      }
    } catch (err) {
      if (isCurrentDoc(documentId)) {
        notifyFailure(err.message, '生成学习路径时出现问题')
      }
    } finally {
      if (isCurrentDoc(documentId)) {
        getDoc().setIsGeneratingFlowchart(false)
      }
    }
  }

  // ========== handleGenerateFlashcards ==========
  const handleGenerateFlashcards = async (options = null) => {
    const { currentDocument, isGeneratingFlashcards } = getDoc()
    if (!currentDocument || isGeneratingFlashcards) return
    const documentId = currentDocument.document_id

    getDoc().setIsGeneratingFlashcards(true)
    getDoc().setError(null)

    try {
      const payload = options && typeof options === 'object'
        ? {
            card_count: options.card_count,
            difficulty: options.difficulty,
            requirements: options.requirements
          }
        : undefined

      const response = await apiRequest(`/api/document/${documentId}/generate-flashcards`, {
        method: 'POST',
        body: payload ? JSON.stringify(payload) : undefined
      })

      if (!response.ok) {
        await throwTaskResponseError(response, '提交任务失败')
      }

      const data = await response.json()

      if (data.success && data.task_id) {
        pollGenerationTask(
          data.task_id,
          (flashcardsData) => {
            getDoc().invalidateDocumentHistoryCache(documentId, 'flashcards')
            if (!isCurrentDoc(documentId)) return
            getDoc().setFlashcards(flashcardsData)
            getDoc().setRightPanelView('flashcards')
            getDoc().setIsGeneratingFlashcards(false)
          },
          (error) => {
            if (!isCurrentDoc(documentId)) return
            notifyFailure(error, '闪卡生成失败')
            getDoc().setIsGeneratingFlashcards(false)
          }
        )
      } else {
        throw new Error(data.message || '任务提交失败')
      }
    } catch (err) {
      if (isCurrentDoc(documentId)) {
        notifyFailure(err.message, '生成闪卡时出现问题')
        getDoc().setIsGeneratingFlashcards(false)
      }
    }
  }

  // ========== handleGenerateQuiz ==========
  const handleGenerateQuiz = async (options = null) => {
    const { currentDocument, isGeneratingQuiz } = getDoc()
    if (!currentDocument || isGeneratingQuiz) return
    const documentId = currentDocument.document_id

    getDoc().setIsGeneratingQuiz(true)
    getDoc().setError(null)

    try {
      const payload = options && typeof options === 'object'
        ? {
            question_count: options.question_count,
            difficulty: options.difficulty,
            requirements: options.requirements
          }
        : undefined

      const response = await apiRequest(`/api/document/${documentId}/generate-quiz`, {
        method: 'POST',
        body: payload ? JSON.stringify(payload) : undefined
      })

      if (!response.ok) {
        await throwTaskResponseError(response, '提交任务失败')
      }

      const data = await response.json()

      if (data.success && data.task_id) {
        pollGenerationTask(
          data.task_id,
          (quizData) => {
            getDoc().invalidateDocumentHistoryCache(documentId, 'quiz')
            if (!isCurrentDoc(documentId)) return
            getDoc().setQuiz(quizData)
            getDoc().setRightPanelView('quiz')
            getDoc().setIsGeneratingQuiz(false)
          },
          (error) => {
            if (!isCurrentDoc(documentId)) return
            notifyFailure(error, '测验生成失败')
            getDoc().setIsGeneratingQuiz(false)
          }
        )
      } else {
        throw new Error(data.message || '任务提交失败')
      }
    } catch (err) {
      if (isCurrentDoc(documentId)) {
        notifyFailure(err.message, '生成测验时出现问题')
        getDoc().setIsGeneratingQuiz(false)
      }
    }
  }

  // ========== handleGenerateMindmap ==========
  const handleGenerateMindmap = async () => {
    const { currentDocument, isGeneratingMindmap } = getDoc()
    if (!currentDocument || isGeneratingMindmap) return
    const documentId = currentDocument.document_id

    getDoc().setIsGeneratingMindmap(true)
    getDoc().setError(null)

    try {
      const response = await apiRequest(`/api/document/${documentId}/generate-mindmap`, {
        method: 'POST'
      })

      if (!response.ok) {
        await throwTaskResponseError(response, '提交任务失败')
      }

      const data = await response.json()

      if (data.success && data.task_id) {
        pollGenerationTask(
          data.task_id,
          (mindmapData) => {
            getDoc().invalidateDocumentHistoryCache(documentId, 'mindmap')
            if (!isCurrentDoc(documentId)) return
            getDoc().setMindmap(mindmapData)
            getDoc().setRightPanelView('mindmap')
            getDoc().setIsGeneratingMindmap(false)
          },
          (error) => {
            if (!isCurrentDoc(documentId)) return
            notifyFailure(error, '思维导图生成失败')
            getDoc().setIsGeneratingMindmap(false)
          }
        )
      } else {
        throw new Error(data.message || '任务提交失败')
      }
    } catch (err) {
      if (isCurrentDoc(documentId)) {
        notifyFailure(err.message, '生成思维导图时出现问题')
        getDoc().setIsGeneratingMindmap(false)
      }
    }
  }

  // ========== handleGenerateHighlights ==========
  const handleGenerateHighlights = async (options = null) => {
    const { currentDocument, isGeneratingHighlights } = getDoc()
    if (!currentDocument || isGeneratingHighlights) return
    const documentId = currentDocument.document_id

    getDoc().setIsGeneratingHighlights(true)
    getDoc().setError(null)

    try {
      const payload = {}
      if (options && typeof options === 'object') {
        if (options.point_count !== undefined && options.point_count !== null && options.point_count !== '') {
          payload.point_count = options.point_count
        }
        if (options.difficulty) {
          payload.difficulty = options.difficulty
        }
        if (options.requirements) {
          payload.requirements = options.requirements
        }
      }

      const response = await apiRequest(`/api/document/${documentId}/generate-highlights`, {
        method: 'POST',
        ...(Object.keys(payload).length > 0
          ? {
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            }
          : {})
      })

      if (!response.ok) {
        await throwTaskResponseError(response, `生成重点提炼失败 (${response.status})`)
      }

      const data = await response.json()

      if (data.success && data.task_id) {
        const result = await new Promise((resolve, reject) => {
          pollGenerationTask(
            data.task_id,
            (taskResult) => resolve(taskResult),
            (errorMsg) => reject(new Error(errorMsg)),
            // 登出 / 卸载取消轮询时结束等待，避免 Promise 永远挂起
            () => resolve(null)
          )
        })

        if (result) {
          getDoc().invalidateDocumentHistoryCache(documentId, 'highlights')
          if (isCurrentDoc(documentId)) {
            getDoc().setHighlights(result)
            getDoc().setRightPanelView('highlights')
          }
        }
      } else if (data.success && data.data) {
        // 兼容旧版同步响应
        getDoc().invalidateDocumentHistoryCache(documentId, 'highlights')
        if (isCurrentDoc(documentId)) {
          getDoc().setHighlights(data.data)
          getDoc().setRightPanelView('highlights')
        }
      } else {
        throw new Error(data.message || '生成失败')
      }
    } catch (err) {
      if (isCurrentDoc(documentId)) {
        notifyFailure(err.message, '生成重点提炼时出现问题')
      }
    } finally {
      // 已切换到其他文档时，生成状态已随文档重置，不能误改新文档的状态
      if (isCurrentDoc(documentId)) {
        getDoc().setIsGeneratingHighlights(false)
      }
    }
  }

  const handleGenerateReport = async (options = null) => {
    const { currentDocument, isGeneratingReport } = getDoc()
    if (!currentDocument || isGeneratingReport) return
    const documentId = currentDocument.document_id

    getDoc().setIsGeneratingReport(true)
    getDoc().setError(null)

    try {
      const payload = options && typeof options === 'object'
        ? {
            report_type: options.report_type,
            custom_style: options.custom_style,
            focus_points: options.focus_points,
            requirements: options.requirements,
          }
        : undefined

      const response = await apiRequest(`/api/document/${documentId}/generate-report`, {
        method: 'POST',
        body: payload ? JSON.stringify(payload) : undefined
      })

      if (!response.ok) {
        await throwTaskResponseError(response, '提交任务失败')
      }

      const data = await response.json()

      if (data.success && data.task_id) {
        pollGenerationTask(
          data.task_id,
          (reportData) => {
            getDoc().invalidateDocumentHistoryCache(documentId, 'report')
            if (!isCurrentDoc(documentId)) return
            getDoc().setReport(reportData)
            getDoc().setRightPanelView('studio')
            getDoc().setIsGeneratingReport(false)
          },
          (error) => {
            if (!isCurrentDoc(documentId)) return
            notifyFailure(error, '长报告生成失败')
            getDoc().setIsGeneratingReport(false)
          }
        )
      } else {
        throw new Error(data.message || '任务提交失败')
      }
    } catch (err) {
      if (isCurrentDoc(documentId)) {
        notifyFailure(err.message, '生成长报告时出现问题')
        getDoc().setIsGeneratingReport(false)
      }
    }
  }

  // ========== handleGenerateProsCons ==========
  const handleGenerateProsCons = async () => {
    const { currentDocument, isGeneratingProsCons } = getDoc()
    if (!currentDocument || isGeneratingProsCons) return
    const documentId = currentDocument.document_id

    getDoc().setIsGeneratingProsCons(true)
    getDoc().setError(null)

    try {
      const response = await apiRequest(`/api/document/${documentId}/generate-pros-cons`, {
        method: 'POST'
      })

      if (!response.ok) {
        await throwTaskResponseError(response, `生成优缺点分析失败 (${response.status})`)
      }

      const data = await response.json()

      if (data.success) {
        alert('优缺点分析已生成并保存到笔记！')
        if (rightPanelRef.current && isCurrentDoc(documentId)) {
          rightPanelRef.current.refreshNotes()
        }
      } else {
        throw new Error(data.message || '生成失败')
      }
    } catch (err) {
      notifyFailure(err.message, '生成优缺点分析时出现问题')
    } finally {
      getDoc().setIsGeneratingProsCons(false)
    }
  }

  // ========== handleGenerateSimpleExample ==========
  const handleGenerateSimpleExample = async () => {
    const { currentDocument, isGeneratingSimpleExample } = getDoc()
    if (!currentDocument || isGeneratingSimpleExample) return
    const documentId = currentDocument.document_id

    getDoc().setIsGeneratingSimpleExample(true)
    getDoc().setError(null)

    try {
      const response = await apiRequest(`/api/document/${documentId}/generate-simple-example`, {
        method: 'POST'
      })

      if (!response.ok) {
        await throwTaskResponseError(response, `生成通俗例子失败 (${response.status})`)
      }

      const data = await response.json()

      if (data.success) {
        alert('通俗例子已生成并保存到笔记！')
        if (rightPanelRef.current && isCurrentDoc(documentId)) {
          rightPanelRef.current.refreshNotes()
        }
      } else {
        throw new Error(data.message || '生成失败')
      }
    } catch (err) {
      notifyFailure(err.message, '生成通俗例子时出现问题')
    } finally {
      getDoc().setIsGeneratingSimpleExample(false)
    }
  }

  // ========== handleStopAllGenerations ==========
  const handleStopAllGenerations = () => {
    getDoc().stopAllGenerations()
  }

  return {
    handleGenerateFlowchart,
    handleGenerateFlashcards,
    handleGenerateQuiz,
    handleGenerateMindmap,
    handleGenerateHighlights,
    handleGenerateReport,
    handleGenerateProsCons,
    handleGenerateSimpleExample,
    handleStopAllGenerations,
  }
}
