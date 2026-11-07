import React, { useState, useEffect } from 'react'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import { apiRequest } from '../utils/api'
import { parseTaskErrorResponse, normalizeTaskErrorMessage } from '../utils/taskError'
import './InterviewDialog.css'

/**
 * 面试对话框组件
 * 实现智能面试功能
 * 注意：初始化在 RightPanel 中完成，此组件仅显示问答过程
 */
const InterviewDialog = ({ documentId, onClose, resumeData = null }) => {
  const [status, setStatus] = useState('questioning') // questioning | evaluating | completed | error
  const [sessionId, setSessionId] = useState(null)
  const [currentQuestion, setCurrentQuestion] = useState(null)
  const [userAnswer, setUserAnswer] = useState('')
  const [evaluation, setEvaluation] = useState(null)
  const [progress, setProgress] = useState({ current: 0, total: 10, avg_score: 0 })
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [isResumed, setIsResumed] = useState(false) // 是否为恢复模式
  const [answerError, setAnswerError] = useState('')
  const [showAbortConfirm, setShowAbortConfirm] = useState(false)
  const [pendingTransition, setPendingTransition] = useState(null)

  // 新增：历史问答记录
  const [qaHistory, setQaHistory] = useState([])
  const [showPreviousQA, setShowPreviousQA] = useState(false)

  // 初始化面试
  useEffect(() => {
    // 必须有 resumeData 才能初始化
    if (resumeData) {
      setSessionId(resumeData.sessionId)
      setCurrentQuestion({
        round: resumeData.firstQuestion.round,
        question: resumeData.firstQuestion.question,
        hints: resumeData.firstQuestion.hints
      })
      setProgress({
        current: resumeData.firstQuestion.round,
        total: resumeData.firstQuestion.total_rounds || 10,
        avg_score: resumeData.firstQuestion.avg_score || 0
      })

      // 恢复之前的问答历史
      if (resumeData.previousQA && resumeData.previousQA.length > 0) {
        const formattedHistory = resumeData.previousQA.map(qa => ({
          round: qa.round,
          question: qa.question,
          answer: qa.answer,
          evaluation: qa.evaluation
        }))
        setQaHistory(formattedHistory)
        setIsResumed(true)
      }

      setStatus('questioning')
    } else {
      // 没有 resumeData 时显示错误
      setError('面试数据缺失')
      setStatus('error')
    }
  }, [documentId, resumeData])

  const validateAnswer = (rawAnswer) => {
    const trimmed = (rawAnswer || '').trim()
    if (!trimmed) {
      return '请输入您的回答'
    }

    const compact = trimmed.replace(/\s+/g, '')
    const uniqueCharCount = new Set(compact).size
    if (uniqueCharCount < 10) {
      return '回答内容重复度较高，请补充更多具体观点'
    }

    return ''
  }

  const proceedAfterEvaluation = () => {
    if (!pendingTransition) return

    if (pendingTransition.type === 'completed') {
      setSummary(pendingTransition.summary)
      setPendingTransition(null)
      setStatus('completed')
      return
    }

    if (pendingTransition.type === 'next') {
      setCurrentQuestion(pendingTransition.nextQuestion)
      setProgress(pendingTransition.progress)
      setUserAnswer('')
      setAnswerError('')
      setEvaluation(null)
      setPendingTransition(null)
      setStatus('questioning')
      return
    }

    setPendingTransition(null)
    setError('获取下一题失败，请尝试刷新重试')
    setStatus('error')
  }


  // 提交答案
  const submitAnswer = async () => {
    const validationError = validateAnswer(userAnswer)
    if (validationError) {
      setAnswerError(validationError)
      return
    }

    try {
      setAnswerError('')
      setStatus('evaluating')
      setLoading(true)
      setPendingTransition(null)

      const response = await apiRequest(`/api/document/${documentId}/interview/answer`, {
        method: 'POST',
        body: JSON.stringify({
          session_id: sessionId,
          answer: userAnswer
        })
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, `请求失败 (${response.status})`))
      }

      const data = await response.json()

      if (data.success) {
        setEvaluation(data.evaluation)

        // 保存到历史记录
        setQaHistory(prev => [...prev, {
          round: progress.current,
          question: currentQuestion.question,
          answer: userAnswer,
          evaluation: data.evaluation
        }])

        if (data.status === 'completed') {
          setPendingTransition({ type: 'completed', summary: data.summary })
        } else if (data.next_question) {
          setPendingTransition({
            type: 'next',
            nextQuestion: data.next_question,
            progress: data.progress,
          })
        } else {
          console.error('Missing next_question in response:', data)
          setPendingTransition({ type: 'error' })
        }
      } else {
        throw new Error('提交失败')
      }
    } catch (err) {
      console.error('Submit Answer Error:', err)
      setError(normalizeTaskErrorMessage(err.message, '提交答案失败，请重试'))
      setStatus('error')
    } finally {
      setLoading(false)
    }
  }

  // 中止面试
  const abortInterview = async () => {
    try {
      await apiRequest(`/api/document/${documentId}/interview/abort`, {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId })
      })
    } catch (err) {
      void err;
    }

    setShowAbortConfirm(false)
    onClose(true) // 传递 true 表示需要刷新
  }

  // 获取进度百分比
  const getProgressPercent = () => {
    if (!progress.current || !progress.total) return 0
    return Math.round((progress.current / progress.total) * 100)
  }

  // 获取分数颜色
  const getScoreColor = (score) => {
    if (score >= 90) return '#10b981' // 绿色
    if (score >= 75) return '#3b82f6' // 蓝色
    if (score >= 60) return '#f59e0b' // 橙色
    return '#ef4444' // 红色
  }

  return (
    <div className="interview-dialog-overlay" onClick={(e) => {
      if (e.target.className === 'interview-dialog-overlay') {
        setShowAbortConfirm(true)
      }
    }}>
      <motion.div
        className="interview-dialog"
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.9 }}
        transition={{ duration: 0.2 }}
      >
        {/* 头部 */}
        <div className="interview-header">
          <h2>面试中...</h2>
          <button className="interview-close-btn" onClick={() => setShowAbortConfirm(true)}>✕</button>
        </div>

        {showAbortConfirm && (
          <div className="interview-abort-confirm">
            <p className="interview-abort-confirm-text">确定要结束面试吗？当前进度会保存，可稍后继续。</p>
            <div className="interview-abort-confirm-actions">
              <button type="button" className="interview-abort-cancel-btn" onClick={() => setShowAbortConfirm(false)}>
                继续作答
              </button>
              <button type="button" className="interview-abort-confirm-btn" onClick={abortInterview}>
                结束面试
              </button>
            </div>
          </div>
        )}

        {/* 进度条 */}
        {status !== 'initializing' && status !== 'error' && (
          <div className="interview-progress-section">
            <div className="interview-progress-bar">
              <div
                className="interview-progress-fill"
                style={{ width: `${getProgressPercent()}%` }}
              ></div>
            </div>
            <div className="interview-progress-text">
              <span>第 {progress.current}/{progress.total} 轮</span>
              {progress.avg_score > 0 && (
                <span style={{ color: getScoreColor(progress.avg_score) }}>
                  平均分: {progress.avg_score.toFixed(1)}
                </span>
              )}
            </div>
          </div>
        )}

        {/* 内容区域 */}
        <div className="interview-content">
          <AnimatePresence mode="wait">
            {/* 提问阶段 */}
            {status === 'questioning' && currentQuestion && (
              <motion.div
                key="questioning"
                className="interview-state interview-questioning"
                initial={{ opacity: 0, x: 50 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -50 }}
                transition={{ duration: 0.3 }}
              >
                {/* 恢复模式提示 */}
                {isResumed && (
                  <div className="interview-resume-notice">
                    <span className="resume-icon">🔄</span>
                    <span className="resume-text">继续之前的面试（已完成 {progress.current - 1} 轮）</span>
                  </div>
                )}

                {/* 查看历史记录按钮 */}
                {qaHistory.length > 0 && (
                  <div className="interview-previous-qa-toggle">
                    <button
                      className="interview-previous-qa-btn"
                      onClick={() => setShowPreviousQA(!showPreviousQA)}
                    >
                      {showPreviousQA ? '▼ 隐藏历史记录' : `▶ 查看历史记录 (${qaHistory.length} 题)`}
                    </button>
                  </div>
                )}

                {/* 历史记录内容 */}
                <AnimatePresence>
                  {showPreviousQA && qaHistory.length > 0 && (
                    <Motion.div
                      className="interview-previous-qa-content"
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.3 }}
                    >
                      {qaHistory.map((qa, index) => (
                        <div key={index} className="interview-previous-qa-card">
                          <div className="interview-previous-qa-header">
                            <h4>问题 {qa.round}</h4>
                            <span
                              className="interview-previous-qa-score"
                              style={{ color: getScoreColor(qa.evaluation.score) }}
                            >
                              {qa.evaluation.score.toFixed(0)} 分
                            </span>
                          </div>
                          <div className="interview-previous-qa-question">
                            <strong>问题：</strong>
                            <p>{qa.question}</p>
                          </div>
                          <div className="interview-previous-qa-answer">
                            <strong>您的回答：</strong>
                            <p>{qa.answer}</p>
                          </div>
                          <div className="interview-previous-qa-evaluation">
                            <div className="interview-previous-qa-feedback">
                              <strong>评价：</strong>
                              <p>{qa.evaluation.feedback}</p>
                            </div>
                            <div className="interview-previous-qa-suggestions">
                              <strong>建议：</strong>
                              <p>{qa.evaluation.suggestions}</p>
                            </div>
                          </div>
                        </div>
                      ))}
                    </Motion.div>
                  )}
                </AnimatePresence>

                <div className="interview-question-section">
                  <h3 className="interview-question-title">
                    问题 {currentQuestion.round || progress.current}
                  </h3>
                  <p className="interview-question-text">{currentQuestion.question}</p>

                  {currentQuestion.hints && (
                    <div className="interview-hints">
                      <span className="interview-hints-icon">💡</span>
                      <span>{currentQuestion.hints}</span>
                    </div>
                  )}
                </div>

                <div className="interview-answer-section">
                  <label className="interview-answer-label">您的回答：</label>
                  <textarea
                    className="interview-answer-textarea"
                    value={userAnswer}
                    onChange={(e) => {
                      setUserAnswer(e.target.value)
                      if (answerError) {
                        setAnswerError('')
                      }
                    }}
                    placeholder="请输入您的回答（建议尽量完整表达）..."
                    rows={6}
                    disabled={loading}
                  />
                  {answerError && (
                    <p className="interview-answer-error">{answerError}</p>
                  )}
                  <div className="interview-answer-footer">
                    <span className="interview-answer-count">
                      {userAnswer.length} 字
                    </span>
                    <button
                      className="interview-submit-btn"
                      onClick={submitAnswer}
                      disabled={loading || !userAnswer.trim()}
                    >
                      {loading ? '提交中...' : '提交答案'}
                    </button>
                  </div>
                </div>
              </motion.div>
            )}

            {/* 评分阶段 */}
            {status === 'evaluating' && (
              <motion.div
                key="evaluating"
                className="interview-state interview-evaluating"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                transition={{ duration: 0.3 }}
              >
                {!evaluation ? (
                  // 正在评分中
                  (<div className="interview-evaluating-loading">
                    <div className="interview-loading-spinner"></div>
                    <p className="interview-evaluating-text">🤔 正在评分中...</p>
                    <p className="interview-loading-hint">正在分析您的答案并给出评价</p>
                  </div>)
                ) : (
                  // 显示评分结果
                  (<div className="interview-evaluation-section">
                    <div className="interview-score-circle" style={{
                      borderColor: getScoreColor(evaluation.score)
                    }}>
                      <div className="interview-score-number" style={{
                        color: getScoreColor(evaluation.score)
                      }}>
                        {evaluation.score.toFixed(0)}
                      </div>
                      <div className="interview-score-label">分</div>
                    </div>
                    <div className="interview-feedback">
                      <h4>📊 评价反馈</h4>
                      <p>{evaluation.feedback}</p>
                    </div>
                    <div className="interview-suggestions">
                      <h4>💡 改进建议</h4>
                      <p>{evaluation.suggestions}</p>
                    </div>
                    <div className="interview-keyword-coverage">
                      <span>关键词覆盖率：</span>
                      <div className="interview-coverage-bar">
                        <div
                          className="interview-coverage-fill"
                          style={{
                            width: `${evaluation.keyword_coverage * 100}%`,
                            backgroundColor: getScoreColor(evaluation.keyword_coverage * 100)
                          }}
                        ></div>
                      </div>
                      <span>{(evaluation.keyword_coverage * 100).toFixed(0)}%</span>
                    </div>
                    <div className="interview-evaluation-actions">
                      {pendingTransition?.type === 'next' && (
                        <button type="button" className="interview-next-btn" onClick={proceedAfterEvaluation}>
                          继续下一题
                        </button>
                      )}
                      {pendingTransition?.type === 'completed' && (
                        <button type="button" className="interview-next-btn" onClick={proceedAfterEvaluation}>
                          查看总结
                        </button>
                      )}
                    </div>
                  </div>)
                )}
              </motion.div>
            )}

            {/* 完成阶段 */}
            {status === 'completed' && summary && (
              <motion.div
                key="completed"
                className="interview-state interview-completed"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                transition={{ duration: 0.3 }}
              >
                <div className="interview-summary-section">
                  <h3 className="interview-summary-title">🎉 面试完成！</h3>

                  <div className="interview-summary-stats">
                    <div className="interview-stat-item">
                      <div className="interview-stat-number">{summary.total_rounds}</div>
                      <div className="interview-stat-label">总轮次</div>
                    </div>
                    <div className="interview-stat-item">
                      <div
                        className="interview-stat-number"
                        style={{ color: getScoreColor(summary.average_score) }}
                      >
                        {summary.average_score.toFixed(1)}
                      </div>
                      <div className="interview-stat-label">平均分</div>
                    </div>
                  </div>

                  <div className="interview-summary-content">
                    <div className="interview-summary-block">
                      <h4>✨ 优势领域</h4>
                      <ul>
                        {summary.strengths.map((strength, index) => (
                          <li key={index}>{strength}</li>
                        ))}
                      </ul>
                    </div>

                    <div className="interview-summary-block">
                      <h4>📈 需要改进</h4>
                      <ul>
                        {summary.weaknesses.map((weakness, index) => (
                          <li key={index}>{weakness}</li>
                        ))}
                      </ul>
                    </div>

                    <div className="interview-summary-block">
                      <h4>📝 总体评价</h4>
                      <p>{summary.overall_assessment}</p>
                    </div>

                    <div className="interview-summary-block">
                      <h4>🎯 学习建议</h4>
                      <p>{summary.recommendations}</p>
                    </div>
                  </div>

                  <button className="interview-done-btn" onClick={() => onClose(true)}>
                    完成
                  </button>
                </div>
              </motion.div>
            )}

            {/* 错误状态 */}
            {status === 'error' && (
              <motion.div
                key="error"
                className="interview-state interview-error"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
              >
                <div className="interview-error-icon">❌</div>
                <p className="interview-error-message">{error}</p>
                <button className="interview-retry-btn" onClick={() => onClose(true)}>
                  关闭
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </motion.div>
    </div>
  );
}

export default InterviewDialog

