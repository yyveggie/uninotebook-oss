import { useState, useRef, useEffect } from 'react'
import './ResizableLayout.css'

/**
 * 可调整大小的三栏布局组件
 * 支持拖动分隔线调整左中右三栏的宽度，以及最大化/最小化功能
 */
function ResizableLayout({
  leftPanel,
  centerPanel,
  rightPanel,
  // 提升状态到父组件
  maximizedPanel = 'none' // 'none' | 'left' | 'center' | 'right'
}) {
  // 从localStorage读取保存的宽度，或使用默认值
  const [leftWidth, setLeftWidth] = useState(() => {
    const saved = localStorage.getItem('layout_left_width')
    return saved ? parseInt(saved) : 280
  })

  const [rightWidth, setRightWidth] = useState(() => {
    const saved = localStorage.getItem('layout_right_width')
    return saved ? parseInt(saved) : 360
  })

  const [isDraggingLeft, setIsDraggingLeft] = useState(false)
  const [isDraggingRight, setIsDraggingRight] = useState(false)

  const containerRef = useRef(null)
  const startXRef = useRef(0)
  const startWidthRef = useRef(0)

  // 保存宽度到localStorage
  useEffect(() => {
    localStorage.setItem('layout_left_width', leftWidth.toString())
  }, [leftWidth])

  useEffect(() => {
    localStorage.setItem('layout_right_width', rightWidth.toString())
  }, [rightWidth])

  // 左侧分隔线拖动开始
  const handleLeftResizeStart = (e) => {
    e.preventDefault()
    setIsDraggingLeft(true)
    startXRef.current = e.clientX
    startWidthRef.current = leftWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  // 右侧分隔线拖动开始
  const handleRightResizeStart = (e) => {
    e.preventDefault()
    setIsDraggingRight(true)
    startXRef.current = e.clientX
    startWidthRef.current = rightWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  // 鼠标移动
  useEffect(() => {
    const handleMouseMove = (e) => {
      if (isDraggingLeft) {
        const delta = e.clientX - startXRef.current
        const newWidth = Math.max(200, Math.min(700, startWidthRef.current + delta))
        setLeftWidth(newWidth)
      } else if (isDraggingRight) {
        const delta = startXRef.current - e.clientX
        // 右侧栏允许更宽，最大可达800px，以便显示流程图和闪卡
        const newWidth = Math.max(320, Math.min(800, startWidthRef.current + delta))
        setRightWidth(newWidth)
      }
    }

    const handleMouseUp = () => {
      if (isDraggingLeft || isDraggingRight) {
        setIsDraggingLeft(false)
        setIsDraggingRight(false)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }

    if (isDraggingLeft || isDraggingRight) {
      document.addEventListener('mousemove', handleMouseMove)
      document.addEventListener('mouseup', handleMouseUp)

      return () => {
        document.removeEventListener('mousemove', handleMouseMove)
        document.removeEventListener('mouseup', handleMouseUp)
      }
    }
  }, [isDraggingLeft, isDraggingRight])

  const hasRightPanel = !!rightPanel

  // 根据最大化状态计算实际宽度
  const getActualWidths = () => {
    if (maximizedPanel === 'left') {
      return { left: '100%', center: '0', right: '0' }
    } else if (maximizedPanel === 'center') {
      return { left: '0', center: '100%', right: '0' }
    } else if (maximizedPanel === 'right') {
      return { left: '0', center: '0', right: '100%' }
    } else {
      if (!hasRightPanel) {
        return {
          left: `${leftWidth}px`,
          center: `calc(100% - ${leftWidth}px - 1px)`,
          right: '0'
        }
      }
      return {
        left: `${leftWidth}px`,
        center: `calc(100% - ${leftWidth}px - ${rightWidth}px - 2px)`,
        right: `${rightWidth}px`
      }
    }
  }

  const widths = getActualWidths()

  return (
    <div
      ref={containerRef}
      className={`resizable-layout ${isDraggingLeft || isDraggingRight ? 'dragging' : ''} ${maximizedPanel !== 'none' ? 'has-maximized' : ''}`}
    >
      {/* 左侧面板 */}
      <div
        className={`resizable-panel left-panel ${maximizedPanel === 'left' ? 'maximized' : ''} ${maximizedPanel !== 'none' && maximizedPanel !== 'left' ? 'minimized' : ''}`}
        style={{ width: widths.left }}
      >
        {/* 最大化按钮移出 */}
        {leftPanel}
      </div>

      {/* 左侧分隔线 */}
      {maximizedPanel === 'none' && (
        <div
          className="resize-handle left-handle"
          onMouseDown={handleLeftResizeStart}
        >
          <div className="resize-handle-bar" />
        </div>
      )}

      {/* 中间面板 */}
      <div
        className={`resizable-panel center-panel ${maximizedPanel === 'center' ? 'maximized' : ''} ${maximizedPanel !== 'none' && maximizedPanel !== 'center' ? 'minimized' : ''}`}
        style={{
          width: widths.center,
          marginLeft: maximizedPanel === 'none' ? '1px' : '0',
          marginRight: maximizedPanel === 'none' && hasRightPanel ? '1px' : '0'
        }}
      >
        {/* 最大化按钮移出 */}
        {centerPanel}
      </div>

      {/* 右侧分隔线 */}
      {maximizedPanel === 'none' && hasRightPanel && (
        <div
          className="resize-handle right-handle"
          onMouseDown={handleRightResizeStart}
        >
          <div className="resize-handle-bar" />
        </div>
      )}

      {/* 右侧面板 */}
      {hasRightPanel && (
        <div
          className={`resizable-panel right-panel ${maximizedPanel === 'right' ? 'maximized' : ''} ${maximizedPanel !== 'none' && maximizedPanel !== 'right' ? 'minimized' : ''}`}
          style={{ width: widths.right }}
        >
          {/* 最大化按钮移出 */}
          {rightPanel}
        </div>
      )}
    </div>
  )
}

export default ResizableLayout
