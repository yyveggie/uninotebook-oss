import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'

/**
 * Floating action bar that appears when the user selects text inside the PDF viewer.
 *
 * Props:
 *   selectionInfo  – { text, pageNumber, rect } from PdfViewer.onTextSelect
 *   onSendToChat   – (payload: { text, pageNumber }) => void
 *   onDismiss      – () => void
 */
export default function PdfSelectionPopover({ selectionInfo, onSendToChat, onDismiss }) {
  const [position, setPosition] = useState(null)
  const popoverRef = useRef(null)

  useEffect(() => {
    if (!selectionInfo?.rect) {
      setPosition(null)
      return
    }

    const { rect } = selectionInfo
    const top = rect.bottom + 6
    let left = rect.left + rect.width / 2

    // Keep within viewport
    const popoverWidth = 170
    if (left - popoverWidth / 2 < 8) left = popoverWidth / 2 + 8
    if (left + popoverWidth / 2 > window.innerWidth - 8) left = window.innerWidth - popoverWidth / 2 - 8

    setPosition({ top: Math.min(top, window.innerHeight - 50), left })
  }, [selectionInfo])

  // Dismiss when clicking outside or when selection clears
  useEffect(() => {
    if (!position) return

    const handleSelectionChange = () => {
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed) {
        onDismiss?.()
      }
    }

    const handleMouseDown = (e) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target)) {
        onDismiss?.()
      }
    }

    document.addEventListener('selectionchange', handleSelectionChange)
    document.addEventListener('mousedown', handleMouseDown, true)
    return () => {
      document.removeEventListener('selectionchange', handleSelectionChange)
      document.removeEventListener('mousedown', handleMouseDown, true)
    }
  }, [position, onDismiss])

  const handleSendToChat = useCallback(() => {
    if (!selectionInfo) return
    onSendToChat?.({
      text: selectionInfo.text,
      pageNumber: selectionInfo.pageNumber,
    })
    window.getSelection()?.removeAllRanges()
    onDismiss?.()
  }, [selectionInfo, onSendToChat, onDismiss])

  const handleCopy = useCallback(() => {
    if (!selectionInfo?.text) return
    navigator.clipboard.writeText(selectionInfo.text).catch(() => {})
    onDismiss?.()
  }, [selectionInfo, onDismiss])

  if (!position || !selectionInfo) return null

  return createPortal(
    <div
      ref={popoverRef}
      className="pdf-selection-popover"
      onMouseDown={(e) => e.preventDefault()}
      style={{
        top: position.top,
        left: position.left,
        transform: 'translateX(-50%)',
      }}
    >
      <button className="primary" onClick={handleSendToChat}>
        💬 发送到对话
      </button>
      <button onClick={handleCopy}>
        📋 复制
      </button>
    </div>,
    document.body,
  )
}
