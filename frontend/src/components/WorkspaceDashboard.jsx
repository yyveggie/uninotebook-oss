import { useState, useRef, useMemo } from 'react'
import { motion as Motion } from 'framer-motion'
import HistoryCards from './HistoryCards'
import { useNavigate } from 'react-router-dom'
import useAuthStore from '../stores/authStore'
import useRuntimeStore, { useCapability } from '../stores/runtimeStore'
import { MAX_UPLOAD_FILE_SIZE_BYTES, MAX_UPLOAD_FILE_SIZE_MB } from '../utils/config'
import './WorkspaceDashboard.css'
import './PPTGeneratorModal.css'
import useFirstEntrance from '../hooks/useFirstEntrance'

function WorkspaceDashboard({
  onDocumentUpload,
  onDocumentHistorySelect,
  refreshKey
}) {
  const user = useAuthStore(s => s.user)
  const setShowAuthModal = useAuthStore(s => s.setShowAuthModal)
  const navigate = useNavigate()
  const animateIn = useFirstEntrance()

  // 文档入库需要向量化能力：缺 Key 时提前拦下并给出指引，
  // 而不是让用户传完文件才撞一个后端报错。
  //
  // useCapability 订阅能力清单本身：清单加载 / 保存 Key 后会重渲染。
  // （以前只选出 isAvailable / getBlockReason 函数，引用不变，清单到达后界面不会更新）
  const isLocalMode = useRuntimeStore(s => s.deployMode === 'local')
  const ingestCapability = useCapability('document_ingest')
  const chatCapability = useCapability('chat')
  const canIngest = ingestCapability.available
  const ingestBlockReason = ingestCapability.reason
  // 首次使用时的配置引导：文档入库与对话模型任一缺配置都提示，并给出前往设置的入口
  const setupReasons = [ingestBlockReason, chatCapability.reason].filter(Boolean)
  const canGoToSettings = isLocalMode && setupReasons.some((reason) => reason.type === 'key')
  const displayName = useMemo(() => {
    if (!user) return '学习者'
    return user.full_name || user.username
  }, [user])

  const [inputValue, setInputValue] = useState('')
  const [pdfFile, setPdfFile] = useState(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isUrlMode, setIsUrlMode] = useState(false)
  const [isTextareaFocused, setIsTextareaFocused] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const fileInputRef = useRef(null)

  const validateFile = (file) => {
    const supportedExtensions = ['.pdf', '.docx', '.doc', '.txt', '.md', '.html', '.htm', '.epub', '.pptx', '.ppt']
    const fileName = (file?.name || '').toLowerCase()
    const hasValidExtension = supportedExtensions.some(ext => fileName.endsWith(ext))

    if (!hasValidExtension) {
      return '不支持的文件格式，请上传 PDF、Word、Markdown、TXT、HTML、EPUB、PPT 文件'
    }

    if (file.size > MAX_UPLOAD_FILE_SIZE_BYTES) {
      return `文件过大：当前 ${Math.ceil(file.size / 1024 / 1024)}MB，最大支持 ${MAX_UPLOAD_FILE_SIZE_MB}MB`
    }

    return ''
  }

  const openFilePicker = () => {
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
      fileInputRef.current.click()
    }
  }

  const shouldExpandTextarea = isTextareaFocused || !!inputValue.trim() || !!pdfFile || isUrlMode

  const handleDragOver = (e) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    setIsDragging(false)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setIsDragging(false)
    setIsUrlMode(false)
    setUploadError('')

    const files = e.dataTransfer.files
    if (files.length > 0) {
      const nextFile = files[0]
      const error = validateFile(nextFile)
      if (error) {
        setUploadError(error)
        setPdfFile(null)
        setInputValue('')
        return
      }

      setPdfFile(nextFile)
      setInputValue('')
    }
  }

  const handleFileSelect = (e) => {
    const file = e.target.files?.[0]
    if (file) {
      const error = validateFile(file)
      if (error) {
        setUploadError(error)
        setPdfFile(null)
        setInputValue('')
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
        return
      }

      setPdfFile(file)
      setInputValue('')
      setIsUrlMode(false)
      setUploadError('')
    }
  }

  const handleClearFile = (e) => {
    e?.stopPropagation()
    setPdfFile(null)
    setInputValue('')
    setUploadError('')
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleSubmit = () => {
    // 本地版免登录，不存在「未登录」
    if (!user && !isLocalMode) {
      setShowAuthModal(true)
      return
    }

    // 未配置向量化 Key 时，文档无法入库——提前拦下，别让用户白等
    if (!canIngest) {
      setUploadError(ingestBlockReason?.text || '文档解析所需的配置尚未就绪')
      return
    }

    if (pdfFile) {
      const error = validateFile(pdfFile)
      if (error) {
        setUploadError(error)
        setPdfFile(null)
        setInputValue('')
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
        return
      }
    }

    setUploadError('')

    if (isUrlMode && inputValue.trim()) {
      onDocumentUpload?.({ type: 'url', url: inputValue.trim() })
    } else if (pdfFile) {
      onDocumentUpload?.({ type: 'file', file: pdfFile })
    } else if (inputValue.trim()) {
      onDocumentUpload?.({ type: 'text', content: inputValue.trim() })
    }
  }

  return (
    <div className="workspace-dashboard">
      <Motion.header
        className="workspace-header"
        initial={animateIn ? { opacity: 0, y: -12 } : false}
        animate={{ opacity: 1, y: 0 }}
      >
        <div className="header-texts">
          <h1>欢迎回来，{displayName}</h1>
        </div>
      </Motion.header>

      <Motion.section
        className="workspace-main"
        initial={animateIn ? { opacity: 0, y: 24 } : false}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
      >
        <div
          className={`upload-card ${isDragging ? 'dragging' : ''} ${pdfFile ? 'has-file' : ''} ${isUrlMode ? 'url-mode' : ''}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => !pdfFile && !inputValue && !isUrlMode && openFilePicker()}
        >
          <input
            type="file"
            className="upload-file-input-hidden"
            ref={fileInputRef}
            accept=".pdf,.docx,.doc,.txt,.md,.html,.htm,.epub,.pptx,.ppt"
            onClick={(e) => {
              e.currentTarget.value = ''
            }}
            onChange={handleFileSelect}
          />

          <textarea
            className={`upload-textarea ${pdfFile ? 'file-selected' : ''} ${shouldExpandTextarea ? 'expanded' : 'collapsed'} ${isUrlMode ? 'url-input-mode' : ''}`}
            placeholder={isUrlMode ? "在此粘贴网页链接 (https://...)，但 UniNotebook 可能会遇到无法触达该网页所在的星球的问题。" : "在这里粘贴重点内容，或将 PDF、Word、PPT 等文件拖拽到此区域。"}
            value={pdfFile ? `📄 ${pdfFile.name} · ${(pdfFile.size / 1024).toFixed(1)} KB` : inputValue}
            onChange={(e) => {
              if (pdfFile) return
              setInputValue(e.target.value)
            }}
            onClick={(e) => e.stopPropagation()}
            onFocus={() => setIsTextareaFocused(true)}
            onBlur={() => setIsTextareaFocused(false)}
            readOnly={!!pdfFile}
            rows={1}
          />

          {pdfFile && (
            <button className="clear-upload-icon" onClick={handleClearFile} aria-label="清除文件">
              ×
            </button>
          )}

          {isUrlMode && inputValue && (
            <button className="clear-upload-icon" onClick={() => setInputValue('')} aria-label="清除链接">
              ×
            </button>
          )}
        </div>

        <div className="workspace-actions">
          <button
            className="action-secondary"
            onClick={() => {
              setIsUrlMode(false)
              openFilePicker()
            }}
          >
            📂 上传文档
          </button>

          <button
            className={`action-secondary ${isUrlMode ? 'active' : ''}`}
            onClick={() => {
              if (isUrlMode) {
                setIsUrlMode(false)
                setInputValue('')
              } else {
                setIsUrlMode(true)
                setPdfFile(null)
                setInputValue('')
              }
            }}
          >
            🔗 粘贴链接
          </button>

          <button
            className={`action-primary ${(!pdfFile && !inputValue.trim()) ? 'inactive' : ''}`}
            onClick={handleSubmit}
            aria-disabled={!pdfFile && !inputValue.trim()}
          >
            {(user || isLocalMode) ? '🔍 开始分析' : '登录后开始分析'}
          </button>

        </div>

        {setupReasons.length > 0 && (
          <div className="workspace-upload-notice" role="status">
            <span>
              {setupReasons.length > 1
                ? setupReasons.map((reason) => `${reason.label}：${reason.text}`).join('；')
                : setupReasons[0].text}
            </span>
            {canGoToSettings && (
              <button
                type="button"
                className="workspace-upload-notice-action"
                onClick={() => navigate('/settings')}
              >
                前往设置
              </button>
            )}
            {setupReasons.filter((reason) => reason.command).map((reason) => (
              <code key={reason.command}>{reason.command}</code>
            ))}
          </div>
        )}

        {uploadError && (
          <div className="workspace-upload-error" role="alert">
            {uploadError}
            {/* 缺 Key 被拦下时，错误提示里直接给出去设置页的入口（不跳转，以免丢失已选文件） */}
            {!canIngest && ingestBlockReason?.type === 'key' && isLocalMode && (
              <button
                type="button"
                className="workspace-upload-notice-action"
                style={{ marginLeft: 10 }}
                onClick={() => navigate('/settings#embedding')}
              >
                前往设置
              </button>
            )}
          </div>
        )}
      </Motion.section >


      <Motion.section
        className="history-wrapper"
        initial={animateIn ? { opacity: 0, y: 24 } : false}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      >
        <HistoryCards
          user={user}
          onDocumentSelect={onDocumentHistorySelect}
          refreshKey={refreshKey}
        />
      </Motion.section>
    </div >
  )
}

export default WorkspaceDashboard


