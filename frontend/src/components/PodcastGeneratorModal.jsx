import { useState, useEffect } from 'react'
import { apiRequest } from '../utils/api'
import { parseTaskErrorResponse, normalizeTaskErrorMessage } from '../utils/taskError'
import AnimatedModal from './ui/AnimatedModal'
import './PPTGeneratorModal.css' // Reuse modal styles

const PODCAST_STYLES = [
    { id: 'fun', name: '轻松有趣', desc: '两个朋友聊天，通俗易懂', emoji: '🎉' },
    { id: 'serious', name: '专业深度', desc: '严肃学术探讨，逻辑严密', emoji: '🧐' },
    { id: 'summary', name: '简明摘要', desc: '快速概括要点，节省时间', emoji: '📝' }
]

function PodcastGeneratorModal({ isOpen, onClose, documentId, initialTitle, onTaskCreated }) {
    const [title, setTitle] = useState('播客')
    const [selectedStyle, setSelectedStyle] = useState('fun')
    const [language, setLanguage] = useState('zh')
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [error, setError] = useState('')

    useEffect(() => {
        if (isOpen) {
            setTitle('播客')
            setSelectedStyle('fun')
            setLanguage('zh')
            setError('')
            setIsSubmitting(false)
        }
    }, [isOpen, initialTitle])

    const handleGenerate = async () => {
        if (!documentId) return

        setError('')
        setIsSubmitting(true)

        try {
            const res = await apiRequest('/api/podcast/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    document_id: documentId,
                    title: title,
                    style: selectedStyle,
                    language: language
                })
            })
            if (!res.ok) throw new Error(await parseTaskErrorResponse(res, '提交失败'))
            const data = await res.json()

            if (onTaskCreated) {
                onTaskCreated(data.task_id)
            }
            onClose()
        } catch (e) {
            setIsSubmitting(false)
            setError(normalizeTaskErrorMessage(e.message, '提交失败'))
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
                <h2>播客生成</h2>
                <p>将文档转化为双人对话播客</p>
            </div>

            <div className="modal-body">
                <div className="step-config">
                    <div className="options">
                        {/* Title Input Removed - Defaulting to '播客' */}

                        {/* Style Selection */}
                        <div className="style-section-label">对话风格</div>
                        <div className="style-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                            {PODCAST_STYLES.map(s => (
                                <div
                                    key={s.id}
                                    className={`style-card ${selectedStyle === s.id ? 'active' : ''}`}
                                    onClick={() => setSelectedStyle(s.id)}
                                >
                                    <span className="style-name" style={{ fontSize: '14px' }}>{s.emoji} {s.name}</span>
                                    <span className="style-desc">{s.desc}</span>
                                </div>
                            ))}
                        </div>

                        {/* Language Selection */}
                        <div className="style-section-label" style={{ marginTop: '16px' }}>语言选择</div>
                        <div className="style-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
                            <div
                                className={`style-card ${language === 'zh' ? 'active' : ''}`}
                                onClick={() => setLanguage('zh')}
                            >
                                <span className="style-name">🇨🇳 中文</span>
                                <span className="style-desc">Chinese</span>
                            </div>
                            <div
                                className={`style-card ${language === 'en' ? 'active' : ''}`}
                                onClick={() => setLanguage('en')}
                            >
                                <span className="style-name">🇺🇸 English</span>
                                <span className="style-desc">English</span>
                            </div>
                        </div>
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
                            marginTop: '24px',
                            opacity: isSubmitting ? 0.5 : 1,
                            cursor: isSubmitting ? 'not-allowed' : 'pointer'
                        }}
                    >
                        {isSubmitting ? '正在生成...' : '开始生成播客'}
                    </button>
                </div>
            </div>
        </AnimatedModal>
    )
}

export default PodcastGeneratorModal
