import { motion as Motion } from 'framer-motion'
import { useState, useEffect, useRef } from 'react'
import './AIExplanationDialog.css'
import MarkdownRenderer from './MarkdownRenderer'
import { apiRequest } from '../utils/api'

function AIExplanationDialog({ node, onClose, learningTopic }) {
  const [aiExplanation, setAiExplanation] = useState(null)
  const [isLoadingExplanation, setIsLoadingExplanation] = useState(false)
  const [explanationError, setExplanationError] = useState(null)
  const [userQuestion, setUserQuestion] = useState('')
  const [showInput, setShowInput] = useState(true)

  // 用于取消API请求
  const abortControllerRef = useRef(null)

  const handleGetAiExplanation = async (customQuestion = '') => {
    // 创建新的 AbortController
    abortControllerRef.current = new AbortController()

    setIsLoadingExplanation(true)
    setExplanationError(null)
    setShowInput(false)

    try {
      const response = await apiRequest('/api/explain-node', {
        method: 'POST',
        body: JSON.stringify({
          node_title: node.title,
          node_description: node.description,
          topic: learningTopic || '学习主题',
          custom_question: customQuestion || undefined,
        }),
        signal: abortControllerRef.current.signal,
      })

      const data = await response.json()

      if (response.ok && data.success) {
        setAiExplanation(data.data)
      } else {
        setExplanationError(data.message || '获取AI解释失败')
      }
    } catch (error) {
      if (error.name === 'AbortError') {
        return
      }
      setExplanationError('网络错误，请稍后重试')
    } finally {
      setIsLoadingExplanation(false)
      abortControllerRef.current = null
    }
  }

  // 组件卸载时取消API请求
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [])

  // 处理关闭时取消请求
  const handleClose = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    onClose()
  }

  // 直接获取AI解释
  const handleDirectExplain = () => {
    handleGetAiExplanation('')
  }

  // 带自定义问题获取AI解释
  const handleSubmitQuestion = () => {
    if (userQuestion.trim()) {
      handleGetAiExplanation(userQuestion.trim())
    } else {
      handleDirectExplain()
    }
  }

  return (
    <Motion.div
      className="ai-dialog"
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ type: 'tween', duration: 0.3 }}
    >
      <div className="ai-dialog-header">
        <div>
          <h2 className="ai-dialog-title">AI深入讲解</h2>
          <p className="ai-dialog-subtitle">{node.title}</p>
        </div>
        <button onClick={handleClose} className="ai-dialog-close-button">
          ✕
        </button>
      </div>

      <div className="ai-dialog-content">
        {/* 用户输入区域 */}
        {showInput && !isLoadingExplanation && !aiExplanation && (
          <Motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="ai-input-section"
          >
            <p className="ai-input-hint">
              您可以添加想要了解的具体问题，或直接让AI为您讲解
            </p>
            <textarea
              className="ai-question-input"
              placeholder="例如：这个知识点在实际工作中如何应用？有哪些常见的坑？"
              value={userQuestion}
              onChange={(e) => setUserQuestion(e.target.value)}
              rows={4}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  handleSubmitQuestion()
                }
              }}
            />
            <div className="ai-input-buttons">
              <button
                className="ai-direct-button"
                onClick={handleDirectExplain}
              >
                直接解释
              </button>
              <button
                className="ai-submit-button"
                onClick={handleSubmitQuestion}
              >
                带问题解释
              </button>
            </div>
          </Motion.div>
        )}

        {/* 加载状态 */}
        {isLoadingExplanation && (
          <div className="ai-loading">
            <div className="spinner"></div>
            <p>AI正在思考中...</p>
            <button onClick={handleClose} className="cancel-loading-button">
              取消
            </button>
          </div>
        )}

        {explanationError && (
          <div className="ai-error">
            <p className="error-message">{explanationError}</p>
            <button onClick={handleGetAiExplanation} className="retry-button">
              重试
            </button>
          </div>
        )}

        {aiExplanation && (
          <Motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="ai-explanation-content"
          >
            <div className="ai-section">
              <h3>原理理解</h3>
              <MarkdownRenderer>{aiExplanation.principle_explanation}</MarkdownRenderer>
            </div>

            <div className="ai-section">
              <h3>通俗理解</h3>
              <MarkdownRenderer>{aiExplanation.simple_explanation}</MarkdownRenderer>
            </div>

            <div className="ai-section">
              <h3>例子</h3>
              {Array.isArray(aiExplanation.real_world_examples) ? (
                <ul className="example-list">
                  {aiExplanation.real_world_examples.map((example, index) => (
                    <li key={index}>
                      <MarkdownRenderer>{example}</MarkdownRenderer>
                    </li>
                  ))}
                </ul>
              ) : (
                <MarkdownRenderer>{aiExplanation.real_world_examples}</MarkdownRenderer>
              )}
            </div>

            <div className="ai-bottom-buttons">
              <button
                onClick={() => {
                  setAiExplanation(null)
                  setShowInput(true)
                  setUserQuestion('')
                }}
                className="back-button"
              >
                ← 重新提问
              </button>
              <button
                onClick={() => {
                  setAiExplanation(null)
                  handleGetAiExplanation(userQuestion)
                }}
                className="refresh-button"
              >
                重新生成
              </button>
            </div>
          </Motion.div>
        )}
      </div>
    </Motion.div>
  )
}

export default AIExplanationDialog

