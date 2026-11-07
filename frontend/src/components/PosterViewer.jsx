import React, { useState } from 'react'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import BackButton from './BackButton'
import './PPTViewer.css'

function PosterViewer({ posterData, onClose }) {
    const [isLightboxOpen, setIsLightboxOpen] = useState(false)
    const [isDownloading, setIsDownloading] = useState(false)
    const [imageError, setImageError] = useState(false)

    const handleDownload = () => {
        if (!posterData?.poster_url) return
        setIsDownloading(true)
        window.open(posterData.poster_url, '_blank')
        setTimeout(() => setIsDownloading(false), 1000)
    }

    // 判断是否有数据
    const hasData = posterData && posterData.poster_url

    // 标题转换：兼容旧的 "Poster - xxx" 格式
    let title = posterData?.title || '海报'
    if (title.startsWith('Poster - ') || title.startsWith('Poster -')) {
        title = '海报'
    }

    return (
        <div className="poster-view">
            <div className="view-header">
                <BackButton onClick={onClose} />
                <h3>{title}</h3>
                {hasData && (
                    <button
                        className="download-btn-minimal"
                        onClick={handleDownload}
                        disabled={isDownloading}
                        title="下载海报"
                    >
                        {isDownloading ? (
                            <div className="spinner-icon"></div>
                        ) : (
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="7 10 12 15 17 10"></polyline>
                                <line x1="12" y1="15" x2="12" y2="3"></line>
                            </svg>
                        )}
                    </button>
                )}
            </div>

            <div className="ppt-slides-grid" style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: hasData && !imageError ? 'flex-start' : 'center',
                padding: '24px',
                minHeight: '300px'
            }}>
                {hasData && !imageError ? (
                    <Motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        style={{
                            cursor: 'zoom-in',
                            maxWidth: '100%',
                            background: 'white',
                            borderRadius: '8px',
                            boxShadow: '0 4px 20px rgba(0,0,0,0.12)',
                            overflow: 'hidden',
                            padding: '8px'
                        }}
                        onClick={() => setIsLightboxOpen(true)}
                    >
                        <img
                            src={posterData.poster_url}
                            alt={title}
                            onError={() => setImageError(true)}
                            style={{
                                maxWidth: '100%',
                                maxHeight: 'calc(100vh - 200px)',
                                objectFit: 'contain',
                                display: 'block'
                            }}
                        />
                    </Motion.div>
                ) : imageError ? (
                    <div style={{ textAlign: 'center', color: '#999' }}>
                        <p style={{ fontSize: '48px', marginBottom: '16px' }}>😞</p>
                        <p>海报加载失败</p>
                        <p style={{ fontSize: '12px', marginTop: '8px' }}>图片可能已被删除或链接已过期</p>
                    </div>
                ) : (
                    <div style={{ textAlign: 'center', color: '#999' }}>
                        <div className="spinner-icon" style={{
                            width: '24px',
                            height: '24px',
                            margin: '0 auto 16px',
                            border: '2px solid #e0e0e0',
                            borderTopColor: '#666',
                            borderRadius: '50%'
                        }}></div>
                    </div>
                )}
            </div>

            {/* Lightbox */}
            <AnimatePresence>
                {isLightboxOpen && hasData && (
                    <Motion.div
                        className="ppt-lightbox"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={() => setIsLightboxOpen(false)}
                    >
                        <Motion.img
                            src={posterData.poster_url}
                            alt={title}
                            initial={{ scale: 0.8 }}
                            animate={{ scale: 1 }}
                            exit={{ scale: 0.8 }}
                            onClick={(e) => e.stopPropagation()}
                        />
                        <button className="lightbox-close" onClick={() => setIsLightboxOpen(false)}>×</button>
                    </Motion.div>
                )}
            </AnimatePresence>
        </div>
    )
}

export default PosterViewer
