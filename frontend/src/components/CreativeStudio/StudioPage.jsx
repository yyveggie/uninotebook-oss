
import React, { useState, useEffect, useRef, useCallback } from 'react'
import Editor from '@monaco-editor/react'
import { apiGet, apiRequest } from '../../utils/api'
import { parseTaskErrorResponse, normalizeTaskErrorMessage } from '../../utils/taskError'
import useUIStore from '../../stores/uiStore'
import ConfirmDialog from '../ConfirmDialog'
import './StudioPage.css'

const TEXT_EXTENSIONS = ['.txt', '.md', '.tex']
const ACCEPTED_EXTENSIONS = '.txt,.md,.tex'
const AUTOSAVE_DELAY_MS = 1000
// 保存失败后自动重试的间隔，用完后一直按最后一档重试
const SAVE_RETRY_DELAYS_MS = [2000, 5000, 15000, 30000]
const SAVE_STATUS_LABELS = { dirty: '未保存', saving: '保存中…', error: '保存失败' }
const UNTITLED_NAME = '未命名文档'
// 与后端 routes/editor.py 保持一致：单次 AI 编辑的选区上限；流式输出中途失败时以 \u0000 开头的标记结尾
const MAX_SELECTION_CHARS = 6000
const STREAM_ERROR_SIGNAL = '\u0000'
const CONTINUE_ACTION = 'continue'
const HUMANIZE_ACTION = 'humanize'

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '')
const AI_SHORTCUT_LABEL = IS_MAC ? '⌘K' : 'Ctrl+K'

const showInfo = (message) => useUIStore.getState().showToast(message, { type: 'info' })
const showError = (message) => useUIStore.getState().showToast(message, { type: 'error' })

const getFileExtension = (filename) => (filename || '').split('.').pop()?.toLowerCase() || ''

const getEditorLanguage = (filename) => {
    const map = { tex: 'latex', md: 'markdown' }
    return map[getFileExtension(filename)] || 'plaintext'
}

const DOWNLOAD_MIME_TYPES = { md: 'text/markdown', tex: 'application/x-tex' }

const nextUntitledFilename = (files) => {
    const names = new Set(files.map(f => f.filename))
    if (!names.has(`${UNTITLED_NAME}.md`)) return `${UNTITLED_NAME}.md`
    let n = 2
    while (names.has(`${UNTITLED_NAME} ${n}.md`)) n += 1
    return `${UNTITLED_NAME} ${n}.md`
}

// 读本地文本文件：带 UTF-16 BOM 的按 UTF-16 解码，合法 UTF-8（含带 BOM 的）照常解码，
// 其余按 GB18030（GBK 的超集）解码，兼容老版 Windows 记事本等保存的中文文件
const decodeTextFile = (buffer) => {
    const bytes = new Uint8Array(buffer)
    if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le').decode(bytes)
    if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be').decode(bytes)
    try {
        return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    } catch {
        return new TextDecoder('gb18030').decode(bytes)
    }
}

// 模型输出统一成 \n 换行：预览按行定位，混入 \r 会让行数对不上
const normalizeAIText = (text) => text.replace(/\r/g, '')

const QUICK_ACTIONS = [
    { key: 'translate_en', label: '翻译为英文', icon: '🌐' },
    { key: 'translate_zh', label: '翻译为中文', icon: '🇨🇳' },
    { key: 'expand', label: '扩写', icon: '📝' },
    { key: 'condense', label: '缩写', icon: '✂️' },
    { key: HUMANIZE_ACTION, label: '去AI味', icon: '🪄' },
    { key: 'fix_grammar', label: '修复语法', icon: '🔧' },
    { key: 'academic', label: '学术化', icon: '🎓' },
    { key: 'casual', label: '口语化', icon: '💬' },
    { key: CONTINUE_ACTION, label: '续写', icon: '➡️' },
]

const MAX_CHINESE_CHARS = 500
const MAX_ENGLISH_WORDS = 500
const MIN_HUMANIZE_CHARS = 10

const countChineseChars = (text) => (text.match(/[一-鿿]/g) || []).length
const countEnglishWords = (text) => (text.match(/\b[a-zA-Z]+(?:'[a-zA-Z]+)?\b/g) || []).length

const getLimitError = (label, text) => {
    const chineseChars = countChineseChars(text)
    const englishWords = countEnglishWords(text)
    if (chineseChars > MAX_CHINESE_CHARS || englishWords > MAX_ENGLISH_WORDS) {
        return `${label}最多支持 ${MAX_CHINESE_CHARS} 个中文字符或 ${MAX_ENGLISH_WORDS} 个英文单词（当前：中文 ${chineseChars}，英文 ${englishWords}）`
    }
    return ''
}

// 发请求前检查选区：续写可以只有光标（用光标前的内容作上文），其余操作都要先选中文本
const getSelectionError = ({ actionKey, selectionText, textBefore }) => {
    if (!selectionText.trim()) {
        if (actionKey !== CONTINUE_ACTION) return '请先在编辑器中选中文本'
        if (!textBefore.trim()) return '光标前还没有内容，先写几句再续写吧'
    }
    if (selectionText.length > MAX_SELECTION_CHARS) {
        return `选中内容过长（${selectionText.length} 字），单次最多 ${MAX_SELECTION_CHARS} 字，请分段处理`
    }
    if (actionKey === HUMANIZE_ACTION) {
        if (selectionText.trim().length < MIN_HUMANIZE_CHARS) return `去 AI 味至少需要选中 ${MIN_HUMANIZE_CHARS} 个字`
        return getLimitError('待去 AI 味文本', selectionText)
    }
    return ''
}

const AIPromptWidget = ({ onSubmit, onClose, position, isLoading }) => {
    const [input, setInput] = useState('')
    const inputRef = useRef(null)
    const widgetRef = useRef(null)

    useEffect(() => {
        if (inputRef.current) inputRef.current.focus()
    }, [])

    useEffect(() => {
        const handleClickOutside = (e) => {
            if (widgetRef.current && !widgetRef.current.contains(e.target)) {
                onClose()
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [onClose])

    const handleSubmit = (e) => {
        e.preventDefault()
        if (input.trim() && !isLoading) onSubmit({ instruction: input })
    }

    return (
        <div className="ai-prompt-widget" ref={widgetRef} style={{ top: position.top, left: position.left }}>
            <form onSubmit={handleSubmit}>
                <div className="ai-prompt-input-container">
                    <input
                        ref={inputRef}
                        type="text"
                        className="ai-prompt-input"
                        placeholder="输入编辑指令... (如：改写为正式语气、翻译为英文)"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === 'Escape') onClose()
                        }}
                        disabled={isLoading}
                    />
                    <button type="submit" className="ai-prompt-submit" disabled={isLoading || !input.trim()}>
                        {isLoading ? '⏳' : '✨'}
                    </button>
                </div>
            </form>
        </div>
    )
}

const StudioPage = () => {
    const [openFiles, setOpenFiles] = useState([])
    const [activeFileId, setActiveFileId] = useState(null)
    const [isLoading, setIsLoading] = useState(false)
    // 当前 AI 请求能否停止：流式编辑可以，去 AI 味是一次性请求，停止也照样计费，不提供
    const [canCancelAI, setCanCancelAI] = useState(false)
    const [showChat] = useState(true)
    const [customInstruction, setCustomInstruction] = useState('')
    const [pendingEdit, setPendingEdit] = useState(null)
    const [runningActionKey, setRunningActionKey] = useState(null)
    // fileId -> 'dirty' | 'saving' | 'error'，没有记录即已保存
    const [saveStates, setSaveStates] = useState({})
    const [confirmState, setConfirmState] = useState(null)
    const [renameValue, setRenameValue] = useState(null)

    const editorRef = useRef(null)
    const monacoRef = useRef(null)
    const decorationsRef = useRef([])
    // 编辑器回调（Cmd+K、自动保存、切换文件）里读最新状态用 ref，避免闭包拿到旧值
    const isLoadingRef = useRef(false)
    const pendingEditRef = useRef(null)
    const aiAbortRef = useRef(null)
    const mountedRef = useRef(true)
    const renameCancelledRef = useRef(false)
    // 每个草稿对应一个独立的 Monaco 模型：正文只存在模型里，不再另存一份会过期的副本，
    // 撤销历史也按文件隔离。路径带本次进入页面的随机前缀，重新进入时不会复用上次残留的模型
    const modelPrefixRef = useRef(`inmemory://studio/${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}/`)
    // 待保存的修改：fileId -> { content, timer }，按文件分别防抖，切换文件不会丢掉上一个文件的修改
    const pendingSavesRef = useRef(new Map())
    // 每个文件连续保存失败的次数：决定重试间隔，以及是否需要提示
    const saveFailuresRef = useRef(new Map())

    const modelPathFor = (fileId) => `${modelPrefixRef.current}${fileId}`
    const fileIdOfModel = (model) => {
        const uri = model?.uri?.toString() || ''
        return uri.startsWith(modelPrefixRef.current) ? Number(uri.slice(modelPrefixRef.current.length)) : null
    }

    const [showPrompt, setShowPrompt] = useState(false)
    const [promptPosition, setPromptPosition] = useState({ top: 100, left: 100 })
    const [selectionRange, setSelectionRange] = useState(null)

    const [panelWidth, setPanelWidth] = useState(300)
    const handlePanelDragStart = useCallback((e) => {
        e.preventDefault()
        e.stopPropagation()
        const startX = e.clientX
        const startWidth = panelWidth
        document.body.style.userSelect = 'none'
        document.body.style.cursor = 'col-resize'
        const onMove = (ev) => {
            const delta = startX - ev.clientX
            setPanelWidth(Math.max(220, Math.min(500, startWidth + delta)))
        }
        const onUp = () => {
            document.body.style.userSelect = ''
            document.body.style.cursor = ''
            document.removeEventListener('mousemove', onMove)
            document.removeEventListener('mouseup', onUp)
        }
        document.addEventListener('mousemove', onMove)
        document.addEventListener('mouseup', onUp)
    }, [panelWidth])

    const [showImportPicker, setShowImportPicker] = useState(false)
    const [platformDocs, setPlatformDocs] = useState([])
    const [isLoadingDocs, setIsLoadingDocs] = useState(false)
    const [importSearch, setImportSearch] = useState('')
    const [deletingFileId, setDeletingFileId] = useState(null)

    useEffect(() => {
        let ignore = false
        const loadDrafts = async () => {
            try {
                // 不读前端缓存：缓存里的旧内容一旦打开就会被当作最新版本继续编辑、保存，覆盖较新的修改
                const res = await apiGet('/api/editor/drafts', { cacheMode: 'no-cache' })
                if (ignore) return
                if (res.success && res.data) {
                    setOpenFiles(res.data)
                    if (res.data.length > 0) {
                        setActiveFileId(res.data[0].id)
                    }
                }
            } catch (err) {
                console.error('Failed to load drafts', err)
                if (!ignore) showError('加载草稿失败，请刷新页面重试')
            }
        }
        loadDrafts()
        return () => {
            ignore = true
        }
    }, [])

    const setSaveState = (fileId, state) => {
        setSaveStates(prev => {
            if ((prev[fileId] || null) === state) return prev
            const next = { ...prev }
            if (state) next[fileId] = state
            else delete next[fileId]
            return next
        })
    }

    // 立即把某个文件待保存的修改写回服务器（keepalive：页面关闭时请求也能发出）。
    // 失败时内容留在队列里：显示「保存失败」并按退避间隔自动重试，离开页面时浏览器也会提示
    const flushSave = async (fileId, { keepalive = false } = {}) => {
        const saves = pendingSavesRef.current
        const entry = saves.get(fileId)
        if (!entry) return
        clearTimeout(entry.timer)
        saves.delete(fileId)
        setSaveState(fileId, 'saving')

        let failure = null
        try {
            const response = await apiRequest(`/api/editor/drafts/${fileId}`, {
                method: 'PUT',
                body: JSON.stringify({ content: entry.content }),
                keepalive,
            })
            if (!response.ok) {
                failure = {
                    retry: response.status >= 500 || response.status === 408 || response.status === 429,
                    message: response.status === 404
                        ? '草稿已被删除，无法继续保存，请先复制内容'
                        : await parseTaskErrorResponse(response, '草稿保存失败'),
                }
            }
        } catch (err) {
            failure = { retry: true, message: normalizeTaskErrorMessage(err?.message, '网络异常，草稿保存失败') }
        }
        if (!mountedRef.current) return

        if (!failure) {
            saveFailuresRef.current.delete(fileId)
            setSaveState(fileId, saves.has(fileId) ? 'dirty' : null)
            return
        }

        const failures = saveFailuresRef.current.get(fileId) || 0
        saveFailuresRef.current.set(fileId, failures + 1)
        // 期间又有新修改时以新的为准，由它自己的计时器负责保存
        if (!saves.has(fileId)) {
            const delay = SAVE_RETRY_DELAYS_MS[Math.min(failures, SAVE_RETRY_DELAYS_MS.length - 1)]
            saves.set(fileId, {
                content: entry.content,
                timer: failure.retry ? setTimeout(() => flushSave(fileId), delay) : null,
            })
        }
        setSaveState(fileId, 'error')
        if (failures === 0) showError(failure.retry ? `${failure.message}，正在自动重试` : failure.message)
    }

    const scheduleSave = (fileId, content) => {
        const saves = pendingSavesRef.current
        clearTimeout(saves.get(fileId)?.timer)
        saves.set(fileId, { content, timer: setTimeout(() => flushSave(fileId), AUTOSAVE_DELAY_MS) })
        setSaveState(fileId, 'dirty')
    }

    const retrySave = (fileId) => {
        saveFailuresRef.current.delete(fileId)
        flushSave(fileId)
    }

    const cancelSave = (fileId) => {
        clearTimeout(pendingSavesRef.current.get(fileId)?.timer)
        pendingSavesRef.current.delete(fileId)
        saveFailuresRef.current.delete(fileId)
        setSaveState(fileId, null)
    }

    useEffect(() => {
        mountedRef.current = true
        const saves = pendingSavesRef.current
        const flushAll = (options) => [...saves.keys()].forEach((fileId) => flushSave(fileId, options))
        // 关闭或刷新页面时还有没保存的修改：尽力发出，并让浏览器提示确认离开
        const handleBeforeUnload = (event) => {
            if (saves.size === 0) return
            flushAll({ keepalive: true })
            event.preventDefault()
            event.returnValue = ''
        }
        window.addEventListener('beforeunload', handleBeforeUnload)
        return () => {
            window.removeEventListener('beforeunload', handleBeforeUnload)
            mountedRef.current = false
            // 离开创作空间：停止进行中的生成，立即保存未保存的修改，
            // 并释放本次创建的编辑器模型（当前模型由编辑器组件自己释放）
            aiAbortRef.current?.abort()
            flushAll()
            const currentModel = editorRef.current?.getModel()
            monacoRef.current?.editor.getModels().forEach((model) => {
                if (model !== currentModel && fileIdOfModel(model) !== null) model.dispose()
            })
        }
    // 只在挂载 / 卸载时执行；flushSave 只读写 ref，用哪次渲染的版本都一样
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    // 用户输入（以及采纳 AI 建议）按文件防抖保存；预览期间插入、撤掉的预览文本不保存
    const handleEditorChange = (value) => {
        const fileId = fileIdOfModel(editorRef.current?.getModel())
        if (fileId === null || pendingEditRef.current) return
        scheduleSave(fileId, value ?? '')
    }

    const disposeModel = (fileId) => {
        const monaco = monacoRef.current
        monaco?.editor.getModel(monaco.Uri.parse(modelPathFor(fileId)))?.dispose()
    }

    const activeFile = openFiles.find(f => f.id === activeFileId) || null
    const activeSaveState = activeFile ? saveStates[activeFile.id] : null

    // 离开当前文件前的检查：AI 正在处理时不能离开（结果会落进别的文件）；
    // 有尚未采纳的建议时先确认，确认后撤掉预览再离开，避免它混进正文
    const leaveActiveFile = (proceed) => {
        if (isLoadingRef.current) {
            showInfo('AI 正在处理当前文件，请等它完成或先停止生成')
            return
        }
        if (pendingEditRef.current) {
            setConfirmState({
                title: '放弃 AI 建议？',
                message: '当前有尚未采纳的 AI 建议，切换文件会放弃这条建议。',
                confirmLabel: '放弃并切换',
                onConfirm: () => {
                    handleRejectEdit()
                    proceed()
                },
            })
            return
        }
        proceed()
    }

    const switchToFile = (fileId) => {
        if (fileId === activeFileId) return
        leaveActiveFile(() => setActiveFileId(fileId))
    }

    const openFileInEditor = (file) => {
        setOpenFiles(prev => (prev.some(f => f.id === file.id) ? prev : [...prev, file]))
        switchToFile(file.id)
    }

    const handleTabSelect = (fileId) => {
        if (openFiles.some(f => f.id === fileId)) switchToFile(fileId)
    }

    const deleteDraft = async (fileId) => {
        setDeletingFileId(fileId)
        try {
            const response = await apiRequest(`/api/editor/drafts/${fileId}`, {
                method: 'DELETE',
            })

            if (!response.ok) {
                throw new Error(await parseTaskErrorResponse(response, '删除草稿失败，请稍后重试'))
            }

            // 文件已删除：丢掉它还没保存的修改，免得再写回去
            cancelSave(fileId)
            if (pendingEditRef.current?.fileId === fileId) clearPendingEdit()
            setOpenFiles(prev => prev.filter(f => f.id !== fileId))
            setActiveFileId(current => {
                if (current !== fileId) return current
                const remaining = openFiles.filter(f => f.id !== fileId)
                return remaining.length > 0 ? remaining[remaining.length - 1].id : null
            })
            disposeModel(fileId)
        } catch (err) {
            console.error('Failed to delete draft', err)
            showError(normalizeTaskErrorMessage(err.message, '删除草稿失败，请稍后重试'))
        } finally {
            setDeletingFileId(null)
        }
    }

    const handleTabClose = (e, file) => {
        e.stopPropagation()
        if (deletingFileId === file.id) return
        if (isLoadingRef.current) {
            showInfo('AI 正在处理当前文件，请等它完成或先停止生成')
            return
        }
        setConfirmState({
            title: '删除草稿',
            message: `确定删除「${file.filename}」吗？删除后无法恢复。`,
            confirmLabel: '删除',
            onConfirm: () => deleteDraft(file.id),
        })
    }

    const startRename = () => {
        if (!activeFile) return
        renameCancelledRef.current = false
        setRenameValue(activeFile.filename)
    }

    // Enter 和失焦都走这里（Enter 只是让输入框失焦），Esc 取消
    const finishRename = async () => {
        const value = renameValue
        const file = activeFile
        setRenameValue(null)
        if (renameCancelledRef.current || value === null || !file) return
        const filename = value.trim()
        if (!filename || filename === file.filename) return
        try {
            const response = await apiRequest(`/api/editor/drafts/${file.id}`, {
                method: 'PUT',
                body: JSON.stringify({ filename }),
            })
            if (!response.ok) throw new Error(await parseTaskErrorResponse(response, '重命名失败，请稍后重试'))
            const data = await response.json()
            const saved = data?.data?.filename || filename
            setOpenFiles(prev => prev.map(f => (f.id === file.id ? { ...f, filename: saved } : f)))
        } catch (err) {
            showError(normalizeTaskErrorMessage(err.message, '重命名失败，请稍后重试'))
        }
    }

    const handleDownload = () => {
        const model = editorRef.current?.getModel()
        if (!model || !activeFile || pendingEditRef.current) return
        const ext = getFileExtension(activeFile.filename)
        const blob = new Blob([model.getValue()], { type: `${DOWNLOAD_MIME_TYPES[ext] || 'text/plain'};charset=utf-8` })
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = activeFile.filename
        document.body.appendChild(link)
        link.click()
        link.remove()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
    }

    const handleEditorDidMount = (editor, monaco) => {
        editorRef.current = editor
        monacoRef.current = monaco

        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK, () => {
            if (pendingEditRef.current || isLoadingRef.current) return
            const selection = editor.getSelection()
            if (selection && !selection.isEmpty()) {
                const p = editor.getScrolledVisiblePosition(selection.getStartPosition())
                setPromptPosition(p ? { top: Math.max(8, p.top + 50), left: p.left + 50 } : { top: 100, left: 100 })
                setSelectionRange(selection)
                setShowPrompt(true)
            }
        })
    }

    const [editBtnPos, setEditBtnPos] = useState(null)

    const clearPendingEdit = () => {
        if (editorRef.current && decorationsRef.current.length > 0) {
            decorationsRef.current = editorRef.current.deltaDecorations(decorationsRef.current, [])
        }
        pendingEditRef.current = null
        setPendingEdit(null)
        setEditBtnPos(null)
    }

    // 停止 / 采纳 / 放弃按钮跟在预览最后一行的下面
    const updateEditBtnPos = () => {
        const editor = editorRef.current
        const pending = pendingEditRef.current
        const model = editor?.getModel()
        if (!pending || !model || pending.model !== model) return
        try {
            const line = pending.anchorLine + pending.previewLineCount
            const pos = editor.getScrolledVisiblePosition({ lineNumber: line, column: model.getLineMaxColumn(line) })
            if (pos) setEditBtnPos({ top: pos.top + pos.height + 6, left: 60 })
        } catch { /* ignore */ }
    }

    useEffect(() => {
        if (!pendingEdit || !editorRef.current) {
            setEditBtnPos(null)
            return
        }
        updateEditBtnPos()
        const disposable = editorRef.current.onDidScrollChange(updateEditBtnPos)
        return () => disposable.dispose()
    }, [pendingEdit])

    const applyDiffDecorations = (pending) => {
        const editor = editorRef.current
        const monaco = monacoRef.current
        if (!editor || !monaco || editor.getModel() !== pending.model || pending.previewLineCount === 0) return
        try {
            const { model, origRange, mode, anchorLine, previewLineCount } = pending
            const lastLine = anchorLine + previewLineCount
            const decorations = [{
                range: new monaco.Range(anchorLine + 1, 1, lastLine, model.getLineMaxColumn(lastLine)),
                options: { className: 'ai-edit-new', isWholeLine: true },
            }]
            // 替换类操作标出将被替换的原文；续写保留原文，不标
            if (mode === 'replace') {
                decorations.push({
                    range: new monaco.Range(
                        origRange.startLineNumber, origRange.startColumn,
                        origRange.endLineNumber, origRange.endColumn
                    ),
                    options: { className: 'ai-edit-original', inlineClassName: 'ai-edit-original-inline' },
                })
            }
            decorationsRef.current = editor.deltaDecorations(decorationsRef.current, decorations)
        } catch { /* ignore */ }
    }

    // AI 结果以预览形式插在选区所在行的下方：生成过程中逐段追加，完成后等用户采纳或放弃。
    // 预览不进撤销历史、不保存；生成和预览期间编辑器只读、模型只会被这里改动，
    // 所以预览的位置可以由锚点（选区所在行的行尾）和行数直接算出
    const startPreview = (model, range, mode) => {
        const anchorLine = range.endLineNumber
        const anchorColumn = model.getLineMaxColumn(anchorLine)
        const pending = {
            fileId: fileIdOfModel(model),
            model,
            mode,
            origRange: {
                startLineNumber: range.startLineNumber,
                startColumn: range.startColumn,
                endLineNumber: range.endLineNumber,
                endColumn: range.endColumn,
            },
            anchorLine,
            anchorColumn,
            previewLineCount: 0,
            text: '',
            streaming: true,
        }
        // 先登记，插入预览触发的 onChange 才会跳过自动保存
        pendingEditRef.current = pending
        model.applyEdits([{
            range: { startLineNumber: anchorLine, startColumn: anchorColumn, endLineNumber: anchorLine, endColumn: anchorColumn },
            text: '\n',
        }])
        pending.previewLineCount = 1
        setPendingEdit({ ...pending })
        applyDiffDecorations(pending)
        editorRef.current?.revealLineInCenterIfOutsideViewport(anchorLine + 1)
        return pending
    }

    const appendPreview = (pending, chunk) => {
        const { model } = pending
        if (!chunk || model.isDisposed()) return
        const lastLine = pending.anchorLine + pending.previewLineCount
        const column = model.getLineMaxColumn(lastLine)
        model.applyEdits([{
            range: { startLineNumber: lastLine, startColumn: column, endLineNumber: lastLine, endColumn: column },
            text: chunk,
        }])
        pending.previewLineCount += (chunk.match(/\n/g) || []).length
        pending.text += chunk
        applyDiffDecorations(pending)
        updateEditBtnPos()
        if (editorRef.current?.getModel() === model) {
            editorRef.current.revealLine(pending.anchorLine + pending.previewLineCount)
        }
    }

    const removePreview = (pending) => {
        const { model, anchorLine, anchorColumn, previewLineCount } = pending
        if (model.isDisposed() || previewLineCount === 0) return
        const lastLine = anchorLine + previewLineCount
        model.applyEdits([{
            range: { startLineNumber: anchorLine, startColumn: anchorColumn, endLineNumber: lastLine, endColumn: model.getLineMaxColumn(lastLine) },
            text: '',
        }])
        pending.previewLineCount = 0
    }

    const replacePreviewText = (pending, text) => {
        removePreview(pending)
        const { model, anchorLine, anchorColumn } = pending
        model.applyEdits([{
            range: { startLineNumber: anchorLine, startColumn: anchorColumn, endLineNumber: anchorLine, endColumn: anchorColumn },
            text: `\n${text}`,
        }])
        pending.previewLineCount = text.split('\n').length
        pending.text = text
        applyDiffDecorations(pending)
    }

    const discardPreview = (pending) => {
        removePreview(pending)
        if (pendingEditRef.current === pending) clearPendingEdit()
    }

    const setAIRunning = (running, actionKey = null, cancellable = false) => {
        isLoadingRef.current = running
        setIsLoading(running)
        setRunningActionKey(running ? actionKey || 'custom' : null)
        setCanCancelAI(running && cancellable)
    }

    const handleCancelAI = () => aiAbortRef.current?.abort()

    // 发请求前的检查（同步）：不满足时提示并返回 null，调用方据此决定是否清空输入框
    const prepareAIEdit = ({ instruction, actionKey, range }) => {
        const activeRange = range || selectionRange
        const model = editorRef.current?.getModel()
        if (!model || !activeRange || isLoadingRef.current) return null
        // 上一条建议还没处理就发起新请求，旧预览会留在正文里
        if (pendingEditRef.current) {
            showInfo('请先采纳或放弃当前的 AI 建议')
            return null
        }

        const selectionText = model.getValueInRange(activeRange)
        const startOffset = model.getOffsetAt(activeRange.getStartPosition())
        const endOffset = model.getOffsetAt(activeRange.getEndPosition())
        const fullText = model.getValue()
        const textBefore = fullText.substring(Math.max(0, startOffset - 1000), startOffset)
        const textAfter = fullText.substring(endOffset, Math.min(fullText.length, endOffset + 1000))

        const inputError = getSelectionError({ actionKey, selectionText, textBefore })
        if (inputError) {
            showInfo(inputError)
            setShowPrompt(false)
            return null
        }
        return { model, range: activeRange, instruction, actionKey, selectionText, textBefore, textAfter }
    }

    const runAIEdit = async ({ model, range, instruction, actionKey, selectionText, textBefore, textAfter }) => {
        const isContinue = actionKey === CONTINUE_ACTION
        const controller = new AbortController()
        aiAbortRef.current = controller
        // 请求期间编辑器只读、不能切换文件：结果放回发起请求时的位置
        setAIRunning(true, actionKey, actionKey !== HUMANIZE_ACTION)
        let pending = null

        try {
            if (actionKey === HUMANIZE_ACTION) {
                const polishResponse = await apiRequest('/api/polish/rewrite', {
                    method: 'POST',
                    body: JSON.stringify({
                        source_text: selectionText,
                        reference_text: '',
                        mode: 'academic',
                        task_type: HUMANIZE_ACTION,
                    }),
                })
                if (!polishResponse.ok) {
                    throw new Error(await parseTaskErrorResponse(polishResponse, '去 AI 味失败，请稍后重试'))
                }
                const polishData = await polishResponse.json()
                const rewritten = polishData?.success ? polishData?.data?.rewritten_text : ''
                if (!rewritten) {
                    throw new Error(polishData?.detail || polishData?.message || '去 AI 味失败，请稍后重试')
                }
                if (model.isDisposed() || !mountedRef.current) return
                pending = startPreview(model, range, 'replace')
                appendPreview(pending, normalizeAIText(rewritten))
            } else {
                // 走 apiRequest：access token 过期时自动刷新重试，本地版不带 Authorization；
                // 流式响应不能被请求调度器合并给多个调用方，故关闭去重
                const response = await apiRequest('/api/editor/ai-edit', {
                    method: 'POST',
                    body: JSON.stringify({
                        selection: selectionText,
                        instruction: instruction || '',
                        action_key: actionKey || null,
                        context_before: textBefore,
                        context_after: textAfter,
                    }),
                    signal: controller.signal,
                }, { dedupeWindowMs: 0 })

                if (!response.ok) {
                    throw new Error(await parseTaskErrorResponse(response, 'AI 编辑失败，请稍后重试'))
                }
                if (model.isDisposed() || !mountedRef.current) return

                // 续写插在选区之后（没有选区时插在光标处），其余操作替换选区
                pending = startPreview(model, range, isContinue ? 'insert' : 'replace')
                const reader = response.body.getReader()
                // 同一个解码器 + stream 模式：被网络分块切开的多字节汉字才能正确拼回
                const decoder = new TextDecoder()
                let interrupted = false
                const consume = (text) => {
                    if (interrupted || !text) return
                    // 后端中途失败（已退款）会以 \u0000 开头的标记结尾，标记之后都不是正文
                    const signalAt = text.indexOf(STREAM_ERROR_SIGNAL)
                    if (signalAt >= 0) {
                        interrupted = true
                        text = text.slice(0, signalAt)
                    }
                    appendPreview(pending, normalizeAIText(text))
                }
                while (true) {
                    const { done, value } = await reader.read()
                    if (done) break
                    consume(decoder.decode(value, { stream: true }))
                }
                consume(decoder.decode())
                if (interrupted) throw new Error('AI 生成中断，本次消耗的积分已退还，请重试')
            }

            if (pending.model.isDisposed()) return
            // 续写时模型偶尔会把续写起点再抄一遍：只保留新内容
            if (isContinue && selectionText && pending.text.startsWith(selectionText)) {
                replacePreviewText(pending, pending.text.slice(selectionText.length).replace(/^\n+/, ''))
            }
            if (!pending.text.trim()) throw new Error('AI 没有返回内容，请重试')
            pending.streaming = false
            setPendingEdit({ ...pending })
            updateEditBtnPos()
        } catch (err) {
            if (pending) discardPreview(pending)
            if (!mountedRef.current) return
            if (err?.name === 'AbortError') showInfo('已停止生成')
            else showError(normalizeTaskErrorMessage(err?.message, 'AI 编辑失败，请稍后重试'))
        } finally {
            if (aiAbortRef.current === controller) aiAbortRef.current = null
            setAIRunning(false)
            setShowPrompt(false)
        }
    }

    const startAIEdit = (args) => {
        const request = prepareAIEdit(args)
        if (request) runAIEdit(request)
        return Boolean(request)
    }

    const handleAcceptEdit = () => {
        const pending = pendingEditRef.current
        if (!pending || pending.streaming) return
        const { model, origRange, mode, text } = pending
        removePreview(pending)
        // 撤掉预览之后再清预览状态：接下来的修改才是真正的修改，进撤销历史并触发自动保存
        pendingEditRef.current = null
        if (!model.isDisposed()) {
            const range = mode === 'insert'
                ? {
                    startLineNumber: origRange.endLineNumber,
                    startColumn: origRange.endColumn,
                    endLineNumber: origRange.endLineNumber,
                    endColumn: origRange.endColumn,
                }
                : origRange
            model.pushStackElement()
            model.pushEditOperations([], [{ range, text }], () => null)
            model.pushStackElement()
        }
        clearPendingEdit()
    }

    const handleRejectEdit = () => {
        const pending = pendingEditRef.current
        if (!pending) return
        if (pending.streaming) {
            handleCancelAI()
            return
        }
        discardPreview(pending)
    }

    const handleOpenImportPicker = async () => {
        setShowImportPicker(true)
        setImportSearch('')
        // 每次打开都重新拉取（期间先显示上次的列表），刚上传的文档也能马上导入
        if (platformDocs.length === 0) setIsLoadingDocs(true)
        try {
            const res = await apiGet('/api/history/documents', { cacheMode: 'no-cache' })
            if (res.success && res.data) {
                setPlatformDocs(res.data)
            }
        } catch (err) {
            console.error('Failed to fetch documents', err)
            showError('加载文档列表失败，请稍后重试')
        } finally {
            setIsLoadingDocs(false)
        }
    }

    const handleImportPlatformDoc = async (doc) => {
        setShowImportPicker(false)
        const existing = openFiles.find(f => f.source_document_id === doc.document_id)
        if (existing) {
            handleTabSelect(existing.id)
            return
        }
        if (isLoadingRef.current) {
            showInfo('AI 正在处理当前文件，请等它完成后再导入')
            return
        }

        try {
            const res = await apiGet(`/api/editor/document/${doc.document_id}/content`)
            if (!res.success) throw new Error('加载文档内容失败')
            const response = await apiRequest('/api/editor/drafts', {
                method: 'POST',
                body: JSON.stringify({
                    filename: doc.display_name || doc.filename,
                    content: res.content || '',
                    source_type: 'platform_import',
                    source_document_id: doc.document_id,
                }),
            })
            if (!response.ok) throw new Error(await parseTaskErrorResponse(response, '导入文档失败'))
            const data = await response.json()
            if (data.success && data.data) {
                openFileInEditor(data.data)
            }
        } catch (err) {
            console.error(err)
            showError(normalizeTaskErrorMessage(err.message, '导入文档出错，请稍后重试'))
        }
    }

    const filteredDocs = importSearch.trim()
        ? platformDocs.filter(d =>
            (d.display_name || d.filename || '').toLowerCase().includes(importSearch.toLowerCase())
        )
        : platformDocs

    const handleCreateDraft = async () => {
        if (isLoadingRef.current) {
            showInfo('AI 正在处理当前文件，请等它完成后再新建')
            return
        }
        try {
            const response = await apiRequest('/api/editor/drafts', {
                method: 'POST',
                body: JSON.stringify({ filename: nextUntitledFilename(openFiles), content: '' }),
            })
            if (!response.ok) throw new Error(await parseTaskErrorResponse(response, '新建文档失败'))
            const data = await response.json()
            if (data.success && data.data) {
                openFileInEditor(data.data)
            }
        } catch (err) {
            console.error('Failed to create draft', err)
            showError(normalizeTaskErrorMessage(err.message, '新建文档失败，请稍后重试'))
        }
    }

    const fileInputRef = useRef(null)

    const handleImportClick = () => {
        if (isLoadingRef.current) {
            showInfo('AI 正在处理当前文件，请等它完成后再上传')
            return
        }
        if (fileInputRef.current) fileInputRef.current.click()
    }

    const handleFileChange = (e) => {
        const file = e.target.files?.[0]
        if (fileInputRef.current) fileInputRef.current.value = ''
        if (!file) return

        const ext = '.' + getFileExtension(file.name)
        if (!TEXT_EXTENSIONS.includes(ext)) {
            showError(`暂不支持 ${ext} 格式，请使用 .txt、.md 或 .tex`)
            return
        }

        const reader = new FileReader()
        reader.onload = async (ev) => {
            try {
                const response = await apiRequest('/api/editor/drafts', {
                    method: 'POST',
                    body: JSON.stringify({ filename: file.name, content: decodeTextFile(ev.target.result), source_type: 'local' }),
                })
                if (!response.ok) throw new Error(await parseTaskErrorResponse(response, '上传文件失败'))
                const data = await response.json()
                if (data.success && data.data) {
                    openFileInEditor(data.data)
                }
            } catch (err) {
                console.error('Failed to create draft from file', err)
                showError(normalizeTaskErrorMessage(err.message, '上传文件失败，请稍后重试'))
            }
        }
        reader.onerror = () => showError('文件读取失败')
        reader.readAsArrayBuffer(file)
    }

    const handleChatSubmit = (e) => {
        e.preventDefault()
        if (!customInstruction.trim() || isLoading || !editorRef.current) return
        const selection = editorRef.current.getSelection()
        if (!selection || selection.isEmpty()) {
            showInfo('请先在编辑器中选中文本')
            return
        }
        setSelectionRange(selection)
        if (startAIEdit({ instruction: customInstruction.trim(), range: selection })) {
            setCustomInstruction('')
        }
    }

    const editorLanguage = activeFile ? getEditorLanguage(activeFile.filename) : 'markdown'

    return (
        <div className="studio-page">
            <input
                type="file"
                ref={fileInputRef}
                style={{ display: 'none' }}
                accept={ACCEPTED_EXTENSIONS}
                onChange={handleFileChange}
            />
            {/* Sidebar */}
            <div className="studio-sidebar">
                <div className="studio-sidebar-header">
                    <div className="studio-sidebar-actions">
                        <button className="studio-action-btn" onClick={handleOpenImportPicker} title="从已上传的文档中导入">
                            导入已有
                        </button>
                        <button className="studio-action-btn" onClick={handleImportClick} title="从本地上传文件 (.txt, .md, .tex 等)">
                            上传文件
                        </button>
                        <button className="studio-action-btn" onClick={handleCreateDraft} title="创建空白文档">
                            创建空文档
                        </button>
                    </div>
                </div>
                <div className="studio-doc-list">
                    {openFiles.length === 0 ? (
                        <div className="studio-doc-list-empty">暂无打开的文件</div>
                    ) : (
                        openFiles.map(file => {
                            const saveState = saveStates[file.id]
                            return (
                                <div
                                    key={file.id}
                                    className={`studio-doc-item ${activeFileId === file.id ? 'active' : ''}`}
                                    onClick={() => handleTabSelect(file.id)}
                                    title={file.filename}
                                >
                                    <span className="doc-item-name">{file.filename}</span>
                                    {saveState && (
                                        <span className={`doc-item-save-dot ${saveState}`} title={SAVE_STATUS_LABELS[saveState]} />
                                    )}
                                    <button
                                        className="doc-item-close"
                                        onClick={(e) => handleTabClose(e, file)}
                                        title="删除"
                                        disabled={deletingFileId === file.id}
                                    >
                                        ×
                                    </button>
                                </div>
                            )
                        })
                    )}
                </div>
            </div>

            {showImportPicker && (
                <div className="import-picker-overlay" onClick={() => setShowImportPicker(false)}>
                    <div className="import-picker" onClick={e => e.stopPropagation()}>
                        <div className="import-picker-header">
                            <h3>导入已有文档</h3>
                            <button className="import-picker-close" onClick={() => setShowImportPicker(false)}>×</button>
                        </div>
                        <input
                            className="import-picker-search"
                            type="text"
                            placeholder="搜索文档名称..."
                            value={importSearch}
                            onChange={e => setImportSearch(e.target.value)}
                            autoFocus
                        />
                        <div className="import-picker-list">
                            {isLoadingDocs ? (
                                <div className="import-picker-loading">加载中...</div>
                            ) : filteredDocs.length === 0 ? (
                                <div className="import-picker-loading">{importSearch ? '未找到匹配的文档' : '暂无已上传的文档'}</div>
                            ) : (
                                filteredDocs.map(doc => (
                                    <div
                                        key={doc.document_id}
                                        className="import-picker-item"
                                        onClick={() => handleImportPlatformDoc(doc)}
                                    >
                                        <span className="import-picker-item-name">{doc.display_name || doc.filename}</span>
                                        <span className="import-picker-item-type">{doc.file_type}</span>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </div>
            )}

            {/* Main Editor Area */}
            <div className="studio-main">
                {!activeFile ? (
                    <div className="studio-empty-state">
                        <div className="empty-state-content">
                            <div className="empty-state-icon">📝</div>
                            <h3>创作空间</h3>
                            <p>选中文本后按 {AI_SHORTCUT_LABEL} 调出 AI 助手，支持翻译、扩写、润色等操作</p>
                            <div className="empty-state-actions">
                                <button className="empty-action-btn" onClick={handleOpenImportPicker}>
                                    📥 导入已有文档
                                </button>
                                <button className="empty-action-btn" onClick={handleImportClick}>
                                    📂 上传本地文件
                                </button>
                                <button className="empty-action-btn primary" onClick={handleCreateDraft}>
                                    + 新建空白文档
                                </button>
                            </div>
                            <div className="empty-state-formats">
                                本地上传支持：.txt .md .tex
                            </div>
                        </div>
                    </div>
                ) : (
                    <div className="studio-workspace">
                        <div className="editor-container">
                            <div className="editor-toolbar">
                                {renameValue !== null ? (
                                    <input
                                        className="editor-filename-input"
                                        value={renameValue}
                                        onChange={(e) => setRenameValue(e.target.value)}
                                        onBlur={finishRename}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter') e.currentTarget.blur()
                                            if (e.key === 'Escape') {
                                                renameCancelledRef.current = true
                                                e.currentTarget.blur()
                                            }
                                        }}
                                        maxLength={255}
                                        aria-label="文件名"
                                        autoFocus
                                    />
                                ) : (
                                    <button type="button" className="editor-filename" onClick={startRename} title="点击重命名">
                                        {activeFile.filename}
                                    </button>
                                )}
                                <span className={`editor-save-status ${activeSaveState || 'saved'}`}>
                                    {SAVE_STATUS_LABELS[activeSaveState] || '已保存'}
                                </span>
                                {activeSaveState === 'error' && (
                                    <button type="button" className="editor-save-retry" onClick={() => retrySave(activeFile.id)}>
                                        重试
                                    </button>
                                )}
                                <div className="editor-toolbar-spacer" />
                                <button
                                    type="button"
                                    className="editor-toolbar-btn"
                                    onClick={handleDownload}
                                    disabled={isLoading || !!pendingEdit}
                                    title={isLoading || pendingEdit ? '请先处理当前的 AI 建议' : '下载为文件'}
                                >
                                    下载
                                </button>
                            </div>
                            <div className="editor-body">
                                <Editor
                                    height="100%"
                                    path={modelPathFor(activeFile.id)}
                                    defaultValue={activeFile.content || ''}
                                    language={editorLanguage}
                                    onChange={handleEditorChange}
                                    onMount={handleEditorDidMount}
                                    theme="light"
                                    options={{
                                        padding: { top: 24, bottom: 24 },
                                        fontSize: 15,
                                        fontFamily: "'Menlo', 'Monaco', 'Courier New', monospace",
                                        minimap: { enabled: false },
                                        wordWrap: 'on',
                                        lineNumbers: 'off',
                                        renderLineHighlight: 'none',
                                        scrollBeyondLastLine: false,
                                        readOnly: isLoading || !!pendingEdit,
                                    }}
                                />
                                {pendingEdit && editBtnPos && (
                                    <div
                                        className="ai-edit-inline-actions"
                                        style={{ top: editBtnPos.top, left: editBtnPos.left }}
                                    >
                                        {pendingEdit.streaming ? (
                                            <button onClick={handleCancelAI} className="ai-edit-btn reject" disabled={!canCancelAI}>
                                                停止生成
                                            </button>
                                        ) : (
                                            <>
                                                <button onClick={handleRejectEdit} className="ai-edit-btn reject">放弃</button>
                                                <button onClick={handleAcceptEdit} className="ai-edit-btn accept">采纳</button>
                                            </>
                                        )}
                                    </div>
                                )}
                                {showPrompt && !pendingEdit && (
                                    <AIPromptWidget
                                        onSubmit={({ instruction }) => startAIEdit({ instruction })}
                                        onClose={() => setShowPrompt(false)}
                                        position={promptPosition}
                                        isLoading={isLoading}
                                    />
                                )}
                            </div>
                        </div>

                        {showChat && (
                            <>
                            <div className="panel-resize-handle" onMouseDown={handlePanelDragStart} />
                            <div className="studio-edit-panel" style={{ width: panelWidth }}>
                                <div className="edit-panel-body">
                                    <div className="edit-panel-hint">
                                        选中文本后使用快捷操作或输入指令（也可按 {AI_SHORTCUT_LABEL}）；续写在光标处也能用
                                    </div>
                                    <div className="edit-panel-actions">
                                        {QUICK_ACTIONS.map(action => {
                                            const isRunning = runningActionKey === action.key
                                            return (
                                                <button
                                                    key={action.key}
                                                    className={`edit-panel-action-btn ${isRunning ? 'running' : ''}`}
                                                    disabled={isRunning ? !canCancelAI : isLoading || !!pendingEdit}
                                                    title={isRunning && canCancelAI ? '停止生成' : undefined}
                                                    onClick={() => {
                                                        if (isRunning) {
                                                            handleCancelAI()
                                                            return
                                                        }
                                                        const selection = editorRef.current?.getSelection()
                                                        if (!selection) return
                                                        setSelectionRange(selection)
                                                        startAIEdit({ actionKey: action.key, range: selection })
                                                    }}
                                                >
                                                    <span className="action-icon">
                                                        {isRunning ? <span className="edit-panel-spinner small" /> : action.icon}
                                                    </span>
                                                    <span className="action-label">
                                                        {isRunning ? (canCancelAI ? '停止' : '运行中') : action.label}
                                                    </span>
                                                </button>
                                            )
                                        })}
                                    </div>
                                </div>
                                <form className="edit-panel-chat-input" onSubmit={handleChatSubmit}>
                                    <div className="edit-chat-box">
                                        <textarea
                                            className="edit-chat-textarea"
                                            placeholder="输入编辑指令..."
                                            value={customInstruction}
                                            onChange={(e) => setCustomInstruction(e.target.value)}
                                            disabled={isLoading}
                                            rows={1}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' && !e.shiftKey) {
                                                    e.preventDefault()
                                                    e.target.form.requestSubmit()
                                                }
                                            }}
                                            onInput={(e) => {
                                                e.target.style.height = 'auto'
                                                e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
                                            }}
                                        />
                                        {isLoading ? (
                                            <button
                                                type="button"
                                                className="edit-chat-send"
                                                onClick={handleCancelAI}
                                                disabled={!canCancelAI}
                                                title={canCancelAI ? '停止生成' : 'AI 正在处理'}
                                            >
                                                {canCancelAI ? (
                                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2" /></svg>
                                                ) : (
                                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12a9 9 0 1 1-6.2-8.56" /></svg>
                                                )}
                                            </button>
                                        ) : (
                                            <button
                                                type="submit"
                                                className="edit-chat-send"
                                                disabled={!!pendingEdit || !customInstruction.trim()}
                                            >
                                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 2L11 13" /><path d="M22 2l-7 20-4-9-9-4z" /></svg>
                                            </button>
                                        )}
                                    </div>
                                </form>
                            </div>
                            </>
                        )}
                    </div>
                )}
            </div>

            <ConfirmDialog
                isOpen={!!confirmState}
                title={confirmState?.title}
                message={confirmState?.message}
                confirmLabel={confirmState?.confirmLabel}
                onConfirm={() => {
                    const action = confirmState?.onConfirm
                    setConfirmState(null)
                    action?.()
                }}
                onCancel={() => setConfirmState(null)}
            />
        </div>
    )
}

export default StudioPage
