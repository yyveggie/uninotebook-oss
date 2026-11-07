
import React from 'react'
import { SkeletonKeywords } from './Skeleton'
import { useKeyTermEditor } from '../hooks/useKeyTermEditor'

const KeyTermEditor = ({ documentId }) => {
    const {
        terms,
        newTerm,
        setNewTerm,
        isLoading,
        isSaving,
        isDirty,
        loadFailed,
        handleChangeTerm,
        handleRemove,
        handleAdd,
        handleSave
    } = useKeyTermEditor(documentId)

    const handleKeyDown = (e) => {
        if (e.key === 'Enter') {
            e.preventDefault()
            handleAdd()
        }
    }

    const disabled = !documentId || loadFailed

    return (
        <div className="summary-keywords-editor">
            <div className="summary-keywords-header">
                <div className="summary-keywords-title">
                    <span className="summary-keywords-icon">🧀</span>
                    <span>（可编辑）知识网络关键词，这会影响到你的知识网络构建</span>
                </div>
                <button
                    type="button"
                    className="summary-keywords-save-btn"
                    onClick={handleSave}
                    disabled={disabled || isSaving || !isDirty}
                >
                    {isSaving ? '保存中…' : loadFailed ? '加载失败' : isDirty ? '保存关键词' : '已同步'}
                </button>
            </div>
            {isLoading ? (
                <div className="summary-keywords-loading"><SkeletonKeywords count={4} /></div>
            ) : (
                <>
                    <div className="summary-keywords-chips">
                        {terms.length === 0 && (
                            <span className="summary-keywords-empty">当前没有关键词，先手动添加一些吧～</span>
                        )}
                        {terms.map((item, index) => {
                            const value = item.term || ''
                            // 粗略按字符数估算宽度：中文当作 2 个字符处理
                            const pseudoLength = value
                                ? value.replace(/[\u4e00-\u9fa5]/g, 'aa').length
                                : 0
                            // 使用 size 属性控制可见字符数，至少 2 个字符宽
                            const inputSize = Math.max(pseudoLength, 2)

                            return (
                                <div key={item.id ?? `new-${index}`} className="summary-keyword-chip">
                                    <input
                                        type="text"
                                        className="summary-keyword-input"
                                        value={value}
                                        onChange={(e) => handleChangeTerm(index, e.target.value)}
                                        placeholder="例如：人工智能 / Artificial Intelligence"
                                        size={inputSize}
                                    />
                                    <button
                                        type="button"
                                        className="summary-keyword-delete"
                                        onClick={() => handleRemove(index)}
                                        aria-label="删除关键词"
                                    >
                                        ×
                                    </button>
                                </div>
                            )
                        })}
                    </div>
                    <div className="summary-keywords-add-row">
                        <input
                            type="text"
                            className="summary-keywords-add-input"
                            value={newTerm}
                            onChange={(e) => setNewTerm(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder="新增关键词，回车快速添加"
                            disabled={disabled}
                        />
                        <button
                            type="button"
                            className="summary-keywords-add-btn"
                            onClick={handleAdd}
                            disabled={disabled}
                        >
                            添加
                        </button>
                    </div>
                </>
            )}
        </div>
    )
}

export default KeyTermEditor
