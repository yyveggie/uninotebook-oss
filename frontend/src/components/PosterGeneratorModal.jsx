import { useState, useEffect } from 'react'
import { apiRequest, getApiErrorMessage } from '../utils/api'
import AnimatedModal from './ui/AnimatedModal'
import './PPTGeneratorModal.css'

const STYLES = [
    { id: 'academic', name: '学术海报', desc: '标准学术排版 (Academic)', color: '#3b82f6' },
    { id: 'flat', name: '扁平简约', desc: '现代简约风格 (Flat)', color: '#10b981' },
    { id: 'business', name: '商务科技', desc: '专业干练的科技风格', color: '#64748b' },
    { id: 'creative', name: '创意设计', desc: '色彩丰富的创意风格', color: '#8b5cf6' }
]

const DENSITIES = [
    { id: 'medium', name: '标准', desc: '图文平衡' },
    { id: 'sparse', name: '疏朗', desc: '留白较多' },
    { id: 'dense', name: '紧凑', desc: '信息量大' }
]

function PosterGeneratorModal({ isOpen, onClose, documentId, initialTitle, onTaskCreated }) {
    const [step, setStep] = useState(documentId ? 'config' : 'upload')
    const [file, setFile] = useState(null)
    const [title, setTitle] = useState(initialTitle || '')
    const [selectedStyle, setSelectedStyle] = useState('academic')
    const [customStyleDesc, setCustomStyleDesc] = useState('')
    const [density, setDensity] = useState('medium')

    const [, setTaskId] = useState(null)
    const [progress, setProgress] = useState(0)
    const [status] = useState('')
    const [error, setError] = useState('')
    const [posterUrl, setPosterUrl] = useState('')
    const [isSubmitting, setIsSubmitting] = useState(false)

    useEffect(() => {
        if (isOpen) {
            setStep(documentId ? 'config' : 'upload')
            setFile(null)
            setTitle(initialTitle || '')
            setTaskId(null)
            setProgress(0)
            setError('')
            setPosterUrl('')
            setCustomStyleDesc('')
            setIsSubmitting(false)
        }
    }, [isOpen, documentId, initialTitle])

    const handleGenerate = async () => {
        if (!file && !documentId) {
            setError('请上传 PDF 文件或选择现有文档')
            return
        }

        setError('')
        setIsSubmitting(true)

        const formData = new FormData()
        if (file) formData.append('file', file)
        if (documentId) formData.append('document_id', documentId)

        formData.append('title', title)
        const finalStyle = customStyleDesc.trim() ? customStyleDesc.trim() : selectedStyle
        formData.append('style', finalStyle)
        formData.append('density', density)

        try {
            const res = await apiRequest('/api/poster/generate', {
                method: 'POST',
                body: formData
            })
            const data = await res.json()
            if (!res.ok) throw new Error(getApiErrorMessage(data, '提交失败'))

            setTaskId(data.id)
            if (onTaskCreated) {
                onTaskCreated(data.id)
            } else {
                setStep('processing')
                setProgress(5)
            }
        } catch (e) {
            setIsSubmitting(false)
            let msg = e.message
            setError(msg)
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
                <h2>海报生成器</h2>
                <p></p>
            </div>

            <div className="modal-body">
                {step === 'processing' && (
                    <div className="step-processing">
                        <div className="spinner"></div>
                        <h3>正在设计海报布局...</h3>
                        <p>AI 正在分析论文核心贡献...</p>
                        <div className="progress-bar">
                            <div className="progress-fill" style={{ width: `${progress}%` }}></div>
                        </div>
                        <p className="status-text">{status} ({progress}%)</p>
                    </div>
                )}

                {step === 'result' && (
                    <div className="step-result">
                        <h3>海报生成完成!</h3>
                        <button className="download-btn" onClick={() => window.open(posterUrl, '_blank')}>
                            下载海报
                        </button>
                        <button className="secondary-btn" onClick={onClose}>
                            关闭
                        </button>
                    </div>
                )}

                {(step === 'upload' || step === 'config') && (
                    <div className="step-config">
                        {/* Density Selection */}
                        <div className="options">
                            <div className="style-section-label">内容密度</div>
                            <div className="style-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                                {DENSITIES.map(d => (
                                    <div
                                        key={d.id}
                                        className={`style-card ${density === d.id ? 'active' : ''}`}
                                        onClick={() => setDensity(d.id)}
                                    >
                                        <span className="style-name">{d.name}</span>
                                        <span className="style-desc">{d.desc}</span>
                                    </div>
                                ))}
                            </div>

                            {/* Style Selection */}
                            <div className="style-section-label">视觉风格</div>
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
                                placeholder="请输入您想要的风格，如：清新文艺、赛博朋克、复古风..."
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

                        {error && (
                            <div className="error-msg" style={{
                                background: '#fef2f2',
                                color: '#dc2626',
                                padding: '12px 16px',
                                borderRadius: '8px',
                                marginBottom: '16px',
                                fontSize: '14px'
                            }}>
                                {error}
                            </div>
                        )}

                        <button
                            className="generate-btn"
                            onClick={handleGenerate}
                            disabled={isSubmitting}
                            style={{
                                opacity: isSubmitting ? 0.5 : 1,
                                cursor: isSubmitting ? 'not-allowed' : 'pointer'
                            }}
                        >
                            {isSubmitting ? '提交中...' : '开始制作海报'}
                        </button>
                    </div>
                )}
            </div>
        </AnimatedModal>
    )
}

export default PosterGeneratorModal

