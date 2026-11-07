import { useState } from 'react'
import PropTypes from 'prop-types'
import { FaArrowRight, FaArrowsRotate, FaCheck, FaCopy } from 'react-icons/fa6'
import { apiRequest } from '../utils/api'
import { parseTaskErrorResponse, normalizeTaskErrorMessage } from '../utils/taskError'
import './PolishPage.css'

const MAX_CHINESE_CHARS = 500
const MAX_ENGLISH_WORDS = 500
const NEAR_LIMIT_THRESHOLD = 450

const countChineseChars = (text) => (text.match(/[\u4E00-\u9FFF]/g) || []).length
const countEnglishWords = (text) => (text.match(/\b[a-zA-Z]+(?:'[a-zA-Z]+)?\b/g) || []).length

const getLimitError = (label, text) => {
  const chineseChars = countChineseChars(text)
  const englishWords = countEnglishWords(text)
  if (chineseChars > MAX_CHINESE_CHARS || englishWords > MAX_ENGLISH_WORDS) {
    return `${label}最多支持 ${MAX_CHINESE_CHARS} 个中文字符或 ${MAX_ENGLISH_WORDS} 个英文单词（当前：中文 ${chineseChars}，英文 ${englishWords}）`
  }
  return ''
}

function PolishPage() {
  const taskType = 'mimic'
  const mode = 'academic'
  const [sourceText, setSourceText] = useState('')
  const [referenceText, setReferenceText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [copySuccess, setCopySuccess] = useState(false)

  const sourceChineseCount = countChineseChars(sourceText)
  const sourceEnglishCount = countEnglishWords(sourceText)
  const referenceChineseCount = countChineseChars(referenceText)
  const referenceEnglishCount = countEnglishWords(referenceText)
  const taskMeta = {
    actionName: '仿写',
    sourceLabel: '待仿写文本',
    sourceHint: '请输入需要仿写改写的原文',
    resultLabel: '仿写结果',
  }

  const handlePolish = async () => {
    if (!sourceText.trim()) {
      setError(`请先输入${taskMeta.sourceLabel}`)
      return
    }

    if (!referenceText.trim()) {
      setError('仿写模式需要参考文本')
      return
    }

    const sourceLimitError = getLimitError(taskMeta.sourceLabel, sourceText)
    if (sourceLimitError) {
      setError(sourceLimitError)
      return
    }

    const referenceLimitError = getLimitError('参考文本', referenceText)
    if (referenceLimitError) {
      setError(referenceLimitError)
      return
    }

    setLoading(true)
    setError('')

    try {
      const response = await apiRequest('/api/polish/rewrite', {
        method: 'POST',
        body: JSON.stringify({
          source_text: sourceText,
          reference_text: referenceText,
          mode,
          task_type: taskType,
        }),
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, `${taskMeta.actionName}失败，请稍后重试`))
      }

      const data = await response.json()
      if (!data.success) {
        throw new Error(data.detail || data.details || data.message || `${taskMeta.actionName}失败，请稍后重试`)
      }

      setResult(data.data)
    } catch (err) {
      setError(normalizeTaskErrorMessage(err.message, `${taskMeta.actionName}失败，请稍后重试`))
    } finally {
      setLoading(false)
    }
  }

  const handleCopyResult = async () => {
    if (!result?.rewritten_text) return

    try {
      await navigator.clipboard.writeText(result.rewritten_text)
      setCopySuccess(true)
      window.setTimeout(() => setCopySuccess(false), 1200)
    } catch {
      setError('复制失败，请手动复制')
    }
  }

  const handleRegenerate = () => {
    if (!sourceText.trim() || loading) return
    handlePolish()
  }

  const handleSourceTextChange = (value) => {
    const limitError = getLimitError(taskMeta.sourceLabel, value)
    if (limitError) {
      setError(limitError)
      return
    }
    setSourceText(value)
    setError(prev => (prev.includes(`最多支持 ${MAX_CHINESE_CHARS} 个中文字符或 ${MAX_ENGLISH_WORDS} 个英文单词`) ? '' : prev))
  }

  const handleReferenceTextChange = (value) => {
    const limitError = getLimitError('参考文本', value)
    if (limitError) {
      setError(limitError)
      return
    }
    setReferenceText(value)
    setError(prev => (prev.includes(`最多支持 ${MAX_CHINESE_CHARS} 个中文字符或 ${MAX_ENGLISH_WORDS} 个英文单词`) ? '' : prev))
  }

  return (
    <div className="polish-page-root">
      <div className="polish-shell">
        <div className="polish-layout">
          <section className="polish-card polish-form-card">
            <div className="polish-input-stage">
              {!sourceText.trim() && (
                <div className="polish-content-hint">
                  <p className="polish-content-hint-title">{taskMeta.sourceLabel}</p>
                  <p className="polish-content-hint-desc">{taskMeta.sourceHint}</p>
                </div>
              )}
              <textarea
                id="sourceText"
                className="polish-textarea polish-textarea-main"
                placeholder=""
                value={sourceText}
                onChange={(e) => handleSourceTextChange(e.target.value)}
              />
            </div>

            <div className="polish-reference-stage polish-input-stage">
              {!referenceText.trim() && (
                <div className="polish-content-hint polish-content-hint-compact">
                  <p className="polish-content-hint-title polish-content-hint-title-compact">参考文本</p>
                  <p className="polish-content-hint-desc polish-content-hint-desc-compact">请输入目标风格参考文本，系统将迁移风格规则</p>
                </div>
              )}
              <textarea
                id="referenceText"
                className="polish-textarea polish-textarea-reference"
                placeholder=""
                value={referenceText}
                onChange={(e) => handleReferenceTextChange(e.target.value)}
              />
            </div>

            {error && <p className="polish-error">{error}</p>}

            <div className="polish-form-footer">
              <div className="polish-footer-counters">
                <p className={`polish-counter ${(sourceChineseCount > NEAR_LIMIT_THRESHOLD || sourceEnglishCount > NEAR_LIMIT_THRESHOLD) ? 'near-limit' : ''}`}>
                  {taskMeta.sourceLabel}：中文 {sourceChineseCount}/{MAX_CHINESE_CHARS} · 英文 {sourceEnglishCount}/{MAX_ENGLISH_WORDS}
                </p>
                <p className={`polish-counter ${(referenceChineseCount > NEAR_LIMIT_THRESHOLD || referenceEnglishCount > NEAR_LIMIT_THRESHOLD) ? 'near-limit' : ''}`}>
                  参考文本：中文 {referenceChineseCount}/{MAX_CHINESE_CHARS} · 英文 {referenceEnglishCount}/{MAX_ENGLISH_WORDS}
                </p>
              </div>

              <button
                type="button"
                className="polish-submit"
                onClick={handlePolish}
                disabled={loading}
                aria-label={`开始${taskMeta.actionName}`}
                title={`开始${taskMeta.actionName}`}
              >
                {loading ? (
                  <div className="polish-loading-dots">
                    <span>.</span><span>.</span><span>.</span>
                  </div>
                ) : (
                  <FaArrowRight className="polish-icon" />
                )}
              </button>
            </div>
          </section>

          <section className="polish-card polish-result-card">
            <div className="polish-result-stage">
              {!result ? (
                <div className="polish-content-hint">
                  <p className="polish-content-hint-title">{taskMeta.resultLabel}</p>
                  <p className="polish-content-hint-desc">结果将显示在这里</p>
                </div>
              ) : (
                <>
                  <div className="polish-output-text">
                    {result.rewritten_text || ''}
                  </div>
                </>
              )}
            </div>

            <div className="polish-result-footer-actions">
              <button
                type="button"
                className="polish-action-text-btn"
                onClick={handleCopyResult}
                disabled={!result?.rewritten_text}
                aria-label={copySuccess ? '已复制' : '复制'}
                title={copySuccess ? '已复制' : '复制'}
              >
                {copySuccess ? <FaCheck className="polish-icon" /> : <FaCopy className="polish-icon" />}
              </button>
              <button
                type="button"
                className="polish-action-text-btn"
                onClick={handleRegenerate}
                disabled={!sourceText.trim() || loading}
                aria-label="重写"
                title="重写"
              >
                <FaArrowsRotate className="polish-icon" />
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}

export default PolishPage
