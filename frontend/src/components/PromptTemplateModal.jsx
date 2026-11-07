import { useState, useEffect } from 'react'
// 复用 RightPanel 的笔记弹窗样式，不需要单独的 CSS

// 常用Emoji图标供选择
const EMOJI_OPTIONS = [
    '🧠', '🎯', '⚖️', '💡', '📝', '🔍', '📊', '🎨',
    '💬', '🔔', '📌', '✨', '🚀', '💎', '🎯', '📚'
]

export default function PromptTemplateModal({
    isOpen,
    onClose,
    onSave,
    onDelete,
    template = null, // null = 创建新模板, object = 编辑现有模板
    existingCount = 0,
    maxCount = 6
}) {
    const [title, setTitle] = useState('')
    const [icon, setIcon] = useState('📝')
    const [prompt, setPrompt] = useState('')
    const [showEmojiPicker, setShowEmojiPicker] = useState(false)
    const [isSaving, setIsSaving] = useState(false)
    const [isDeleting, setIsDeleting] = useState(false)

    // 编辑模式：加载模板内容
    useEffect(() => {
        if (template) {
            setTitle(template.title || '')
            setIcon(template.icon || '📝')
            setPrompt(template.prompt || '')
        } else {
            setTitle('')
            setIcon('📝')
            setPrompt('')
        }
    }, [template])

    const isEditMode = !!template
    const canCreate = !isEditMode && existingCount < maxCount

    const handleSave = async () => {
        if (!title.trim() || !prompt.trim()) {
            alert('请填写标题和提示词内容')
            return
        }

        setIsSaving(true)
        try {
            await onSave({
                id: template?.id,
                title: title.trim(),
                icon,
                prompt: prompt.trim()
            })
            onClose()
        } catch (err) {
            console.error('保存失败:', err)
            alert('保存失败: ' + (err.message || '未知错误'))
        } finally {
            setIsSaving(false)
        }
    }

    const handleDelete = async () => {
        if (!template?.id) return

        if (!confirm('确定要删除这个提示词模板吗？')) return

        setIsDeleting(true)
        try {
            await onDelete(template.id)
            onClose()
        } catch (err) {
            console.error('删除失败:', err)
            alert('删除失败: ' + (err.message || '未知错误'))
        } finally {
            setIsDeleting(false)
        }
    }

    if (!isOpen) return null

    return (
        // 复用 note-preview-overlay 样式
        <div className="note-preview-overlay" onClick={onClose}>
            <div className="note-preview-modal" onClick={e => e.stopPropagation()}>
                <div className="note-preview-content">
                    {/* 工具栏 - 复用 note-dialog-toolbar */}
                    <div className="note-dialog-toolbar">
                        <h3 className="note-dialog-title">
                            {isEditMode ? '编辑提示词模板' : '创建提示词模板'}
                        </h3>
                        <div className="note-dialog-actions">
                            {isEditMode && (
                                <button
                                    className="preview-cancel-btn slim ptm-delete-btn"
                                    onClick={handleDelete}
                                    disabled={isDeleting}
                                >
                                    {isDeleting ? '删除中...' : '删除'}
                                </button>
                            )}
                            <button
                                className="preview-cancel-btn slim"
                                onClick={onClose}
                            >
                                取消
                            </button>
                            <button
                                className="preview-save-btn primary slim"
                                onClick={handleSave}
                                disabled={isSaving || (!isEditMode && !canCreate)}
                            >
                                {isSaving ? '保存中...' : '保存'}
                            </button>
                        </div>
                    </div>

                    {/* 数量提示 */}
                    {!isEditMode && (
                        <div className="ptm-count-hint">
                            已有 {existingCount}/{maxCount} 个模板
                            {existingCount >= maxCount && (
                                <span className="ptm-count-hint-error">
                                    （已达上限，请先删除现有模板）
                                </span>
                            )}
                        </div>
                    )}

                    {/* 图标和标题 - 标签平行 */}
                    <div className="note-form-group ptm-row">
                        <div className="ptm-icon-block">
                            <label className="note-form-label ptm-inline-label">图标</label>
                            <div className="ptm-emoji-picker-wrap">
                                <button
                                    type="button"
                                    onClick={() => setShowEmojiPicker(!showEmojiPicker)}
                                    className="ptm-emoji-trigger"
                                >
                                    {icon}
                                </button>
                                {showEmojiPicker && (
                                    <div className="ptm-emoji-menu">
                                        {EMOJI_OPTIONS.map((emoji, idx) => (
                                            <button
                                                key={idx}
                                                type="button"
                                                onClick={() => {
                                                    setIcon(emoji)
                                                    setShowEmojiPicker(false)
                                                }}
                                                className="ptm-emoji-option"
                                                style={icon === emoji ? { background: '#e0f2fe' } : undefined}
                                            >
                                                {emoji}
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>
                        <div className="ptm-title-block">
                            <label className="note-form-label ptm-inline-label ptm-inline-label-fixed">标题</label>
                            <input
                                type="text"
                                className="note-form-input ptm-title-input"
                                value={title}
                                onChange={e => setTitle(e.target.value)}
                                maxLength={20}
                            />
                        </div>
                    </div>

                    {/* 提示词内容 */}
                    <div className="note-form-group ptm-prompt-group">
                        <label className="note-form-label">提示词内容</label>
                        <textarea
                            value={prompt}
                            onChange={e => setPrompt(e.target.value)}
                            className="ptm-prompt-textarea"
                        />
                    </div>
                </div>
            </div>
        </div>
    )
}
