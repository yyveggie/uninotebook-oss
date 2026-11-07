import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import { apiRequest } from '../utils/api'
import { dataCache } from '../utils/dataCache'
import { invalidateDocumentStatusCache } from '../utils/documentStatusCache'
import { SkeletonSessionList } from './Skeleton'
import GenerationConfigModal from './RightPanelViews/GenerationConfigModal'
import ConfirmDialog from './ConfirmDialog'
import { MarkdownRenderer } from './MarkdownRenderer'
import { useNavigate } from 'react-router-dom'
import useRuntimeStore, { useCapability } from '../stores/runtimeStore'
import './DocumentInfo.css'

const normalizeDocumentContent = (rawText) => {
  if (!rawText) return ''

  const cleanedText = rawText
    .replace(/\r\n/g, '\n')
    .replace(/\(cid:\d+\)/gi, '')

  const isBulletLine = (line) => /^\s*(?:[-•●·]|\d+\.)\s+/.test(line)
  const isEquationNumber = (line) => /^\(*\s*\d+\s*\)*$/.test(line)
  const isTinyGlyph = (line) => {
    if (!line) return false
    const stripped = line.replace(/\s+/g, '')
    if (!stripped) return false
    if (/^[,.;:!?]$/.test(stripped)) return true
    if (/^\.{2,}$/.test(stripped)) return true
    if (/^[A-Za-z0-9]$/.test(stripped)) return true
    if (/^[\p{Sc}\p{Sk}\p{Sm}\p{So}]$/u.test(stripped)) return true
    if (/^[\p{L}]$/u.test(stripped)) return true
    return stripped.length === 1
  }

  const normalizeParagraph = (paragraph) => {
    const lines = paragraph
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)

    if (!lines.length) return ''

    const mergedLines = []
    let currentSentence = ''
    let glyphBuffer = ''

    const flushGlyphBuffer = () => {
      if (!glyphBuffer) return
      // 🔧 修复：保留单词间的空格，只清理首尾和多余空格
      const glyphText = glyphBuffer.trim().replace(/\s+/g, ' ')
      if (!glyphText) {
        glyphBuffer = ''
        return
      }
      if (currentSentence) {
        currentSentence += (currentSentence.endsWith(' ') ? '' : ' ') + glyphText
      } else {
        currentSentence = glyphText
      }
      glyphBuffer = ''
    }

    const flushSentence = () => {
      flushGlyphBuffer()
      if (currentSentence.trim()) {
        mergedLines.push(currentSentence.trim())
      }
      currentSentence = ''
    }

    lines.forEach((line) => {
      if (isBulletLine(line) || isEquationNumber(line.replace(/\s+/g, ''))) {
        flushSentence()
        mergedLines.push(line)
        return
      }

      if (isTinyGlyph(line)) {
        // 🔧 修复：在tiny glyph之间保留空格
        glyphBuffer += (glyphBuffer ? ' ' : '') + line
        return
      }

      flushGlyphBuffer()

      const normalizedLine = line.replace(/\s+/g, ' ')
      if (currentSentence) {
        currentSentence += (currentSentence.endsWith(' ') ? '' : ' ') + normalizedLine
      } else {
        currentSentence = normalizedLine
      }
    })

    flushSentence()

    return mergedLines.join('\n')
  }

  const paragraphs = cleanedText.split(/\n{2,}/)
  const normalizedParagraphs = paragraphs
    .map(normalizeParagraph)
    .filter(Boolean)

  return normalizedParagraphs.join('\n\n')
}

const DOCUMENT_CONTENT_CACHE_TTL = 5 * 60 * 1000
const buildDocumentContentCacheKey = (documentId) => `document:${documentId}:content`
const buildDocumentContentStorageKey = (documentId) => `document_${documentId}_content_cache`

const readDocumentContentFromStorage = (documentId) => {
  if (!documentId) return null
  const key = buildDocumentContentStorageKey(documentId)
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed?.expiresAt && parsed.expiresAt < Date.now()) {
      localStorage.removeItem(key)
      return null
    }
    return typeof parsed?.value === 'string' ? parsed.value : null
  } catch {
    localStorage.removeItem(key)
    return null
  }
}

const persistDocumentContentToStorage = (documentId, value) => {
  if (!documentId) return
  const key = buildDocumentContentStorageKey(documentId)
  try {
    const payload = {
      value,
      expiresAt: Date.now() + DOCUMENT_CONTENT_CACHE_TTL
    }
    localStorage.setItem(key, JSON.stringify(payload))
  } catch {
    // ignore storage errors
  }
}

const MAX_FILES_PER_UPLOAD = 5
const SOURCE_UPLOAD_POLL_INTERVAL_MS = 1500
const SOURCE_UPLOAD_POLL_MAX_ATTEMPTS = 80
const SOURCE_UPLOAD_ENQUEUE_CONCURRENCY = 3

const getSourceTypeLabel = (sourceType) => {
  const key = String(sourceType || '').toLowerCase().trim()
  if (key === 'file') return '文件'
  if (key === 'web') return '网页'
  if (key === 'web_research') return '深度研究'
  if (key === 'text') return '文本'
  if (key === 'document') return '文档'
  return '文本'
}

const DocumentInfo = ({
  document,
  onLoadSession,
  currentSessionId,
  refreshKey,
  isMaximized,
  onToggleMaximize,
  selectedSourceIds = [],
  onSelectedSourcesChange,
  onOpenPdfPreview,
}) => {

  const [sessions, setSessions] = useState([])
  const [isLoadingSessions, setIsLoadingSessions] = useState(false)
  const hasLoadedSessionsRef = useRef(false)
  const pendingSelectRef = useRef(null)
  // 当前展示的文档 ID：异步请求返回时据此判断结果是否已过期（组件跨文档复用，不会重新挂载）
  const activeDocIdRef = useRef(document?.document_id ?? null)
  activeDocIdRef.current = document?.document_id ?? null
  const [, setShowMenu] = useState(false)
  const [menuState, setMenuState] = useState(null) // { id, rect, items }
  const [isRenaming, setIsRenaming] = useState(false)
  const [newName, setNewName] = useState('')
  const [sources, setSources] = useState([])
  const [isLoadingSources, setIsLoadingSources] = useState(false)
  const [hasLoadedSourcesOnce, setHasLoadedSourcesOnce] = useState(false)
  const [showSourceForm, setShowSourceForm] = useState(false)
  const [sourceType, setSourceType] = useState(null)
  const [sourceName, setSourceName] = useState('')
  const [sourceContent, setSourceContent] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [sourceFiles, setSourceFiles] = useState([])
  const [isSavingSource, setIsSavingSource] = useState(false)
  const [deletingSourceId, setDeletingSourceId] = useState(null)
  const [deleteConfirmState, setDeleteConfirmState] = useState({ isOpen: false, message: '', onConfirm: null })
  const [renamingSourceId, setRenamingSourceId] = useState(null)
  const [sourceEditName, setSourceEditName] = useState('')
  const [deepResearchQuestion, setDeepResearchQuestion] = useState('')
  const [isSearchingDeepResearch, setIsSearchingDeepResearch] = useState(false)
  const [isFileListCollapsed, setIsFileListCollapsed] = useState(false)
  const hasInitializedSelectionRef = useRef(false)
  const [showContentModal, setShowContentModal] = useState(false)
  const [contentModalTitle, setContentModalTitle] = useState('')
  const [contentModalText, setContentModalText] = useState('')
  const [isLoadingModalContent, setIsLoadingModalContent] = useState(false)
  const sourceFileInputRef = useRef(null)
  const isDeepResearchBusy = isSearchingDeepResearch
  const navigate = useNavigate()
  const isLocalMode = useRuntimeStore((s) => s.deployMode === 'local')
  // 联网研究依赖可选服务与依赖：不可用时置灰并说明原因
  const deepResearchCapability = useCapability('deep_research')
  const deepResearchBlockReason = deepResearchCapability.reason

  const [fullText, setFullText] = useState('')
  const [, setIsLoadingFullText] = useState(false)
  const [isFullTextLoaded, setIsFullTextLoaded] = useState(false) // 是否已加载完全文


  const primarySource = useMemo(() => {
    return {
      id: `primary-${document?.document_id || 'unknown'}`,
      source_id: `document:${document?.document_id || 'unknown'}`,
      source_type: document?.file_type || 'document',
      source_name: document?.display_name || document?.filename || '主文档',
      source_url: null,
      chunk_count: document?.chunk_count || 0,
      is_primary: true,
    }
  }, [document?.document_id, document?.file_type, document?.display_name, document?.filename, document?.chunk_count])

  const visibleSources = useMemo(() => {
    const reportSources = []
    const urlSources = []
    const otherSources = []

    sources.forEach((item) => {
      const sourceType = (item?.source_type || '').toLowerCase()
      if (sourceType === 'web_research') {
        reportSources.push(item)
      } else if (item?.source_url) {
        urlSources.push(item)
      } else {
        otherSources.push(item)
      }
    })

    return [...reportSources, ...urlSources, ...otherSources]
  }, [sources])
  const visibleSourceIds = useMemo(() => visibleSources.map((item) => item.source_id), [visibleSources])
  const primaryFileCount = useMemo(() => {
    const fileType = String(document?.file_type || '').toLowerCase().trim()
    return fileType && fileType !== 'url' ? 1 : 0
  }, [document?.file_type])
  const fileSourceCount = useMemo(
    () => sources.filter((item) => (item?.source_type || '').toLowerCase() === 'file').length,
    [sources]
  )
  const totalFileCount = primaryFileCount + fileSourceCount
  const isFileSourceLimitReached = totalFileCount >= MAX_FILES_PER_UPLOAD
  const selectedSet = useMemo(() => new Set(selectedSourceIds || []), [selectedSourceIds])
  const hasVisibleSources = visibleSources.length > 0
  const isAllVisibleSelected = hasVisibleSources && visibleSourceIds.every((id) => selectedSet.has(id))
  const allFileItems = useMemo(() => [primarySource, ...visibleSources], [primarySource, visibleSources])

  const withPrimarySelected = useCallback((ids) => {
    const normalized = Array.from(new Set([primarySource.source_id, ...(ids || [])]))
    return normalized
  }, [primarySource.source_id])

  const loadSources = useCallback(async ({ silent = false } = {}) => {
    if (!document?.document_id) return
    const requestedDocId = document.document_id
    if (!silent) setIsLoadingSources(true)
    try {
      const response = await apiRequest(`/api/document/${requestedDocId}/sources`)
      const data = await response.json()
      // 切换文档后，上一篇文档迟到的响应不能覆盖当前文档的来源列表
      if (activeDocIdRef.current !== requestedDocId) return
      if (response.ok && data?.success) {
        setSources(Array.isArray(data.data) ? data.data : [])
      }
    } catch {
      // ignore
    } finally {
      if (activeDocIdRef.current === requestedDocId) {
        setHasLoadedSourcesOnce(true)
        if (!silent) setIsLoadingSources(false)
      }
    }
  }, [document?.document_id])

  const handleToggleSource = (sourceId) => {
    if (!onSelectedSourcesChange) return
    const current = Array.isArray(selectedSourceIds) ? selectedSourceIds : []
    const withoutPrimary = current.filter((id) => id !== primarySource.source_id)
    if (withoutPrimary.includes(sourceId)) {
      onSelectedSourcesChange(withPrimarySelected(withoutPrimary.filter((id) => id !== sourceId)))
    } else {
      onSelectedSourcesChange(withPrimarySelected([...withoutPrimary, sourceId]))
    }
  }

  const handleToggleAllSources = () => {
    if (!onSelectedSourcesChange) return
    if (!hasVisibleSources) return
    const isAllSelected = visibleSourceIds.length > 0 && visibleSourceIds.every((id) => selectedSet.has(id))
    onSelectedSourcesChange(isAllSelected ? withPrimarySelected([]) : withPrimarySelected(visibleSourceIds))
  }

  const enqueueSingleSourceFile = async (currentFile, filesLength, trimmedSourceName) => {
    const formData = new FormData()
    formData.append('file', currentFile)
    formData.append('total_files', String(filesLength))
    if (trimmedSourceName && filesLength === 1) {
      formData.append('source_name', trimmedSourceName)
    }

    const response = await apiRequest(`/api/document/${document.document_id}/sources/upload-file`, {
      method: 'POST',
      body: formData,
    })
    const data = await response.json()
    if (!response.ok || !data?.success) {
      throw new Error(data?.detail || data?.message || `上传文件失败：${currentFile?.name || '未知文件'}`)
    }
    const taskId = String(data?.data?.task_id || '').trim()
    if (!taskId) {
      throw new Error(`任务ID缺失：${currentFile?.name || '未知文件'}`)
    }
    return taskId
  }

  const enqueueSourceFilesConcurrent = async (files, trimmedSourceName) => {
    const taskIds = new Array(files.length)
    const concurrency = Math.max(1, Math.min(SOURCE_UPLOAD_ENQUEUE_CONCURRENCY, files.length))
    let nextIndex = 0
    let firstError = null

    const worker = async () => {
      while (true) {
        if (firstError) return
        const currentIndex = nextIndex
        nextIndex += 1
        if (currentIndex >= files.length) return

        try {
          const currentFile = files[currentIndex]
          const taskId = await enqueueSingleSourceFile(currentFile, files.length, trimmedSourceName)
          taskIds[currentIndex] = taskId
        } catch (error) {
          firstError = error
          return
        }
      }
    }

    await Promise.all(Array.from({ length: concurrency }, () => worker()))

    if (firstError) {
      throw firstError
    }

    return taskIds.filter(Boolean)
  }

  const evaluateBatchTaskPayload = (tasks, expectedTaskCount) => {
    const normalizedTasks = Array.isArray(tasks) ? tasks : []
    for (const task of normalizedTasks) {
      if (task?.status === 'failed') {
        throw new Error(task?.error || '来源文件处理失败')
      }
    }
    const completedCount = normalizedTasks.filter((task) => task?.status === 'completed').length
    return completedCount >= expectedTaskCount && expectedTaskCount > 0
  }

  const pollSourceTasksInBatch = async (queuedTaskIds) => {
    for (let attempt = 0; attempt < SOURCE_UPLOAD_POLL_MAX_ATTEMPTS; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, SOURCE_UPLOAD_POLL_INTERVAL_MS))
      const statusResp = await apiRequest('/api/document/source-tasks/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_ids: queuedTaskIds }),
      })
      const statusData = await statusResp.json()
      if (!statusResp.ok || !statusData?.success) {
        throw new Error(statusData?.detail || statusData?.message || '来源任务批量状态查询失败')
      }

      const tasks = statusData?.data?.tasks || []
      const allDone = evaluateBatchTaskPayload(tasks, queuedTaskIds.length)
      if (allDone) {
        return
      }
    }
    throw new Error('来源文件处理超时，请稍后刷新查看')
  }

  const streamSourceTasks = async (queuedTaskIds) => {
    const response = await apiRequest('/api/document/source-tasks/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_ids: queuedTaskIds }),
    })

    if (!response.ok || !response.body) {
      throw new Error('来源任务状态推送不可用')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() || ''

      for (const block of blocks) {
        const dataLine = block.split('\n').find((line) => line.startsWith('data: '))
        if (!dataLine) continue

        const raw = dataLine.slice(6).trim()
        if (!raw) continue

        let payload
        try {
          payload = JSON.parse(raw)
        } catch {
          continue
        }

        const tasks = payload?.tasks || []
        const allDone = evaluateBatchTaskPayload(tasks, queuedTaskIds.length)
        if (allDone) {
          await reader.cancel().catch(() => undefined)
          return
        }

        if (payload?.timeout) {
          throw new Error('来源任务推送超时')
        }
      }
    }

    await pollSourceTasksInBatch(queuedTaskIds)
  }

  const handleAddSource = async () => {
    if (!document?.document_id || isSavingSource) {
      return
    }

    try {
      if (sourceType === 'file' && isFileSourceLimitReached) {
        throw new Error(`最多 ${MAX_FILES_PER_UPLOAD} 个文件来源，可删除后继续添加`)
      }
      setIsSavingSource(true)
      setShowSourceForm(false)
      setSourceType(null)
      setSourceName('')
      setSourceContent('')
      setSourceUrl('')
      setSourceFiles([])
      if (sourceFileInputRef.current) sourceFileInputRef.current.value = ''
      let response
      const trimmedSourceName = sourceName.trim()
      const normalizedSourceType = sourceType === 'web' ? 'web' : 'text'
      const autoSourceName = trimmedSourceName || (
        sourceType === 'web'
          ? (sourceUrl.trim() ? `网页来源：${sourceUrl.trim().slice(0, 48)}` : '网页来源')
          : '文本来源'
      )

      if (sourceType === 'file') {
        const files = Array.isArray(sourceFiles) ? sourceFiles : []
        if (files.length === 0) {
          throw new Error('请先选择要上传的文件')
        }
        if (files.length > MAX_FILES_PER_UPLOAD) {
          throw new Error(`一次最多上传 ${MAX_FILES_PER_UPLOAD} 个文件`)
        }
        const queuedTaskIds = await enqueueSourceFilesConcurrent(files, trimmedSourceName)

        if (queuedTaskIds.length > 0) {
          try {
            await streamSourceTasks(queuedTaskIds)
          } catch (streamError) {
            const message = String(streamError?.message || '')
            if (message.includes('处理失败') || message.includes('失败')) {
              throw streamError
            }
            await pollSourceTasksInBatch(queuedTaskIds)
          }
          await loadSources({ silent: true })
          return
        }
        await loadSources({ silent: true })
        return
      } else if (sourceType === 'web' && sourceUrl.trim() && !sourceContent.trim()) {
        response = await apiRequest(`/api/document/${document.document_id}/sources/upload-url`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: sourceUrl.trim(),
            source_name: trimmedSourceName || null,
          }),
        })
      } else {
        if (!sourceContent.trim()) {
          throw new Error('请填写来源内容')
        }
        response = await apiRequest(`/api/document/${document.document_id}/sources`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source_type: normalizedSourceType,
            source_name: autoSourceName,
            content: sourceContent.trim(),
            source_url: sourceUrl.trim() || null,
          }),
        })
      }

      const data = await response.json()
      if (!response.ok || !data?.success) {
        throw new Error(data?.detail || data?.message || '添加来源失败')
      }

      await loadSources({ silent: true })
    } catch (error) {
      alert(error?.message || '添加来源失败')
    } finally {
      setIsSavingSource(false)
    }
  }

  const handlePickSourceFiles = () => {
    sourceFileInputRef.current?.click()
  }

  const handleSourceFilesChange = (event) => {
    const fileList = Array.from(event.target.files || [])
    const limitedFiles = fileList.slice(0, MAX_FILES_PER_UPLOAD)
    if (fileList.length > MAX_FILES_PER_UPLOAD) {
      alert(`最多 ${MAX_FILES_PER_UPLOAD} 个文件来源，可删除后继续添加`)
    }
    setSourceFiles(limitedFiles)
    if (limitedFiles.length === 1 && !sourceName.trim()) {
      const pureName = (limitedFiles[0]?.name || '').replace(/\.[^/.]+$/, '')
      setSourceName(pureName || '文件来源')
    }
  }

  const openMenu = (e, id, items) => {
    e.stopPropagation()
    if (menuState?.id === id) { setMenuState(null); return }
    const rect = e.currentTarget.getBoundingClientRect()
    setMenuState({ id, rect, items })
  }

  const closeMenu = () => setMenuState(null)

  const handleRenameSourceClick = (sourceItem) => {
    setSourceEditName(sourceItem.source_name || '')
    setRenamingSourceId(sourceItem.id)
    closeMenu()
  }

  const handleSaveSourceRename = async (sourceItem) => {
    const trimmed = sourceEditName.trim()
    if (!trimmed || trimmed === sourceItem.source_name) {
      setRenamingSourceId(null)
      return
    }
    try {
      await apiRequest(`/api/document/${document.document_id}/sources/${sourceItem.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_name: trimmed }),
      })
      await loadSources({ silent: true })
    } catch (err) {
      alert(err?.message || '重命名失败')
    } finally {
      setRenamingSourceId(null)
    }
  }

  const handleCancelSourceRename = () => {
    setRenamingSourceId(null)
  }

  const handleDeleteSource = async (sourceItem) => {
    if (!sourceItem || sourceItem.is_primary || deletingSourceId) return
    setDeleteConfirmState({
      isOpen: true,
      message: `确定删除来源“${sourceItem.source_name}”吗？`,
      onConfirm: async () => {
        setDeleteConfirmState({ isOpen: false, message: '', onConfirm: null })
        try {
          setDeletingSourceId(sourceItem.id)
          const response = await apiRequest(`/api/document/${document.document_id}/sources/${sourceItem.id}`, {
            method: 'DELETE',
          })
          const data = await response.json()
          if (!response.ok || !data?.success) {
            throw new Error(data?.detail || data?.message || '删除来源失败')
          }
          await loadSources({ silent: true })
        } catch (error) {
          setDeleteConfirmState({
            isOpen: true,
            isAlert: true,
            message: error?.message || '删除来源失败',
            onConfirm: () => setDeleteConfirmState({ isOpen: false, message: '', onConfirm: null }),
          })
        } finally {
          setDeletingSourceId(null)
        }
      },
    })
  }

  // Deep Research：调用 LLM 生成研究报告，直接存为单个来源，完成后关闭弹窗
  const handleDeepResearch = async () => {
    if (!document?.document_id || !deepResearchQuestion.trim() || isSearchingDeepResearch) return
    if (!deepResearchCapability.available) return
    try {
      const question = deepResearchQuestion.trim()
      setIsSearchingDeepResearch(true)
      setShowSourceForm(false)
      setSourceType(null)
      setDeepResearchQuestion('')
      const res = await apiRequest(`/api/document/${document.document_id}/sources/deep-research`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          source_name: question.slice(0, 60),
        }),
      })
      const data = await res.json()
      if (!res.ok || !data?.success) {
        throw new Error(data?.detail || data?.message || 'Deep Research 失败')
      }
      await loadSources({ silent: true })
    } catch (error) {
      alert(error?.message || 'Deep Research 失败')
    } finally {
      setIsSearchingDeepResearch(false)
    }
  }

  const handleSourceModalSubmit = async () => {
    if (sourceType === null || sourceType === 'deep_research') {
      setShowSourceForm(false)
      await handleDeepResearch()
      return
    }
    await handleAddSource()
  }

  // 加载会话列表 - 在文档变化或refreshKey变化时重新加载
  useEffect(() => {
    loadSessions()
  }, [document?.document_id, refreshKey])

  useEffect(() => {
    hasInitializedSelectionRef.current = false
    setHasLoadedSourcesOnce(false)
    // 先清掉上一篇文档的来源，避免加载期间被当作当前文档的可选来源
    setSources([])
    loadSources()
  }, [document?.document_id, loadSources])

  useEffect(() => {
    if (!onSelectedSourcesChange) return
    const validIds = [primarySource.source_id, ...visibleSourceIds]

    if (!hasInitializedSelectionRef.current) {
      if (hasLoadedSourcesOnce && validIds.length > 0) {
        onSelectedSourcesChange(withPrimarySelected(visibleSourceIds))
        hasInitializedSelectionRef.current = true
      }
      return
    }

    if (selectedSourceIds.length > 0) {
      const filtered = selectedSourceIds.filter((id) => validIds.includes(id))
      const normalized = withPrimarySelected(filtered.filter((id) => id !== primarySource.source_id))
      const changed = normalized.length !== selectedSourceIds.length || normalized.some((id, idx) => id !== selectedSourceIds[idx])
      if (changed) {
        onSelectedSourcesChange(normalized)
      }
    } else {
      onSelectedSourcesChange(withPrimarySelected(visibleSourceIds))
    }
  }, [primarySource.source_id, visibleSourceIds, selectedSourceIds, onSelectedSourcesChange, withPrimarySelected, hasLoadedSourcesOnce])

  // 加载文档全文用于段落批注
  const loadFullText = useCallback(async ({ forceRefresh = false, previewOnly = true } = {}) => {
    if (!document?.document_id) return

    // 如果请求的是全量且已经加载了全量，且不是强制刷新，则跳过
    if (!forceRefresh && !previewOnly && isFullTextLoaded) return

    const cacheKey = buildDocumentContentCacheKey(document.document_id)

    // URL 文档（或文件名看起来是 URL）始终走实时接口，避免缓存住旧的纯 URL 文本
    const isUrlDoc = (() => {
      const ft = (document.file_type || '').toLowerCase()
      if (ft === 'url') return true
      const name = document.filename || ''
      return /^https?:\/\//.test(name.trim())
    })()

    if (!forceRefresh && !isUrlDoc) {
      const cached = dataCache.get(cacheKey)
      if (cached) {
        setFullText(cached)
        setIsFullTextLoaded(true) // 缓存肯定是全量的
        setIsLoadingFullText(false)
        return cached
      }

      const stored = readDocumentContentFromStorage(document.document_id)
      if (stored) {
        setFullText(stored)
        setIsFullTextLoaded(true)
        dataCache.set(cacheKey, stored, DOCUMENT_CONTENT_CACHE_TTL)
        setIsLoadingFullText(false)
        return stored
      }
    }

    setIsLoadingFullText(true)
    try {
      // 如果是预览模式，请求前 500 字符
      const url = `/api/document/${document.document_id}/content` + (previewOnly ? '?limit=500' : '')
      const response = await apiRequest(url)

      if (response.ok) {
        const data = await response.json()
        if (data.success && data.data.content) {
          const normalized = normalizeDocumentContent(data.data.content)
          setFullText(normalized)

          // 只有非预览模式（全量）才标记已加载完成并写缓存
          if (!previewOnly) {
            setIsFullTextLoaded(true)
            // 仅对非 URL 文档写缓存
            if (!isUrlDoc) {
              dataCache.set(cacheKey, normalized, DOCUMENT_CONTENT_CACHE_TTL)
              persistDocumentContentToStorage(document.document_id, normalized)
            }
          }
          return normalized
        }
      }
    } catch (err) {
      void err;
    } finally {
      setIsLoadingFullText(false)
    }
    return ''
  }, [document?.document_id, isFullTextLoaded])

  useEffect(() => {
    // 初始化只加载预览
    loadFullText({ previewOnly: true })
    // 重置状态
    setIsFullTextLoaded(false)
    setShowContentModal(false)
    setContentModalTitle('')
    setContentModalText('')
  }, [document?.document_id]) // 注意：这里不能加 loadFullText 依赖，否则可能会循环调用

  const handleCloseContentModal = () => {
    setShowContentModal(false)
    setContentModalTitle('')
    setContentModalText('')
  }

  // 主文档全文的预取：鼠标移到文件名上就开始加载，点开时多半已经就绪。
  // 记下进行中的请求，悬停后马上点击时复用它，不重复请求
  const fullTextRequestRef = useRef(null)
  const prefetchFullText = () => {
    if (isFullTextLoaded || fullTextRequestRef.current) return fullTextRequestRef.current
    const request = loadFullText({ previewOnly: false }).finally(() => {
      fullTextRequestRef.current = null
    })
    fullTextRequestRef.current = request
    return request
  }

  const handleOpenSourceContentModal = async (item) => {
    if (!item) return

    setContentModalTitle(item.source_name || '来源内容')

    // 全文已就绪：直接带着内容打开，不经过「加载中」——
    // 否则卡片先以一行字的高度出现、再撑开到全文高度，打开时会跳一下
    if (item.is_primary && isFullTextLoaded && fullText) {
      setContentModalText(fullText)
      setIsLoadingModalContent(false)
      setShowContentModal(true)
      return
    }

    setShowContentModal(true)
    setContentModalText('')
    setIsLoadingModalContent(true)

    try {
      if (item.is_primary) {
        const content = await prefetchFullText()
        setContentModalText(content || fullText || '暂无内容')
        return
      }

      const response = await apiRequest(`/api/document/${document.document_id}/sources/${item.id}/content`)
      const data = await response.json()
      if (!response.ok || !data?.success) {
        throw new Error(data?.detail || data?.message || '加载来源内容失败')
      }

      const loadedContent = data?.data?.content || item.source_preview || '暂无内容'
      setContentModalText(loadedContent)
    } catch (error) {
      alert(error?.message || '加载来源内容失败')
      setShowContentModal(false)
    } finally {
      setIsLoadingModalContent(false)
    }
  }

  const loadSessions = async () => {
    if (!document?.document_id) return
    const requestedDocId = document.document_id
    const shouldShowLoading = !hasLoadedSessionsRef.current && sessions.length === 0
    try {
      if (shouldShowLoading) {
        setIsLoadingSessions(true)
      }
      const response = await apiRequest(`/api/document/${requestedDocId}/sessions`)

      // 切换文档后，上一篇文档迟到的会话列表直接丢弃
      if (activeDocIdRef.current !== requestedDocId) return

      if (response.ok) {
        const data = await response.json()
        if (data.success && data.data) {
          setSessions(prevSessions => {
            const prev = prevSessions || []
            const next = data.data || []
            if (prev.length === next.length) {
              let same = true
              for (let i = 0; i < prev.length; i += 1) {
                const p = prev[i]
                const n = next[i]
                if (
                  p.session_id !== n.session_id ||
                  p.message_count !== n.message_count ||
                  p.first_question !== n.first_question ||
                  p.last_activity_time !== n.last_activity_time
                ) {
                  same = false
                  break
                }
              }
              if (same) {
                return prevSessions
              }
            }
            return next
          })

          if (!hasLoadedSessionsRef.current) {
            hasLoadedSessionsRef.current = true
          }

          const hasActiveSession = data.data.some(session => session.session_id === currentSessionId)
          if (!hasActiveSession && data.data.length > 0 && onLoadSession) {
            pendingSelectRef.current = data.data[0].session_id
          }
        }
      }
    } catch (err) {
      void err;
    } finally {
      if (shouldShowLoading) {
        setIsLoadingSessions(false)
      }
      pendingSelectRef.current = null
    }
  }

  useEffect(() => {
    if (!pendingSelectRef.current || !onLoadSession) return
    const sessionId = pendingSelectRef.current
    const timeout = requestAnimationFrame(() => {
      if (sessions.some(session => session.session_id === sessionId)) {
        onLoadSession(sessionId)
        pendingSelectRef.current = null
      }
    })
    return () => cancelAnimationFrame(timeout)
  }, [sessions, onLoadSession])

  // 点击外部关闭来源 ⋮ 菜单
  useEffect(() => {
    if (!menuState) return
    const handleClickOutside = () => setMenuState(null)
    window.addEventListener('click', handleClickOutside)
    return () => window.removeEventListener('click', handleClickOutside)
  }, [menuState])


  // 处理点击文档名称
  const handleDocumentClick = async () => {
    // 如果正在重命名，不响应点击
    if (isRenaming) return
    await handleOpenSourceContentModal(primarySource)
  }

  // 开始重命名（修改来源文件名）
  const handleRenameClick = () => {
    setNewName(document.filename)
    setIsRenaming(true)
    setShowMenu(false)
  }

  // 保存重命名
  const handleSaveRename = async () => {
    const trimmedName = newName.trim()

    // 如果名称为空或与原名称相同，直接取消
    if (!trimmedName || trimmedName === document.filename) {
      setIsRenaming(false)
      setNewName('')
      return
    }

    try {
      const response = await apiRequest(`/api/document/${document.document_id}/rename-file`, {
        method: 'PUT',
        body: JSON.stringify({ new_filename: trimmedName })
      })

      if (response.ok) {
        document.filename = trimmedName  // 更新本地数据
        setIsRenaming(false)
        setNewName('')
        dataCache.invalidateByPrefix('history:documents:')
        dataCache.invalidateByPrefix(`doc:${document.document_id}:history:`)
        invalidateDocumentStatusCache(document.document_id)
      } else {
        alert('重命名失败')
        setIsRenaming(false)
      }
    } catch (err) {
      void err;
      alert('重命名失败')
      setIsRenaming(false)
    }
  }

  // 取消重命名
  const handleCancelRename = () => {
    setIsRenaming(false)
    setNewName('')
  }

  if (!document) return null

  return (
    <>
    <div
      className="document-info-panel"
    >
      <div className="info-header">
        <h3 className="info-title">来源</h3>
        {onToggleMaximize && (
          <button
            className="panel-embedded-maximize-btn"
            onClick={onToggleMaximize}
            title={isMaximized ? '还原' : '最大化'}
          >
            {isMaximized ? (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3" />
              </svg>
            ) : (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
              </svg>
            )}
          </button>
        )}
      </div>

      <div className="sources-section">
        {onOpenPdfPreview && (
          <div className="sources-header-row" style={{ marginBottom: 8 }}>
            <button className="sources-add-btn sources-add-btn-full" onClick={onOpenPdfPreview}>
              📄 查看 PDF 原文
            </button>
          </div>
        )}
        <div className="sources-header-row">
          <button
            className={`sources-add-btn sources-add-btn-full${(isDeepResearchBusy || isSavingSource) ? ' sources-add-btn--busy' : ''}`}
            onClick={() => setShowSourceForm((prev) => !prev)}
            disabled={isDeepResearchBusy || isSavingSource || isFileSourceLimitReached}
          >
            {isDeepResearchBusy
              ? 'Deep Research 中...'
              : isSavingSource
                ? '添加中...'
                : isFileSourceLimitReached
                  ? `最多 ${MAX_FILES_PER_UPLOAD} 个文件来源，可删除后继续添加`
                  : '+ 添加来源'}
          </button>
        </div>

        <div className="file-list-box">
          <button
            type="button"
            className="file-list-header"
            onClick={() => setIsFileListCollapsed((prev) => !prev)}
          >
            <span className="file-list-title">文件列表</span>
            <span className="file-list-chevron">{isFileListCollapsed ? '▸' : '▾'}</span>
          </button>

          {!isFileListCollapsed && (
            <>
              {hasVisibleSources && (
                <div className="sources-select-all" onClick={handleToggleAllSources}>
                  <input
                    type="checkbox"
                    readOnly
                    checked={isAllVisibleSelected}
                  />
                  <span>选择所有可选来源</span>
                </div>
              )}

              {isLoadingSources ? (
                <div className="sources-loading">加载来源中...</div>
              ) : (
                <div className="sources-list">
                  {allFileItems.map((item) => {
                    if (item.is_primary) {
                      return (
                        <div key={item.source_id} className="source-item">
                          <div className="source-item-row">
                            <div className="source-item-main">
                              <input
                                type="checkbox"
                                checked
                                disabled
                                readOnly
                              />
                              <div className="source-item-text">
                                {isRenaming ? (
                                  <input
                                    type="text"
                                    value={newName}
                                    onChange={(e) => setNewName(e.target.value)}
                                    onKeyDown={(e) => {
                                      if (e.key === 'Enter') { e.preventDefault(); handleSaveRename() }
                                      if (e.key === 'Escape') { e.preventDefault(); handleCancelRename() }
                                    }}
                                    onBlur={handleSaveRename}
                                    autoFocus
                                    className="document-rename-input"
                                    style={{ width: '100%' }}
                                  />
                                ) : (
                                  <button
                                    className="source-item-name-btn"
                                    type="button"
                                    onClick={handleDocumentClick}
                                    onMouseEnter={prefetchFullText}
                                    onFocus={prefetchFullText}
                                  >
                                    <span
                                      className="source-item-name"
                                      title={document.filename || '未命名文档'}
                                    >
                                      {document.filename || '未命名文档'}
                                    </span>
                                  </button>
                                )}
                                <div className="source-item-meta">
                                  主文档
                                </div>
                              </div>
                            </div>
                            <div className="source-menu-wrap">
                              <button
                                className="source-menu-btn"
                                onClick={(e) => openMenu(e, 'primary', [
                                  { label: '重命名', action: () => { closeMenu(); handleRenameClick() } },
                                ])}
                              >⋮</button>
                            </div>
                          </div>
                        </div>
                      )
                    }

                    return (
                      <div key={item.source_id} className="source-item">
                        <div className="source-item-row">
                          <div className="source-item-main">
                            <input
                              type="checkbox"
                              checked={selectedSet.has(item.source_id)}
                              onChange={() => handleToggleSource(item.source_id)}
                            />
                            <div className="source-item-text">
                              {renamingSourceId === item.id ? (
                                <input
                                  type="text"
                                  value={sourceEditName}
                                  onChange={(e) => setSourceEditName(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') { e.preventDefault(); handleSaveSourceRename(item) }
                                    if (e.key === 'Escape') { e.preventDefault(); handleCancelSourceRename() }
                                  }}
                                  onBlur={() => handleSaveSourceRename(item)}
                                  autoFocus
                                  className="document-rename-input"
                                  style={{ width: '100%' }}
                                />
                              ) : (
                                <button
                                  className="source-item-name-btn"
                                  type="button"
                                  onClick={() => handleOpenSourceContentModal(item)}
                                >
                                  <span className="source-item-name" title={item.source_name}>
                                    {item.source_name}
                                  </span>
                                </button>
                              )}
                              <div className="source-item-meta">
                                {getSourceTypeLabel(item.source_type)}
                              </div>
                            </div>
                          </div>
                          <div className="source-menu-wrap">
                            <button
                              className="source-menu-btn"
                              onClick={(e) => openMenu(e, item.source_id, [
                                { label: '重命名', action: () => { closeMenu(); handleRenameSourceClick(item) } },
                                { label: deletingSourceId === item.id ? '删除中...' : '删除', danger: true, disabled: !!deletingSourceId, action: () => { closeMenu(); handleDeleteSource(item) } },
                              ])}
                            >⋮</button>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <GenerationConfigModal
        open={showSourceForm && !isDeepResearchBusy}
        title="添加来源"
        onClose={() => {
          setShowSourceForm(false)
          setSourceType(null)
          setSourceName('')
          setSourceContent('')
          setSourceUrl('')
          setSourceFiles([])
          setDeepResearchQuestion('')
          if (sourceFileInputRef.current) sourceFileInputRef.current.value = ''
        }}
        onSubmit={handleSourceModalSubmit}
        submitLabel={
          (sourceType === null || sourceType === 'deep_research') ? 'Deep Research' : '保存来源'
        }
        isSubmitting={isDeepResearchBusy}
        submitDisabled={(sourceType === null || sourceType === 'deep_research') && !deepResearchCapability.available}
      >
        <div className="add-source-layout">

          {/* Deep Research 搜索栏 */}
          {!(sourceType === 'web' || sourceType === 'file' || sourceType === 'text') && (
            <>
          <div className="add-source-section-title">深度报告</div>
          <div className="add-source-research-header">
            <p className="add-source-research-desc">
              深度检索网页并生成研究报告，自动添加为来源。通常需要 3–4 分钟。
            </p>
            {deepResearchBlockReason && (
              <p className="add-source-research-desc" role="status" style={{ color: '#8a5a12' }}>
                暂不可用：{deepResearchBlockReason.text}
                {deepResearchBlockReason.type === 'key' && isLocalMode && (
                  <button type="button" className="add-source-back-btn" style={{ marginLeft: 8 }} onClick={() => navigate('/settings')}>
                    前往设置
                  </button>
                )}
                {deepResearchBlockReason.command && <code style={{ display: 'block', marginTop: 4 }}>{deepResearchBlockReason.command}</code>}
              </p>
            )}
          </div>
          <div className="add-source-search-wrap">
            <svg className="add-source-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              className="add-source-search-input"
              placeholder="输入研究主题，生成研究报告"
              value={deepResearchQuestion}
              onChange={(e) => setDeepResearchQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !isDeepResearchBusy) handleDeepResearch()
              }}
              disabled={isDeepResearchBusy || !deepResearchCapability.available}
              title={deepResearchBlockReason?.text}
            />
            <button
              type="button"
              className="add-source-search-btn"
              onClick={handleDeepResearch}
              disabled={isDeepResearchBusy || !deepResearchQuestion.trim() || !deepResearchCapability.available}
              title={deepResearchBlockReason?.text}
            >
              {isSearchingDeepResearch ? '…' : '→'}
            </button>
          </div>
            </>
          )}

          {/* 下方：子表单 或 三个选项按钮 */}
          {(sourceType === 'web' || sourceType === 'file' || sourceType === 'text') ? (
            <div className="add-source-sub-panel">
              <button
                type="button"
                className="add-source-back-btn"
                onClick={() => {
                  setSourceType(null)
                  setSourceName('')
                  setSourceContent('')
                  setSourceUrl('')
                  setSourceFiles([])
                  if (sourceFileInputRef.current) sourceFileInputRef.current.value = ''
                }}
                disabled={isSavingSource}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
                返回
              </button>
              <input
                className="sources-input"
                placeholder="标题（可选）"
                value={sourceName}
                onChange={(e) => setSourceName(e.target.value)}
                disabled={isSavingSource}
              />
              {sourceType === 'web' && (
                <input
                  className="sources-input sources-input-tall"
                  placeholder="来源 URL（必填）"
                  value={sourceUrl}
                  onChange={(e) => setSourceUrl(e.target.value)}
                  disabled={isSavingSource}
                />
              )}
              {sourceType === 'file' && (
                <>
                  <input
                    ref={sourceFileInputRef}
                    className="source-file-input-hidden"
                    type="file"
                    accept=".pdf,.doc,.docx,.txt,.md,.html,.htm,.epub,.ppt,.pptx"
                    multiple
                    onChange={handleSourceFilesChange}
                    disabled={isSavingSource}
                  />
                  <button
                    type="button"
                    className={`sources-input sources-input-tall source-file-upload-btn ${isSavingSource ? 'disabled' : ''}`}
                    onClick={() => { if (!isSavingSource) handlePickSourceFiles() }}
                    disabled={isSavingSource}
                  >
                    {sourceFiles.length === 0
                      ? '点击选择文件'
                      : (sourceFiles.length === 1 ? sourceFiles[0].name : `已选择 ${sourceFiles.length} 个文件（最多${MAX_FILES_PER_UPLOAD}个）`)}
                  </button>
                </>
              )}
              {sourceType === 'text' && (
                <textarea
                  className="sources-textarea"
                  placeholder="粘贴来源内容（必填）"
                  value={sourceContent}
                  onChange={(e) => setSourceContent(e.target.value)}
                  disabled={isSavingSource}
                />
              )}
            </div>
          ) : (
            <>
            <div className="add-source-section-title">上传文件/URL</div>
            <div className="add-source-drop-zone">
              <div className="add-source-drop-title">或上传 / 粘贴 / 输入网址</div>
              <div className="add-source-drop-hint">PDF、文档、文本、音频</div>
              <div className="add-source-option-row">
                <button
                  type="button"
                  className="add-source-option-btn"
                  onClick={() => setSourceType('file')}
                  disabled={isSavingSource || isDeepResearchBusy || isFileSourceLimitReached}
                >
                  <span className="add-source-option-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="16" height="16"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                  </span>
                  <span>上传文件</span>
                </button>
                <button
                  type="button"
                  className="add-source-option-btn"
                  onClick={() => setSourceType('web')}
                  disabled={isSavingSource || isDeepResearchBusy}
                >
                  <span className="add-source-option-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="16" height="16"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
                  </span>
                  <span>网站 URL</span>
                </button>
                <button
                  type="button"
                  className="add-source-option-btn"
                  onClick={() => setSourceType('text')}
                  disabled={isSavingSource || isDeepResearchBusy}
                >
                  <span className="add-source-option-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="16" height="16"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                  </span>
                  <span>粘贴文字</span>
                </button>
              </div>
            </div>
            </>
          )}

        </div>
      </GenerationConfigModal>

      <AnimatePresence>
        {showContentModal && (
          <Motion.div
            className="source-content-modal-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={handleCloseContentModal}
          >
            {/* 只做透明度过渡，不缩放：缩放中的文字以低分辨率绘制、结束时才变清晰，
                整段正文在打开瞬间会「闪」一下（Retina 屏与 Safari 尤其明显） */}
            <Motion.div
              className="source-content-modal-card"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="source-content-modal-header">
                <div className="source-content-modal-title">{contentModalTitle}</div>
                <button className="source-content-modal-close" onClick={handleCloseContentModal}>✕</button>
              </div>
              <div className="source-content-modal-body">
                {isLoadingModalContent ? (
                  '加载中...'
                ) : (
                  <div className="source-content-markdown markdown-content">
                    <MarkdownRenderer>
                      {contentModalText || '暂无内容'}
                    </MarkdownRenderer>
                  </div>
                )}
              </div>
            </Motion.div>
          </Motion.div>
        )}
      </AnimatePresence>

      {/* 历史对话列表 - 始终显示 */}
      <div className="sessions-container">
        <h4 className="sessions-title">历史对话</h4>
        {isLoadingSessions ? (
          <div className="sessions-loading"><SkeletonSessionList count={3} /></div>
        ) : sessions.length > 0 ? (
          <div className="sessions-list">
            {sessions.map((session) => (
              <div
                key={session.session_id}
                className={`session-item ${currentSessionId === session.session_id ? 'active' : ''}`}
                onClick={() => onLoadSession && onLoadSession(session.session_id)}
              >
                <div className="session-row">
                  <div
                    className="session-question"
                    title={session.first_question || '未命名对话'}
                  >
                    {session.first_question || '未命名对话'}
                  </div>
                  <span className="session-time">
                    {new Date(session.last_activity_time).toLocaleDateString('zh-CN', {
                      month: 'numeric',
                      day: 'numeric'
                    })}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="sessions-empty">
            暂无历史对话
            <br />
            <span className="sessions-empty-hint">开始提问后会自动保存</span>
          </div>
        )}
      </div>
    </div>

    <ConfirmDialog
      isOpen={deleteConfirmState.isOpen}
      type={deleteConfirmState.isAlert ? 'alert' : 'confirm'}
      title={deleteConfirmState.isAlert ? '提示' : '确认删除'}
      message={deleteConfirmState.message}
      confirmLabel={deleteConfirmState.isAlert ? '知道了' : '删除'}
      cancelLabel="取消"
      isDangerous={!deleteConfirmState.isAlert}
      onConfirm={deleteConfirmState.onConfirm || (() => setDeleteConfirmState({ isOpen: false, message: '', onConfirm: null }))}
      onCancel={() => setDeleteConfirmState({ isOpen: false, message: '', onConfirm: null })}
    />

    {menuState && createPortal(
      <div
        className="source-menu-dropdown"
        style={{
          position: 'fixed',
          top: menuState.rect.bottom + 4,
          left: menuState.rect.right - 120,
          zIndex: 999999,
          width: 'fit-content',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {menuState.items.map((it, i) => (
          <button
            key={i}
            className={it.danger ? 'danger' : ''}
            disabled={it.disabled}
            onClick={it.action}
          >{it.label}</button>
        ))}
      </div>,
      globalThis.document.body
    )}
    </>
  )
}

export default DocumentInfo

