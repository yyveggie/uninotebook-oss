import React, { useState } from 'react'
import { FaProjectDiagram } from 'react-icons/fa'
import BackButton from '../BackButton'
import EmptyState from '../EmptyState'
import MindMap from '../MindMap'

const MindmapView = ({
  mindmap,
  tempMindmap,
  isGeneratingMindmap,
  onBack,
  onNodeClick,
  expandingNodeId
}) => {
  const [isDownloadingMindmap, setIsDownloadingMindmap] = useState(false)
  const displayMindmap = tempMindmap || mindmap

  if (!displayMindmap) {
    if (isGeneratingMindmap) {
      return (
        <div className="empty-view" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '16px' }}>
          <div className="spinner-icon" style={{
            width: '32px',
            height: '32px',
            border: '3px solid #e5e7eb',
            borderTopColor: '#1a1a1a',
            borderRadius: '50%',
            animation: 'spin 1s linear infinite'
          }}></div>
          <p style={{ color: '#6b7280', fontSize: '14px' }}>正在生成思维导图，请稍候...</p>
        </div>
      )
    }

    return (
      <EmptyState
        icon={<FaProjectDiagram />}
        text="暂无思维导图数据"
        subtext="点击左侧生成按钮开始创建"
      />
    )
  }

  const handleDownloadMindmap = async () => {
    try {
      setIsDownloadingMindmap(true)

      const svgElement = document.querySelector('.mindmap-wrapper svg')
      if (!svgElement) {
        throw new Error('找不到思维导图')
      }

      const clonedSvg = svgElement.cloneNode(true)

      const gElement = clonedSvg.querySelector('g')
      if (gElement) {
        gElement.setAttribute('transform', 'translate(50, 50)')
      }

      const width = 2000
      const height = 1500
      clonedSvg.setAttribute('width', width)
      clonedSvg.setAttribute('height', height)
      clonedSvg.setAttribute('viewBox', `0 0 ${width} ${height}`)
      clonedSvg.style.backgroundColor = 'white'

      const styleSheet = document.styleSheets
      let cssText = ''
      for (let i = 0; i < styleSheet.length; i++) {
        try {
          const rules = styleSheet[i].cssRules || styleSheet[i].rules
          if (rules) {
            for (let j = 0; j < rules.length; j++) {
              if (rules[j].cssText.includes('.link') ||
                rules[j].cssText.includes('.node') ||
                rules[j].cssText.includes('mindmap')) {
                cssText += rules[j].cssText + '\n'
              }
            }
          }
        } catch (e) {
          void e
        }
      }

      const styleElement = document.createElementNS('http://www.w3.org/2000/svg', 'style')
      styleElement.textContent = `
          .link { fill: none; stroke: #ccc; stroke-width: 1.5px; }
          .node-circle { fill: #fff; stroke: #1a1a1a; stroke-width: 2px; }
          .node-circle.root { fill: #1a1a1a; }
          .node-circle.level-1 { fill: #4A90E2; stroke: #4A90E2; }
          .node-circle.level-2 { fill: #50C878; stroke: #50C878; }
          .node-circle.level-3 { fill: #FFB347; stroke: #FFB347; }
          .node-text { font-size: 12px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; fill: #333; }
          .node-text-bg { fill: white; opacity: 0.9; }
          .expand-button-group { display: none; }
          ${cssText}
        `
      clonedSvg.insertBefore(styleElement, clonedSvg.firstChild)

      const serializer = new XMLSerializer()
      const svgString = serializer.serializeToString(clonedSvg)
      const svgBlob = new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' })
      const svgUrl = URL.createObjectURL(svgBlob)

      const canvas = document.createElement('canvas')
      canvas.width = width * 2
      canvas.height = height * 2
      const ctx = canvas.getContext('2d')
      ctx.scale(2, 2)
      ctx.fillStyle = 'white'
      ctx.fillRect(0, 0, width, height)

      const img = new Image()
      img.onload = () => {
        ctx.drawImage(img, 0, 0)
        URL.revokeObjectURL(svgUrl)

        canvas.toBlob((blob) => {
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a')
          a.href = url
          a.download = `思维导图_${new Date().toISOString().slice(0, 10)}.png`
          document.body.appendChild(a)
          a.click()
          document.body.removeChild(a)
          URL.revokeObjectURL(url)
          setIsDownloadingMindmap(false)
        }, 'image/png')
      }
      img.onerror = () => {
        URL.revokeObjectURL(svgUrl)
        setIsDownloadingMindmap(false)
        alert('导出失败，请重试')
      }
      img.src = svgUrl
    } catch (e) {
      setIsDownloadingMindmap(false)
      alert('下载失败: ' + e.message)
    }
  }

  return (
    <div className="mindmap-view">
      <div className="view-header">
        <BackButton onClick={onBack} />
        <h3>思维导图</h3>
        <button
          className="download-btn-minimal"
          onClick={handleDownloadMindmap}
          disabled={isDownloadingMindmap}
          title="下载思维导图"
        >
          {isDownloadingMindmap ? (
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
      <div className="view-content">
        <MindMap
          data={displayMindmap}
          onNodeClick={onNodeClick}
          expandingNodeId={expandingNodeId}
        />
      </div>
    </div>
  )
}

export default MindmapView