import React, { useState, useEffect, useRef, useCallback } from 'react';
import { motion as Motion, AnimatePresence } from 'framer-motion';
import { createPortal } from 'react-dom';
import './ChartCard.css';

/**
 * ChartCard - A consistent container for various charts (Mermaid, DataChart, etc.)
 * Provides: header with icon/title, subtitle, fullscreen toggle, zoom/pan, and unified styling.
 * 
 * @param {Object} props
 * @param {string} props.title - Main title
 * @param {string} props.icon - Emoji or icon element (default: 📊)
 * @param {string} props.subtitle - Optional description/overview
 * @param {React.ReactNode} props.children - The chart component to render
 * @param {Function} props.onFullscreenRender - Optional async function to return content for fullscreen (e.g. SVG string or React Node). If not provided, clones chart.
 * @param {boolean} props.allowFullscreen - Whether to show the fullscreen button (default: true)
 */
const ChartCard = ({
    title = '图表',
    subtitle,
    children,
    onFullscreenRender,
    allowFullscreen = true,
    minimal = false
}) => {
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [fullscreenContent, setFullscreenContent] = useState(null);
    const [isLoading, setIsLoading] = useState(false);

    // 🆕 缩放和平移状态
    const [scale, setScale] = useState(1);
    const [position, setPosition] = useState({ x: 0, y: 0 });
    const [isDragging, setIsDragging] = useState(false);
    const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
    const contentRef = useRef(null);

    const handleFullscreen = async (e) => {
        e.stopPropagation();
        setIsLoading(true);
        try {
            if (onFullscreenRender) {
                // If specific render logic is provided (e.g., for Mermaid to re-render SVG)
                const content = await onFullscreenRender();
                setFullscreenContent(content);
            } else {
                // Default: Clone the children for fullscreen
                setFullscreenContent(children);
            }
            // 重置缩放和位置
            setScale(1);
            setPosition({ x: 0, y: 0 });
            setIsFullscreen(true);
        } finally {
            setIsLoading(false);
        }
    };

    const closeFullscreen = () => {
        setIsFullscreen(false);
        setFullscreenContent(null);
        setScale(1);
        setPosition({ x: 0, y: 0 });
    };

    // 🆕 缩放控制函数
    const handleZoomIn = useCallback(() => {
        setScale(prev => Math.min(prev * 1.25, 5)); // 最大 5x
    }, []);

    const handleZoomOut = useCallback(() => {
        setScale(prev => Math.max(prev / 1.25, 0.25)); // 最小 0.25x
    }, []);

    const handleResetZoom = useCallback(() => {
        setScale(1);
        setPosition({ x: 0, y: 0 });
    }, []);

    // 🆕 鼠标滚轮缩放
    const handleWheel = useCallback((e) => {
        if (!isFullscreen) return;
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        setScale(prev => Math.min(Math.max(prev * delta, 0.25), 5));
    }, [isFullscreen]);

    // 🆕 拖拽平移
    const handleMouseDown = useCallback((e) => {
        if (e.button !== 0) return; // 只响应左键
        setIsDragging(true);
        setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y });
    }, [position]);

    const handleMouseMove = useCallback((e) => {
        if (!isDragging) return;
        setPosition({
            x: e.clientX - dragStart.x,
            y: e.clientY - dragStart.y
        });
    }, [isDragging, dragStart]);

    const handleMouseUp = useCallback(() => {
        setIsDragging(false);
    }, []);

    // Close on Escape key
    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.key === 'Escape' && isFullscreen) {
                closeFullscreen();
            }
            // 🆕 键盘快捷键缩放
            if (isFullscreen) {
                if (e.key === '+' || e.key === '=') handleZoomIn();
                if (e.key === '-') handleZoomOut();
                if (e.key === '0') handleResetZoom();
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [isFullscreen, handleZoomIn, handleZoomOut, handleResetZoom]);

    return (
        <>
            <div className={`chart-card ${minimal ? 'minimal' : 'default'}`}>
                {/* Header - Use inline flex to guarantee layout */}
                <div className={`chart-card-header ${minimal ? 'minimal' : 'default'} ${!minimal ? 'with-toolbar' : ''}`}>
                    {/* Title Area */}
                    <div className="chart-card-title-row">
                        <div className="chart-card-title-stack">
                            <h3 className="chart-card-title">
                                {title}
                            </h3>
                            {subtitle && (
                                <p className="chart-card-subtitle" title={subtitle}>
                                    {subtitle}
                                </p>
                            )}
                        </div>
                    </div>

                    {/* Toolbar - Absolute top right */}
                    {allowFullscreen && !minimal && (
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                e.preventDefault();
                                handleFullscreen(e);
                            }}
                            onMouseDown={(e) => {
                                e.stopPropagation();
                                e.preventDefault();
                            }}
                            disabled={isLoading}
                            title="全屏查看"
                            className="chart-fullscreen-btn"
                        >
                            {isLoading ? (
                                <span className="animate-spin block chart-fullscreen-loading">↻</span>
                            ) : (
                                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                                </svg>
                            )}
                        </button>
                    )}
                </div>

                {/* Content */}
                <div
                    className={`chart-content ${minimal ? 'is-minimal' : 'is-default'} ${minimal && allowFullscreen ? 'is-clickable' : ''}`}
                    onClick={minimal && allowFullscreen ? handleFullscreen : undefined}
                >
                    {children}
                </div>
            </div>

            {/* Fullscreen Modal */}
            <AnimatePresence>
                {isFullscreen && (
                    <ModalPortal>
                        <Motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="chart-modal-overlay"
                            onClick={closeFullscreen}
                        >
                            <Motion.div
                                initial={{ scale: 0.95, opacity: 0 }}
                                animate={{ scale: 1, opacity: 1 }}
                                exit={{ scale: 0.95, opacity: 0 }}
                                onClick={(e) => e.stopPropagation()}
                                className="chart-modal-panel"
                            >
                                {/* Modal Header with Zoom Controls */}
                                <div className="chart-modal-header">
                                    <h2 className="chart-modal-title">{title}</h2>

                                    {/* 🆕 缩放控制工具栏 */}
                                    <div className="chart-modal-toolbar">
                                        <button
                                            onClick={handleZoomOut}
                                            title="缩小 (-)"
                                            className="chart-modal-btn chart-modal-btn-zoom"
                                        >
                                            −
                                        </button>
                                        <span className="chart-modal-zoom-value">
                                            {Math.round(scale * 100)}%
                                        </span>
                                        <button
                                            onClick={handleZoomIn}
                                            title="放大 (+)"
                                            className="chart-modal-btn chart-modal-btn-zoom"
                                        >
                                            +
                                        </button>
                                        <button
                                            onClick={handleResetZoom}
                                            title="重置 (0)"
                                            className="chart-modal-btn chart-modal-btn-reset"
                                        >
                                            重置
                                        </button>
                                        <div className="chart-modal-separator" />
                                        <button
                                            onClick={closeFullscreen}
                                            title="关闭 (Esc)"
                                            className="chart-modal-btn chart-modal-btn-close"
                                        >
                                            关闭
                                        </button>
                                    </div>
                                </div>

                                {/* Modal Content - Zoomable & Pannable */}
                                <div
                                    ref={contentRef}
                                    onWheel={handleWheel}
                                    onMouseDown={handleMouseDown}
                                    onMouseMove={handleMouseMove}
                                    onMouseUp={handleMouseUp}
                                    onMouseLeave={handleMouseUp}
                                    className={`chart-modal-content ${isDragging ? 'is-dragging' : 'is-idle'}`}
                                >
                                    <div
                                        className="chart-modal-transform-wrap"
                                        style={{
                                            transform: `translate(${position.x}px, ${position.y}px) scale(${scale})`,
                                            transition: isDragging ? 'none' : 'transform 0.1s ease-out',
                                        }}
                                    >
                                        {/* Handle string content (like SVG source) or React Node */}
                                        {typeof fullscreenContent === 'string' ? (
                                            <div
                                                dangerouslySetInnerHTML={{ __html: fullscreenContent }}
                                                className="chart-modal-static-render"
                                            />
                                        ) : (
                                            // 🆕 修复：为 DataChart 等使用 ResponsiveContainer 的组件提供明确的宽高
                                            <div className="chart-modal-react-render">
                                                {React.isValidElement(fullscreenContent)
                                                    ? React.cloneElement(fullscreenContent, {
                                                        style: {
                                                            ...fullscreenContent.props?.style,
                                                            width: '100%',
                                                            height: '100%',
                                                            minHeight: '400px'
                                                        }
                                                    })
                                                    : fullscreenContent}
                                            </div>
                                        )}
                                    </div>
                                </div>

                                {/* 🆕 操作提示 */}
                                <div className="chart-modal-hint">
                                    💡 滚轮缩放 · 鼠标拖拽移动 · 快捷键: + 放大 / - 缩小 / 0 重置 / Esc 关闭
                                </div>
                            </Motion.div>
                        </Motion.div>
                    </ModalPortal>
                )}
            </AnimatePresence>
        </>
    );
};

// Portal utility to render modal at document root
const ModalPortal = ({ children }) => {
    if (typeof document === 'undefined') return null;
    return createPortal(children, document.body);
};

export default ChartCard;
