import { useState, useCallback, useEffect } from 'react'
import PdfViewer from './PdfViewer'
import PdfSelectionPopover from './PdfSelectionPopover'
import { apiGet } from '../../utils/api'

/**
 * Integrated PDF preview panel with text selection + send-to-chat support.
 *
 * Props:
 *   documentId      – backend document ID
 *   onSendToChat    – (payload: { title, content, type, pageNumber }) => void
 *   className       – optional wrapper class
 */
export default function PdfPreviewPanel({ documentId, onSendToChat, className = '' }) {
  const [fileUrl, setFileUrl] = useState(null)
  const [fileInfo, setFileInfo] = useState(null)
  const [selectionInfo, setSelectionInfo] = useState(null)

  // Fetch file availability + signed URL
  useEffect(() => {
    if (!documentId) return
    let cancelled = false

    ;(async () => {
      try {
        const infoRes = await apiGet(`/api/document/${documentId}/file/info`)
        if (cancelled) return

        const info = infoRes?.data
        setFileInfo(info || { available: false })

        if (!info?.available) return

        const urlRes = await apiGet(`/api/document/${documentId}/file/url`)
        if (cancelled) return

        if (urlRes?.data?.url) {
          setFileUrl(urlRes.data.url)
        }
      } catch (err) {
        console.warn('PDF preview unavailable:', err)
        if (!cancelled) setFileInfo({ available: false })
      }
    })()

    return () => { cancelled = true }
  }, [documentId])

  const handleTextSelect = useCallback((info) => {
    setSelectionInfo(info)
  }, [])

  const handleSendToChat = useCallback((payload) => {
    const pageLabel = payload.pageNumber
      ? `PDF 选段 · 第 ${payload.pageNumber} 页`
      : 'PDF 选段'

    onSendToChat?.({
      title: pageLabel,
      content: payload.text,
      type: 'pdf_selection',
      pageNumber: payload.pageNumber,
    })
  }, [onSendToChat])

  const handleDismiss = useCallback(() => {
    setSelectionInfo(null)
  }, [])

  if (!fileInfo) {
    return (
      <div className={className} style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999', fontSize: 13 }}>
        正在检查文件可用性…
      </div>
    )
  }

  if (!fileInfo.available) {
    return (
      <div className={className} style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#999', gap: 8 }}>
        <span style={{ fontSize: 32, opacity: 0.4 }}>📄</span>
        <span style={{ fontSize: 13 }}>该文档上传时未保存原始文件，暂无预览</span>
      </div>
    )
  }

  return (
    <div className={className} style={{ height: '100%' }}>
      <PdfViewer
        fileUrl={fileUrl}
        onTextSelect={handleTextSelect}
      />
      <PdfSelectionPopover
        selectionInfo={selectionInfo}
        onSendToChat={handleSendToChat}
        onDismiss={handleDismiss}
      />
    </div>
  )
}
