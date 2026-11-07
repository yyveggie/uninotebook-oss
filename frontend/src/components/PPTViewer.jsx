import React, { useState } from 'react'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import { apiRequest } from '../utils/api'
import BackButton from './BackButton'
import './PPTViewer.css'

function PPTViewer({ pptData, onClose }) {
    const [selectedImage, setSelectedImage] = useState(null)
    const [isDownloading, setIsDownloading] = useState(false)

    const normalizeImages = (value) => {
        if (!value) return []
        if (Array.isArray(value)) return value.filter(Boolean)
        if (typeof value === 'string') {
            try {
                const parsed = JSON.parse(value)
                if (Array.isArray(parsed)) return parsed.filter(Boolean)
            } catch {
                return value.startsWith('http://') || value.startsWith('https://')
                    ? [value]
                    : value.split(',').map(item => item.trim()).filter(Boolean)
            }
            if (value.startsWith('http://') || value.startsWith('https://')) {
                return [value]
            }
            return value.split(',').map(item => item.trim()).filter(Boolean)
        }
        return []
    }

    const images = normalizeImages(pptData?.preview_images)

    if (!pptData || images.length === 0) {
        return (
            <div className="ppt-viewer-empty">
                <p>暂无幻灯片预览</p>
            </div>
        )
    }

    const handleDownload = async () => {
        if (!pptData || !pptData.id) return
        setIsDownloading(true)
        try {
            // Use authenticated apiRequest
            const response = await apiRequest(`/api/ppt/download/${pptData.id}`)
            if (response.ok) {
                const blob = await response.blob()
                const url = window.URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = `ppt_package_${pptData.id}.zip`
                document.body.appendChild(a)
                a.click()
                window.URL.revokeObjectURL(url)
                document.body.removeChild(a)
            } else {
                alert('下载失败，请稍后重试')
            }
        } catch {
            alert('下载出错')
        } finally {
            setIsDownloading(false)
        }
    }

    return (
        <div className="ppt-viewer-container">
            <div className="ppt-viewer-header">
                <BackButton onClick={onClose} />
                <h3>PPT 预览</h3>
                <button
                    className="download-btn-minimal"
                    onClick={handleDownload}
                    disabled={isDownloading}
                    title="打包下载 (图片+PDF)"
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
            </div>

            {(() => {
                // Analysis of missing slides
                try {
                    if (images.length === 0) return null;

                    // Extract indices from URLs (assuming format: .../003_title.jpg)
                    const indices = images.map(url => {
                        const match = url.match(/\/(\d{3})_/);
                        return match ? parseInt(match[1], 10) : -1;
                    }).filter(i => i >= 0);

                    if (indices.length > 0) {
                        const maxIndex = Math.max(...indices);
                        const expectedTotal = maxIndex + 1;
                        if (images.length < expectedTotal) {
                            return (
                                <div className="ppt-warning-banner" style={{
                                    background: '#fff7ed',
                                    borderBottom: '1px solid #ffedd5',
                                    padding: '10px 16px',
                                    fontSize: '13px',
                                    color: '#9a3412',
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '8px'
                                }}>
                                    <span style={{ fontSize: '16px' }}>⚠️</span>
                                    <div>
                                        检测到幻灯片可能缺失（当前 {images.length} 张 / 应有 {expectedTotal} 张）。
                                        可能是生成过程中因网络波动中断。
                                        <br />
                                        <span style={{ fontWeight: 500, textDecoration: 'underline', cursor: 'pointer' }} onClick={onClose}>
                                            建议关闭此窗口，再次点击左侧“PPT生成”按钮进行补全。
                                        </span>
                                    </div>
                                </div>
                            )
                        }
                    }
                } catch {
                    return null
                }
                return null;
            })()}

            <div className="ppt-slides-grid">
                {images.map((url, index) => (
                    <Motion.div
                        key={index}
                        className="ppt-slide-card"
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: index * 0.05 }}
                        onClick={() => setSelectedImage(url)}
                    >
                        <div className="slide-number">{index + 1}</div>
                        <img src={url} alt={`Slide ${index + 1}`} loading="lazy" />
                    </Motion.div>
                ))}
            </div>

            <AnimatePresence>
                {selectedImage && (
                    <Motion.div
                        className="ppt-lightbox"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={() => setSelectedImage(null)}
                    >
                        <Motion.img
                            src={selectedImage}
                            alt="Full Screen Slide"
                            initial={{ scale: 0.8 }}
                            animate={{ scale: 1 }}
                            exit={{ scale: 0.8 }}
                            onClick={(e) => e.stopPropagation()} // Prevent closing when clicking image
                        />
                        <button className="lightbox-close" onClick={() => setSelectedImage(null)}>×</button>
                    </Motion.div>
                )}
            </AnimatePresence>
        </div>
    )
}

export default PPTViewer
