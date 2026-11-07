import { useEffect, useRef } from 'react'
import { apiRequest } from '../utils/api'
import { parseTaskErrorResponse, normalizeTaskErrorMessage } from '../utils/taskError'
import { TASK_POLLING_INTERVAL_MS } from '../utils/config'

export default function useRightPanelTaskPolling({
  state,
  actions,
  loaders,
}) {
  const {
    documentId,
    pptTaskId,
    posterTaskId,
  } = state

  // PPT / 海报轮询所需的 setter 与 loader 通过 latestRef 读取，见下方说明
  const {
    setErrorDialogState,
    invalidateHistoryCache,
    setIsPreparingInterview,
    setResumeInterviewData,
    setShowInterviewDialog,
  } = actions

  const {
    loadInterviewHistory,
  } = loaders

  // RightPanel 每次渲染都会重新创建 actions / loaders 里的函数。
  // 轮询 effect 若直接依赖它们，定时器会随每次渲染被拆掉重建：
  // pollingStartTime 被不断重置（30 分钟超时永不触发），渲染频繁时甚至一次都轮询不到。
  // 因此轮询只依赖 taskId，回调统一从 ref 读取最新版本。
  const latestRef = useRef({ actions, loaders })
  useEffect(() => {
    latestRef.current = { actions, loaders }
  })

  const interviewPollingRef = useRef({
    timeoutId: null,
    cancelled: false,
  })

  const clearInterviewPolling = () => {
    if (interviewPollingRef.current.timeoutId) {
      clearTimeout(interviewPollingRef.current.timeoutId)
      interviewPollingRef.current.timeoutId = null
    }
    interviewPollingRef.current.cancelled = true
  }

  const startInterviewPolling = () => {
    clearInterviewPolling()
    interviewPollingRef.current.cancelled = false
  }

  useEffect(() => {
    return () => {
      clearInterviewPolling()
    }
  }, [])

  useEffect(() => {
    let interval
    if (pptTaskId) {
      const getActions = () => latestRef.current.actions
      const getLoaders = () => latestRef.current.loaders
      getActions().setIsGeneratingPpt(true)
      const pollingStartTime = Date.now()
      const MAX_POLLING_MS = 30 * 60 * 1000

      interval = setInterval(async () => {
        if (Date.now() - pollingStartTime > MAX_POLLING_MS) {
          getActions().setIsGeneratingPpt(false)
          getActions().setPptTaskId(null)
          clearInterval(interval)
          getActions().setErrorDialogState({
            isOpen: true,
            message: 'PPT 生成超时，请稍后再试'
          })
          return
        }

        try {
          const res = await apiRequest(`/api/ppt/status/${pptTaskId}`)
          const data = await res.json()

          if (data.status === 'completed') {
            getActions().setPptProgress(100)
            getActions().setIsGeneratingPpt(false)
            getActions().setPptTaskId(null)
            clearInterval(interval)
            getActions().invalidateHistoryCache('ppt')
            getLoaders().loadPptHistory({ forceRefresh: true })
            alert('PPT 生成完成！')
          } else if (data.status === 'failed') {
            getActions().setIsGeneratingPpt(false)
            getActions().setPptTaskId(null)
            clearInterval(interval)
            getActions().setErrorDialogState({
              isOpen: true,
              message: data.error_message || data.error || 'PPT 生成失败，请重试'
            })
          } else {
            getActions().setPptStatusText(data.current_stage || data.status)
            if (data.progress) getActions().setPptProgress(data.progress)
          }
        } catch {
          // ignore polling errors and continue retrying
        }
      }, TASK_POLLING_INTERVAL_MS)
    }

    return () => clearInterval(interval)
  }, [pptTaskId])

  useEffect(() => {
    let interval
    if (posterTaskId) {
      const getActions = () => latestRef.current.actions
      const getLoaders = () => latestRef.current.loaders
      getActions().setIsGeneratingPoster(true)
      const pollingStartTime = Date.now()
      const MAX_POLLING_MS = 30 * 60 * 1000

      interval = setInterval(async () => {
        if (Date.now() - pollingStartTime > MAX_POLLING_MS) {
          getActions().setIsGeneratingPoster(false)
          getActions().setPosterTaskId(null)
          clearInterval(interval)
          getActions().setErrorDialogState({
            isOpen: true,
            message: '海报生成超时，请稍后再试'
          })
          return
        }

        try {
          const res = await apiRequest(`/api/poster/status/${posterTaskId}`)
          const data = await res.json()

          if (data.status === 'completed') {
            getActions().setPosterProgress(100)
            getActions().setIsGeneratingPoster(false)
            getActions().setPosterTaskId(null)
            clearInterval(interval)
            getActions().invalidateHistoryCache('poster')
            getLoaders().loadPosterHistory({ forceRefresh: true })
          } else if (data.status === 'failed') {
            getActions().setIsGeneratingPoster(false)
            getActions().setPosterTaskId(null)
            clearInterval(interval)
            getActions().setErrorDialogState({
              isOpen: true,
              message: `海报生成失败: ${data.error_message || data.error || '未知错误'}`
            })
          } else {
            getActions().setPosterStatusText(data.current_stage || data.status)
            if (data.progress) getActions().setPosterProgress(data.progress)
          }
        } catch {
          // ignore polling errors and continue retrying
        }
      }, TASK_POLLING_INTERVAL_MS)
    }

    return () => clearInterval(interval)
  }, [posterTaskId])

  const handleStartInterview = async () => {
    if (!documentId) return

    try {
      startInterviewPolling()
      setIsPreparingInterview(true)

      const response = await apiRequest(`/api/document/${documentId}/interview/start`, {
        method: 'POST'
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '面试初始化失败，请重试'))
      }

      const data = await response.json()

      if (data.success && data.task_id) {
        const maxAttempts = 90
        let attempts = 0

        const poll = async () => {
          if (interviewPollingRef.current.cancelled) {
            setIsPreparingInterview(false)
            return
          }

          attempts++
          try {
            const statusResponse = await apiRequest(`/api/generation-task/${data.task_id}/status`)
            const statusData = await statusResponse.json()

            if (interviewPollingRef.current.cancelled) {
              setIsPreparingInterview(false)
              return
            }

            if (statusData.status === 'completed' && statusData.success) {
              setResumeInterviewData({
                sessionId: statusData.session_id,
                firstQuestion: statusData.first_question,
                previousQA: []
              })
              setShowInterviewDialog(true)
              invalidateHistoryCache('interview')
              await loadInterviewHistory({ withLoading: false, forceRefresh: true })
              clearInterviewPolling()
              setIsPreparingInterview(false)
            } else if (statusData.status === 'failed') {
              // 任务已明确失败：属于终态，不能落入下方的重试分支空转数分钟
              const failure = new Error(statusData.error || '初始化失败')
              failure.isTerminal = true
              throw failure
            } else if (attempts < maxAttempts) {
              interviewPollingRef.current.timeoutId = setTimeout(poll, 2000)
            } else {
              const timeoutError = new Error('任务超时，请稍后重试')
              timeoutError.isTerminal = true
              throw timeoutError
            }
          } catch (err) {
            if (interviewPollingRef.current.cancelled) {
              setIsPreparingInterview(false)
              return
            }

            // 只有网络抖动等临时错误才重试
            if (!err?.isTerminal && attempts < maxAttempts) {
              interviewPollingRef.current.timeoutId = setTimeout(poll, 2000)
            } else {
              clearInterviewPolling()
              setIsPreparingInterview(false)
              setErrorDialogState({
                isOpen: true,
                message: normalizeTaskErrorMessage(err.message, '面试初始化失败，请重试')
              })
            }
          }
        }

        poll()
      } else if (data.session_id) {
        setResumeInterviewData({
          sessionId: data.session_id,
          firstQuestion: data.first_question,
          previousQA: []
        })
        setShowInterviewDialog(true)
        invalidateHistoryCache('interview')
        await loadInterviewHistory({ withLoading: false, forceRefresh: true })
        clearInterviewPolling()
        setIsPreparingInterview(false)
      } else {
        throw new Error('初始化失败')
      }
    } catch (err) {
      clearInterviewPolling()
      setIsPreparingInterview(false)
      setErrorDialogState({
        isOpen: true,
        message: normalizeTaskErrorMessage(err?.message, '面试初始化失败，请重试')
      })
    }
  }

  return {
    handleStartInterview,
  }
}
