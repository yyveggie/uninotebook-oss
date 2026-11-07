import { useState, useRef, useEffect } from 'react'
import { apiRequest, getApiErrorMessage } from '../utils/api'
import AnimatedModal from './ui/AnimatedModal'
import './PPTGeneratorModal.css'

const STYLES = [
    { id: 'academic', name: '学术风格', desc: '严谨、清晰，适合论文汇报', color: '#3b82f6' },
    { id: 'business', name: '商务风格', desc: '专业、现代，适合商业演示', color: '#10b981' },
    { id: 'doraemon', name: '哆啦A梦', desc: '趣味、生动，适合创意展示', color: '#38bdf8' },
    { id: 'minimal', name: '极简白', desc: '干净、简单，通用风格', color: '#64748b' }
]

function PPTGeneratorModal({ isOpen, onClose, documentId, initialTitle, onTaskCreated }) {
    // If documentId is passed, we default to 'config' mode (skipping upload)
    const [step, setStep] = useState(documentId ? 'config' : 'upload') // upload, config, processing, result

    const [file, setFile] = useState(null)
    // If documentId is present, we assume the title is the document name initially, but allow edit.
    const [title, setTitle] = useState(initialTitle || '')
    const [selectedStyle, setSelectedStyle] = useState('academic')
    const [customStyleDesc, setCustomStyleDesc] = useState('') // 用户自定义风格描述
    const [mode, setMode] = useState('paper') // 'paper' (academic) or 'general' (books/reports)
    const [outputType, setOutputType] = useState('slides') // 'slides' or 'poster'
    const [, setTaskId] = useState(null)
    const [progress, setProgress] = useState(0)
    const [status] = useState('')
    const [error, setError] = useState('')
    const [pptUrl, setPptUrl] = useState('')
    const [isSubmitting, setIsSubmitting] = useState(false)

    const fileInputRef = useRef(null)

    // Reset on open
    useEffect(() => {
        if (isOpen) {
            setStep(documentId ? 'config' : 'upload')
            setFile(null)
            setTitle(initialTitle || '')
            setMode('paper')
            setOutputType('slides')
            setCustomStyleDesc('')
            setTaskId(null)
            setProgress(0)
            setError('')
            setPptUrl('')
            setIsSubmitting(false)
        }
    }, [isOpen, documentId, initialTitle])

    const handleFileSelect = (e) => {
        const f = e.target.files?.[0]
        if (f && f.name.toLowerCase().endsWith('.pdf')) {
            setFile(f)
            setTitle(f.name.replace('.pdf', ''))
            setError('')
        } else {
            setError('请选择 PDF 文件')
        }
    }

    const handleGenerate = async () => {
        // Validation: Must have file OR documentId
        if (!file && !documentId) {
            setError('请上传 PDF 文件或选择现有文档')
            return
        }

        setError('')
        setIsSubmitting(true)

        const formData = new FormData()
        if (file) { // Only append file if one is selected
            formData.append('file', file)
        }
        // 如果用户输入了自定义风格，优先使用；否则使用选中的风格
        const finalStyle = customStyleDesc.trim() ? customStyleDesc.trim() : selectedStyle
        formData.append('style', finalStyle)
        formData.append('language', 'zh') // 默认中文
        formData.append('mode', mode)
        formData.append('output_type', outputType)
        if (title) formData.append('title', title)
        if (documentId && !file) { // Only append document_id if no new file is uploaded
            formData.append('document_id', documentId)
        }

        try {
            const res = await apiRequest('/api/ppt/generate', {
                method: 'POST',
                body: formData // Content-Type header handled automatically
            })
            const data = await res.json()
            if (!res.ok) throw new Error(getApiErrorMessage(data, '提交失败'))

            setTaskId(data.task_id)
            if (onTaskCreated) {
                onTaskCreated(data.task_id)
            } else {
                // Fallback if no handler provided (legacy behavior)
                setStep('processing')
                setProgress(5)
            }
        } catch (e) {
            setIsSubmitting(false)
            // Unify error handling: Popup with friendly message if it's a quota/cost issue
            let msg = e.message
            alert(msg)
            setError('') // Clear inline error as we alerted
            // Stay in current step
        }
    }

    const downloadPPT = async () => {
        if (!pptUrl) return
        try {
            // Check if it's a full URL (e.g. from COS)
            if (pptUrl.startsWith('http')) {
                window.open(pptUrl, '_blank')
                return
            }

            // Otherwise, use backend proxy for local files
            const res = await apiRequest(pptUrl)
            if (!res.ok) throw new Error('下载失败')
            const blob = await res.blob()
            const url = window.URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = `${title || 'presentation'}${outputType === 'poster' ? '_poster' : ''}.zip`
            document.body.appendChild(a)
            a.click()
            window.URL.revokeObjectURL(url)
            document.body.removeChild(a)
        } catch (e) {
            setError("下载出错: " + e.message)
        }
    }

    return (
        <AnimatedModal
            isOpen={isOpen}
            onClose={onClose}
            size="md"
            className="ppt-modal-content"
        >
            <div className="modal-header">
                <h2>PPT 生成器</h2>
                <p></p>
            </div>

            <div className="modal-body">
                {step === 'upload' && !documentId && (
                    <div className="step-upload">
                        <div
                            className={`drop-zone ${file ? 'has-file' : ''}`}
                            onClick={() => fileInputRef.current?.click()}
                        >
                            <input
                                type="file"
                                ref={fileInputRef}
                                accept=".pdf"
                                onChange={handleFileSelect}
                                style={{ display: 'none' }}
                            />
                            {file ? (
                                <div className="file-info">
                                    <span className="name">{file.name}</span>
                                    <button onClick={(e) => { e.stopPropagation(); setFile(null); }}>更改</button>
                                </div>
                            ) : (
                                <div className="placeholder">
                                    <p>点击或拖拽 PDF 文件到这里</p>
                                    <div style={{ fontSize: '12px', color: '#999', marginTop: '8px' }}>
                                        (目前仅支持 PDF 格式)
                                    </div>
                                </div>
                            )}
                        </div>

                        <div className="options">

                            <div className="style-section-label">内容模式</div>
                            <div className="style-grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: '16px' }}>
                                <div
                                    className={`style-card ${mode === 'paper' ? 'active' : ''}`}
                                    onClick={() => setMode('paper')}
                                >
                                    <span className="style-name">学术模式</span>
                                    <span className="style-desc">适合论文 (摘要/方法/实验)</span>
                                </div>
                                <div
                                    className={`style-card ${mode === 'general' ? 'active' : ''}`}
                                    onClick={() => setMode('general')}
                                >
                                    <span className="style-name">通用模式</span>
                                    <span className="style-desc">适合研报/书籍/方案</span>
                                </div>
                            </div>

                            <div className="style-section-label">风格</div>
                            <div className="style-grid">
                                {STYLES.map(s => (
                                    <div
                                        key={s.id}
                                        className={`style-card ${selectedStyle === s.id ? 'active' : ''}`}
                                        onClick={() => setSelectedStyle(s.id)}
                                    >
                                        <span className="style-name">{s.name}</span>
                                        <span className="style-desc">{s.desc}</span>
                                    </div>
                                ))}
                            </div>
                            <div className="style-section-label" style={{ marginTop: '16px', fontSize: '12px', color: '#666' }}>或者输入自定义风格</div>
                            <input
                                type="text"
                                className="custom-style-input"
                                placeholder="请输入您想要的风格，如：科技感、赛博朋克、水彩手绘..."
                                value={customStyleDesc}
                                onChange={(e) => setCustomStyleDesc(e.target.value)}
                                style={{
                                    width: '100%',
                                    padding: '12px 16px',
                                    marginTop: '8px',
                                    border: customStyleDesc.trim() ? '2px solid #8b5cf6' : '1px solid #e5e7eb',
                                    borderRadius: '8px',
                                    fontSize: '14px',
                                    outline: 'none',
                                    transition: 'border-color 0.2s'
                                }}
                            />
                        </div>

                        {error && <div className="error-msg">{error}</div>}

                        <button
                            className="generate-btn"
                            disabled={isSubmitting}
                            style={{ opacity: isSubmitting ? 0.6 : 1, cursor: isSubmitting ? 'not-allowed' : 'pointer' }}
                            onClick={handleGenerate}
                        >
                            {isSubmitting ? '提交中...' : '开始生成'}
                        </button>
                    </div>
                )}

                {/* Config mode: Used when documentId is present (Clean view, no upload) */}
                {(step === 'config' || (step === 'upload' && documentId)) && (
                    <div className="step-config">


                        <div className="options">

                            <div className="style-section-label">内容模式</div>
                            <div className="style-grid" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: '16px' }}>
                                <div
                                    className={`style-card ${mode === 'paper' ? 'active' : ''}`}
                                    onClick={() => setMode('paper')}
                                >
                                    <span className="style-name">学术模式</span>
                                    <span className="style-desc">适合论文 (摘要/方法/实验)</span>
                                </div>
                                <div
                                    className={`style-card ${mode === 'general' ? 'active' : ''}`}
                                    onClick={() => setMode('general')}
                                >
                                    <span className="style-name">通用模式</span>
                                    <span className="style-desc">适合研报/书籍/方案</span>
                                </div>
                            </div>

                            <div className="style-section-label">风格</div>
                            <div className="style-grid">
                                {STYLES.map(s => (
                                    <div
                                        key={s.id}
                                        className={`style-card ${selectedStyle === s.id ? 'active' : ''}`}
                                        onClick={() => setSelectedStyle(s.id)}
                                    >
                                        <span className="style-name">{s.name}</span>
                                        <span className="style-desc">{s.desc}</span>
                                    </div>
                                ))}
                            </div>
                            <div className="style-section-label" style={{ marginTop: '16px', fontSize: '12px', color: '#666' }}>或者输入自定义风格</div>
                            <input
                                type="text"
                                className="custom-style-input"
                                placeholder="请输入您想要的风格，如：科技感、赛博朋克、水彩手绘..."
                                value={customStyleDesc}
                                onChange={(e) => setCustomStyleDesc(e.target.value)}
                                style={{
                                    width: '100%',
                                    padding: '12px 16px',
                                    marginTop: '8px',
                                    border: customStyleDesc.trim() ? '2px solid #8b5cf6' : '1px solid #e5e7eb',
                                    borderRadius: '8px',
                                    fontSize: '14px',
                                    outline: 'none',
                                    transition: 'border-color 0.2s'
                                }}
                            />
                        </div>

                        {error && <div className="error-msg">{error}</div>}

                        <button
                            className="generate-btn"
                            disabled={isSubmitting}
                            style={{ opacity: isSubmitting ? 0.6 : 1, cursor: isSubmitting ? 'not-allowed' : 'pointer' }}
                            onClick={handleGenerate}
                        >
                            {isSubmitting ? "提交中..." : (outputType === 'poster' ? "开始生成海报" : "开始生成 PPT")}
                        </button>
                    </div>
                )}


                {step === 'processing' && (
                    <div className="step-processing">
                        <div className="spinner"></div>
                        <h3>正在分析文档并生成 PPT...</h3>
                        <p>这可能需要几分钟，请耐心等待</p>
                        <div className="progress-bar">
                            <div className="progress-fill" style={{ width: `${progress}%` }}></div>
                        </div>
                        <p className="status-text">{status} ({progress}%)</p>
                    </div>
                )}

                {step === 'result' && (
                    <div className="step-result">
                        <h3>生成完成!</h3>
                        <p>您的演示文稿已准备就绪。</p>
                        <button className="download-btn" onClick={downloadPPT}>
                            下载 PPTX
                        </button>
                        <button className="secondary-btn" onClick={() => { setStep('upload'); setFile(null); }}>
                            再做一个
                        </button>
                    </div>
                )}
            </div>
        </AnimatedModal>
    )
}

export default PPTGeneratorModal

