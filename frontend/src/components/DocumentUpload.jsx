import { useState, useRef, useEffect } from 'react'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import { apiRequest, getAccessToken } from '../utils/api'
import { TASK_POLLING_INTERVAL_MS, MAX_UPLOAD_FILE_SIZE_BYTES, MAX_UPLOAD_FILE_SIZE_MB } from '../utils/config'
import { fetchDocumentStatus, invalidateDocumentStatusCache } from '../utils/documentStatusCache'
import { parseTaskErrorResponse, normalizeTaskErrorMessage } from '../utils/taskError'
import MiniNeuralNetwork from './MiniNeuralNetwork'
import './DocumentUpload.css'

import RotatingLoadingText from './RotatingLoadingText'

function DocumentUpload({ onUploadSuccess, onBack }) {
  const [uploadMode, setUploadMode] = useState('file') // 'file' or 'text' or 'url'
  const [textContent, setTextContent] = useState('')
  const [urlContent, setUrlContent] = useState('')
  const [filename] = useState('')
  const [isUploading, setIsUploading] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [error, setError] = useState(null)
  const fileInputRef = useRef(null)

  // 新增：处理进度状态
  const [processingStatus, setProcessingStatus] = useState(null) // pending | processing | completed | failed
  const [, setProcessingProgress] = useState(0)
  const [, setCurrentDocumentId] = useState(null)
  const [, setProcessingStage] = useState('')
  const [visualProgress, setVisualProgress] = useState(0)
  const pollingIntervalRef = useRef(null)

  // 模拟进度条效果
  useEffect(() => {
    let interval
    if (isUploading && (processingStatus === 'pending' || processingStatus === 'processing') && visualProgress < 95) {
      interval = setInterval(() => {
        setVisualProgress(prev => {
          if (prev >= 95) return prev
          // 越往后越慢
          const increment = prev < 30 ? 5 : prev < 60 ? 2 : prev < 80 ? 1 : 0.5
          return Math.min(prev + increment, 95)
        })
      }, 200)
    }
    return () => clearInterval(interval)
  }, [isUploading, processingStatus, visualProgress])

  // 轮询查询处理状态
  const pollProcessingStatus = async (documentId) => {
    if (!documentId) {
      return true
    }

    const token = getAccessToken()
    if (!token) {
      setIsUploading(false)
      setError('登录状态已失效，请重新登录后再试')
      return true
    }

    try {
      const statusResult = await fetchDocumentStatus(documentId, { useCache: true })

      if (!statusResult.ok) {
        if (statusResult.status === 404) {
          clearInterval(pollingIntervalRef.current)
          setError('文档处理失败，已自动清理')
          setIsUploading(false)
          return true
        }
        return false
      }

      const payload = statusResult.data
      if (!payload?.success || !payload.data) {
        return false
      }

      const {
        processing_status,
        processing_progress = 0,
      } = payload.data

      setProcessingStatus(processing_status)
      setProcessingProgress(processing_progress) // 保持记录真实进度，但不直接用于显示

      // 统一显示文本
      if (processing_status !== 'completed' && processing_status !== 'failed') {
        setProcessingStage('正在解析文档内容...')
      } else if (processing_status === 'completed') {
        setProcessingStage('处理完成！')
        setVisualProgress(100)
      }

      if (processing_status === 'completed') {
        clearInterval(pollingIntervalRef.current)
        setTimeout(() => {
          onUploadSuccess({
            ...payload.data,
          })
        }, 500)
        return true
      }

      if (processing_status === 'failed') {
        clearInterval(pollingIntervalRef.current)
        setError('文档处理失败，请重试')
        setIsUploading(false)
        return true
      }

      return false
    } catch (err) {
      const message = err instanceof Error ? err.message : ''
      if (message.includes('未授权') || message.includes('登录')) {
        clearInterval(pollingIntervalRef.current)
        setError('登录状态已过期，请重新登录后再试')
        setIsUploading(false)
        return true
      }
      return false
    }
  }

  // 清理轮询
  useEffect(() => {
    return () => {
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current)
      }
    }
  }, [])

  const handleFileSelect = async (file) => {
    if (!file) return

    // 支持的文件格式
    const supportedExtensions = ['.pdf', '.docx', '.doc', '.txt', '.md', '.tex', '.latex', '.html', '.htm', '.epub', '.pptx', '.ppt']
    const fileExtension = '.' + file.name.split('.').pop().toLowerCase()

    if (!supportedExtensions.includes(fileExtension)) {
      setError(`不支持的文件格式。支持：PDF、Word、Markdown、TXT、HTML、EPUB、PowerPoint`)
      return
    }

    if (file.size > MAX_UPLOAD_FILE_SIZE_BYTES) {
      setError(`文件过大：当前 ${Math.ceil(file.size / 1024 / 1024)}MB，最大支持 ${MAX_UPLOAD_FILE_SIZE_MB}MB`) 
      return
    }

    setIsUploading(true)
    setError(null)
    setIsUploading(true)
    setError(null)
    setProcessingProgress(0)
    setVisualProgress(0)
    setProcessingStage('正在解析文档内容...')

    try {
      const formData = new FormData()
      formData.append('file', file)

      const response = await apiRequest('/api/document/upload', {
        method: 'POST',
        body: formData
      })

      if (response.status === 413) {
        throw new Error(`文件过大，最大支持 ${MAX_UPLOAD_FILE_SIZE_MB}MB，请压缩后重试`)
      }

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '上传失败'))
      }

      const data = await response.json()

      if (data.success) {
        const { document_id, processing_status } = data.data
        setCurrentDocumentId(document_id)
        invalidateDocumentStatusCache(document_id)
        setProcessingStatus(processing_status)

        // 如果状态是 pending 或 processing，开始轮询
        if (processing_status === 'pending' || processing_status === 'processing') {
          setProcessingStage('正在解析文档内容...')

          // 立即查询一次
          void pollProcessingStatus(document_id)

          // 每2秒轮询一次
          pollingIntervalRef.current = setInterval(() => {
            void pollProcessingStatus(document_id)
          }, TASK_POLLING_INTERVAL_MS)
        } else if (processing_status === 'completed') {
          // 如果已经完成（复用向量），直接跳转
          setProcessingProgress(100)
          setVisualProgress(100)
          setProcessingStage('处理完成！')
          setTimeout(() => {
            onUploadSuccess(data.data)
          }, 500)
        }
      } else {
        throw new Error(data.message || '上传失败')
      }
    } catch (err) {
      let message = err instanceof Error ? err.message : '上传失败，请重试'
      if (message.includes('未授权') || message.includes('登录')) {
        message = '登录状态已过期，请重新登录后再试'
      } else {
        message = normalizeTaskErrorMessage(message, '上传失败，请重试')
      }
      setError(message || '上传失败，请重试')
      setIsUploading(false)
    }
  }

  const handleUrlSubmit = async () => {
    if (!urlContent.trim()) {
      setError('请输入网页地址')
      return
    }

    if (!urlContent.match(/^https?:\/\/.+/)) {
      setError('请输入有效的 URL（以 http:// 或 https:// 开头）')
      return
    }

    setIsUploading(true)
    setError(null)
    setProcessingProgress(0)
    setVisualProgress(0)
    setProcessingStage('正在解析文档内容...')

    try {
      const response = await apiRequest('/api/document/url', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ url: urlContent })
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '抓取失败'))
      }

      const data = await response.json()

      if (data.success) {
        const { document_id, processing_status } = data.data
        setCurrentDocumentId(document_id)
        invalidateDocumentStatusCache(document_id)
        setProcessingStatus(processing_status)

        // 如果状态是 pending 或 processing，开始轮询
        if (processing_status === 'pending' || processing_status === 'processing') {
          setProcessingStage('正在解析文档内容...')

          // 立即查询一次
          void pollProcessingStatus(document_id)

          // 每2秒轮询一次
          pollingIntervalRef.current = setInterval(() => {
            void pollProcessingStatus(document_id)
          }, TASK_POLLING_INTERVAL_MS)
        } else if (processing_status === 'completed') {
          // 如果已经完成，直接跳转
          setProcessingProgress(100)
          setVisualProgress(100)
          setProcessingStage('处理完成！')
          setTimeout(() => {
            onUploadSuccess(data.data)
          }, 500)
        }
      } else {
        throw new Error(data.message || '抓取失败')
      }
    } catch (err) {
      let message = err instanceof Error ? err.message : '抓取失败，请重试'
      if (message.includes('未授权') || message.includes('登录')) {
        message = '登录状态已过期，请重新登录后再试'
      } else {
        message = normalizeTaskErrorMessage(message, '抓取失败，请重试')
      }
      setError(message)
      setIsUploading(false)
    }
  }

  const handleTextSubmit = async () => {
    if (!textContent.trim()) {
      setError('请输入文本内容')
      return
    }

    setIsUploading(true)
    setError(null)
    setError(null)
    setProcessingProgress(0)
    setVisualProgress(0)
    setProcessingStage('正在解析文档内容...')

    try {
      const formData = new FormData()
      formData.append('text', textContent)
      formData.append('filename', filename || '粘贴的文本.txt')

      const response = await apiRequest('/api/document/upload', {
        method: 'POST',
        body: formData
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '提交失败'))
      }

      const data = await response.json()

      if (data.success) {
        const { document_id, processing_status } = data.data
        setCurrentDocumentId(document_id)
        invalidateDocumentStatusCache(document_id)
        setProcessingStatus(processing_status)

        // 如果状态是 pending 或 processing，开始轮询
        if (processing_status === 'pending' || processing_status === 'processing') {
          setProcessingStage('正在解析文档内容...')

          // 立即查询一次
          void pollProcessingStatus(document_id)

          // 每2秒轮询一次
          pollingIntervalRef.current = setInterval(() => {
            void pollProcessingStatus(document_id)
          }, TASK_POLLING_INTERVAL_MS)
        } else if (processing_status === 'completed') {
          // 如果已经完成，直接跳转
          setProcessingProgress(100)
          setVisualProgress(100)
          setProcessingStage('处理完成！')
          setTimeout(() => {
            onUploadSuccess(data.data)
          }, 500)
        }
      } else {
        throw new Error(data.message || '提交失败')
      }
    } catch (err) {
      let message = err instanceof Error ? err.message : '提交失败，请重试'
      if (message.includes('未授权') || message.includes('登录')) {
        message = '登录状态已过期，请重新登录后再试'
      } else {
        message = normalizeTaskErrorMessage(message, '提交失败，请重试')
      }
      setError(message || '提交失败，请重试')
      setIsUploading(false)
    }
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = () => {
    setIsDragging(false)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setIsDragging(false)

    const file = e.dataTransfer.files[0]
    if (file) {
      handleFileSelect(file)
    }
  }

  return (
    <Motion.div
      className="document-upload-container"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      {/* 顶部导航 */}
      <div className="document-upload-header">
        <button className="back-button" onClick={onBack}>
          ← 返回
        </button>
      </div>

      {/* 模式切换 */}
      <div className="upload-mode-switcher">
        <button
          className={`mode-tab ${uploadMode === 'file' ? 'active' : ''}`}
          onClick={() => setUploadMode('file')}
        >
          上传文件
        </button>
        <button
          className={`mode-tab ${uploadMode === 'url' ? 'active' : ''}`}
          onClick={() => setUploadMode('url')}
        >
          网页链接
        </button>
        <button
          className={`mode-tab ${uploadMode === 'text' ? 'active' : ''}`}
          onClick={() => setUploadMode('text')}
        >
          粘贴文本
        </button>
      </div>

      {/* 上传区域 */}
      <div className="upload-content">
        <AnimatePresence mode="wait">
          {uploadMode === 'file' ? (
            <Motion.div
              key="file"
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 20 }}
              className="upload-section"
            >
              <div
                className={`file-drop-zone ${isDragging ? 'dragging' : ''}`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
              >
                <div className="drop-zone-icon">📄</div>
                <p className="drop-zone-text">
                  拖拽文件到这里
                  <br />
                  或点击选择文件
                </p>
                <p className="drop-zone-hint">支持 PDF、Word、Markdown、TXT、HTML、EPUB、PPT 等格式</p>
                <p className="drop-zone-hint">单文件最大 {MAX_UPLOAD_FILE_SIZE_MB}MB</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.docx,.doc,.txt,.md,.tex,.latex,.html,.htm,.epub,.pptx,.ppt"
                  onChange={(e) => handleFileSelect(e.target.files[0])}
                  style={{ display: 'none' }}
                />
              </div>
            </Motion.div>
          ) : uploadMode === 'url' ? (
            <Motion.div
              key="url"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="upload-section"
            >
              <div className="url-input-container">
                <input
                  type="text"
                  placeholder="请输入网页链接 (例如: https://example.com/article)"
                  value={urlContent}
                  onChange={(e) => setUrlContent(e.target.value)}
                  className="url-content-input"
                />
                <p className="input-hint">将自动抓取网页正文并移除广告干扰</p>
              </div>
              <button
                className="submit-button"
                onClick={handleUrlSubmit}
                disabled={isUploading || !urlContent.trim()}
              >
                {isUploading ? '处理中...' : '抓取并生成'}
              </button>
            </Motion.div>
          ) : (
            <Motion.div
              key="text"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="upload-section"
            >
              <textarea
                placeholder="在此粘贴或输入文本内容..."
                value={textContent}
                onChange={(e) => setTextContent(e.target.value)}
                className="text-content-input"
                rows={15}
              />
              <button
                className="submit-button"
                onClick={handleTextSubmit}
                disabled={isUploading || !textContent.trim()}
              >
                {isUploading ? '处理中...' : '生成学习路径'}
              </button>
            </Motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* 错误提示 */}
      <AnimatePresence>
        {error && (
          <Motion.div
            className="error-message"
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
          >
            ⚠️ {error}
          </Motion.div>
        )}
      </AnimatePresence>

      {/* 加载状态 - 简洁设计 */}
      <AnimatePresence>
        {isUploading && (
          <Motion.div
            className="uploading-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div className="uploading-content">
              <div className="neural-spinner-wrapper">
                <MiniNeuralNetwork width={120} height={120} />
              </div>

              <RotatingLoadingText className="uploading-text" style={{ marginBottom: '24px' }} />

              {/* 进度条 */}
              <div className="progress-container">
                <div className="progress-bar-wrapper">
                  <Motion.div
                    className="progress-bar"
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.round(visualProgress)}%` }}
                    transition={{ duration: 0.3 }}
                  />
                </div>
                <div className="progress-text">{Math.round(visualProgress)}%</div>
              </div>

              {/* 取消按钮 */}
              {visualProgress < 100 && (
                <button
                  className="cancel-button"
                  onClick={() => {
                    if (pollingIntervalRef.current) {
                      clearInterval(pollingIntervalRef.current)
                    }
                    setIsUploading(false)
                    setProcessingProgress(0)
                    setVisualProgress(0)
                    setProcessingStatus(null)
                  }}
                >
                  取消
                </button>
              )}
            </div>
          </Motion.div>
        )}
      </AnimatePresence>
    </Motion.div>
  )
}

export default DocumentUpload

