/**
 * Skeleton 骨架屏组件
 * 用于替代 "加载中..." 文字，提供更现代的加载体验
 * 
 * 统一设计规范：
 * - 主文本高度: 14px
 * - 次要文本高度: 12px
 * - 间距: 8px
 */
import React from 'react'
import './Skeleton.css'

/**
 * 基础骨架元素
 * @param {string} variant - 变体: 'text' | 'circular' | 'rectangular'
 * @param {string} width - 宽度
 * @param {string} height - 高度
 * @param {string} className - 额外的类名
 */
export const Skeleton = ({
    variant = 'text',
    width,
    height,
    className = '',
    style = {}
}) => {
    const baseClass = `skeleton skeleton-${variant}`

    return (
        <div
            className={`${baseClass} ${className}`}
            style={{
                width: width || (variant === 'text' ? '100%' : undefined),
                height: height || (variant === 'text' ? '14px' : undefined),
                ...style
            }}
        />
    )
}

/**
 * 列表项骨架 - 用于会话历史等列表
 * 统一：主文本 14px，次要文本 12px
 */
export const SkeletonListItem = ({ lines = 2 }) => {
    return (
        <div className="skeleton-list-item">
            <div className="skeleton-list-item-content">
                <Skeleton variant="text" width="70%" height="14px" />
                {lines >= 2 && (
                    <Skeleton variant="text" width="40%" height="12px" style={{ opacity: 0.7 }} />
                )}
            </div>
        </div>
    )
}

/**
 * 历史对话骨架 - 用于左侧历史对话列表
 */
export const SkeletonSessionList = ({ count = 3 }) => {
    return (
        <div className="skeleton-session-list">
            {Array.from({ length: count }).map((_, i) => (
                <SkeletonListItem key={i} lines={2} />
            ))}
        </div>
    )
}

/**
 * 关键词骨架 - 用于关键词编辑区域
 */
export const SkeletonKeywords = ({ count = 4 }) => {
    return (
        <div className="skeleton-keywords">
            {Array.from({ length: count }).map((_, i) => (
                <Skeleton
                    key={i}
                    variant="rectangular"
                    width={`${50 + Math.random() * 40}px`}
                    height="28px"
                    className="skeleton-keyword-chip"
                />
            ))}
        </div>
    )
}

/**
 * 聊天消息骨架 - 用于中间聊天区域加载
 * 统一：所有骨架条 14px 高
 */
export const SkeletonChatMessage = ({ isAi = true }) => {
    return (
        <div className={`skeleton-chat-message ${isAi ? 'ai' : 'user'}`}>
            <div className="skeleton-chat-bubble">
                <Skeleton variant="text" width="90%" height="14px" />
                <Skeleton variant="text" width="60%" height="14px" />
                {isAi && (
                    <Skeleton variant="text" width="75%" height="14px" />
                )}
            </div>
        </div>
    )
}

/**
 * 笔记/记录骨架 - 用于右侧"我的记录"区域
 * 🔧 优化：添加图标占位符，与实际列表项布局一致
 */
export const SkeletonNotesList = ({ count = 2 }) => {
    return (
        <div className="skeleton-notes-list">
            {Array.from({ length: count }).map((_, i) => (
                <div key={i} className="skeleton-list-item" style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                    {/* 左侧图标占位 */}
                    <Skeleton variant="circular" width="20px" height="20px" style={{ marginTop: '2px', flexShrink: 0 }} />
                    {/* 右侧内容 */}
                    <div className="skeleton-list-item-content" style={{ flex: 1 }}>
                        <Skeleton variant="text" width="70%" height="14px" />
                        <Skeleton variant="text" width="40%" height="12px" style={{ opacity: 0.7 }} />
                    </div>
                </div>
            ))}
        </div>
    )
}




/**
 * 段落批注区域骨架
 * 🔧 优化：模拟文档预览效果，添加更多行
 */
export const SkeletonAnnotation = () => {
    return (
        <div className="skeleton-annotation">
            <Skeleton variant="text" width="100%" height="14px" />
            <Skeleton variant="text" width="95%" height="14px" />
            <Skeleton variant="text" width="88%" height="14px" />
            <Skeleton variant="text" width="92%" height="14px" />
            <Skeleton variant="text" width="60%" height="14px" />
        </div>
    )
}

export default Skeleton
