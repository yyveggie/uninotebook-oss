import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import { getAccessToken } from '../../utils/api'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import './PdfViewer.css'

const LOCAL_PDF_WORKER_SRC = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

const CDN_PDF_WORKER_SRC = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`

const shouldUseCdnWorker =
  typeof window !== 'undefined' && /(^|\.)uninotebook\.com$/i.test(window.location.hostname)

// Production fallback: use CDN worker to avoid server-side .mjs MIME misconfiguration.
pdfjs.GlobalWorkerOptions.workerSrc = shouldUseCdnWorker
  ? CDN_PDF_WORKER_SRC
  : LOCAL_PDF_WORKER_SRC

const MIN_SCALE = 0.5
const MAX_SCALE = 3.0
const SCALE_STEP = 0.15
const OBSERVER_MARGIN = '400px 0px'

export default function PdfViewer({ fileUrl, onTextSelect, className = '' }) {
  const [numPages, setNumPages] = useState(null)
  const [scale, setScale] = useState(1.0)
  const [fitMode, setFitMode] = useState('width')
  const [loadError, setLoadError] = useState(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [jumpInput, setJumpInput] = useState('')

  const containerRef = useRef(null)
  const pageRefs = useRef({})
  const visiblePagesRef = useRef(new Set([1]))

  const onDocumentLoadSuccess = useCallback(({ numPages: total }) => {
    setNumPages(total)
    setLoadError(null)
  }, [])

  const onDocumentLoadError = useCallback((error) => {
    console.error('PDF load error:', error)
    setLoadError(error?.message || '加载失败')
  }, [])

  // --- Scale ---
  const zoomIn = useCallback(() => {
    setFitMode('custom')
    setScale(s => Math.min(s + SCALE_STEP, MAX_SCALE))
  }, [])
  const zoomOut = useCallback(() => {
    setFitMode('custom')
    setScale(s => Math.max(s - SCALE_STEP, MIN_SCALE))
  }, [])

  const fitWidth = useCallback(() => {
    setFitMode('width')
    setScale(1.0)
  }, [])

  // --- Page jump ---
  const jumpToPage = useCallback((pageNum) => {
    const num = parseInt(pageNum, 10)
    if (isNaN(num) || num < 1 || num > numPages) return
    const el = pageRefs.current[num]
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setCurrentPage(num)
    }
  }, [numPages])

  const handleJumpSubmit = useCallback((e) => {
    e.preventDefault()
    jumpToPage(jumpInput)
    setJumpInput('')
  }, [jumpInput, jumpToPage])

  // --- Intersection Observer for lazy rendering + current page tracking ---
  useEffect(() => {
    if (!numPages) return

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = new Set(visiblePagesRef.current)

        entries.forEach((entry) => {
          const pageNum = parseInt(entry.target.dataset.pageNum, 10)
          if (entry.isIntersecting) {
            visible.add(pageNum)
          } else {
            visible.delete(pageNum)
          }
        })

        visiblePagesRef.current = visible

        if (visible.size > 0) {
          setCurrentPage(Math.min(...visible))
        }
      },
      {
        root: containerRef.current,
        rootMargin: OBSERVER_MARGIN,
        threshold: 0.1,
      },
    )

    Object.values(pageRefs.current).forEach((el) => {
      if (el) observer.observe(el)
    })

    return () => observer.disconnect()
  }, [numPages])

  // --- Text selection handler ---
  const handleMouseUp = useCallback(() => {
    if (!onTextSelect) return
    const selection = window.getSelection()
    if (!selection || selection.isCollapsed) return

    const selectedText = selection.toString().trim()
    if (selectedText.length < 2) return

    const range = selection.getRangeAt(0)
    const rect = range.getBoundingClientRect()

    // Determine which page(s) the selection spans
    let pageNum = null
    let node = range.startContainer
    while (node && node !== document.body) {
      if (node.dataset?.pageNum) {
        pageNum = parseInt(node.dataset.pageNum, 10)
        break
      }
      node = node.parentElement
    }

    onTextSelect({
      text: selectedText.slice(0, 3000),
      pageNumber: pageNum,
      rect,
    })
  }, [onTextSelect])

  // Container width for fit-to-width mode
  const [containerWidth, setContainerWidth] = useState(0)
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(([entry]) => {
      setContainerWidth(entry.contentRect.width - 24)
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  const pageWidth = fitMode === 'width' && containerWidth > 0
    ? containerWidth
    : undefined

  const documentOptions = useMemo(() => {
    const opts = {
      cMapUrl: `https://unpkg.com/pdfjs-dist@${pdfjs.version}/cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `https://unpkg.com/pdfjs-dist@${pdfjs.version}/standard_fonts/`,
    }
    const token = getAccessToken()
    if (token) {
      opts.httpHeaders = { Authorization: `Bearer ${token}` }
    }
    return opts
  }, [fileUrl])

  if (!fileUrl) {
    return (
      <div className={`pdf-viewer-empty ${className}`}>
        <div className="pdf-empty-icon">📄</div>
        <p>该文档无原始文件预览</p>
      </div>
    )
  }

  return (
    <div className={`pdf-viewer ${className}`}>
      {/* Toolbar */}
      <div className="pdf-toolbar">
        <div className="pdf-toolbar-left">
          <button onClick={zoomOut} title="缩小" disabled={scale <= MIN_SCALE}>−</button>
          <span className="pdf-scale-label">{Math.round(scale * 100)}%</span>
          <button onClick={zoomIn} title="放大" disabled={scale >= MAX_SCALE}>+</button>
          <button onClick={fitWidth} title="适应宽度" className={fitMode === 'width' ? 'active' : ''}>
            ↔
          </button>
        </div>
        <div className="pdf-toolbar-center">
          <form onSubmit={handleJumpSubmit} className="pdf-page-jump">
            <input
              type="text"
              value={jumpInput}
              onChange={(e) => setJumpInput(e.target.value)}
              placeholder={`${currentPage}`}
              className="pdf-page-input"
            />
            <span className="pdf-page-total">/ {numPages || '—'}</span>
          </form>
        </div>
      </div>

      {/* Content */}
      <div className="pdf-content" ref={containerRef} onMouseUp={handleMouseUp}>
        {loadError ? (
          <div className="pdf-error">
            <p>PDF 加载失败</p>
            <p className="pdf-error-detail">{loadError}</p>
            <button onClick={() => setLoadError(null)}>重试</button>
          </div>
        ) : (
          <Document
            file={fileUrl}
            onLoadSuccess={onDocumentLoadSuccess}
            onLoadError={onDocumentLoadError}
            options={documentOptions}
            loading={<div className="pdf-loading">正在加载 PDF…</div>}
          >
            {numPages && Array.from({ length: numPages }, (_, i) => {
              const pageNum = i + 1
              const isNear = visiblePagesRef.current.has(pageNum) ||
                Math.abs(pageNum - currentPage) <= 2

              return (
                <div
                  key={pageNum}
                  data-page-num={pageNum}
                  ref={(el) => { pageRefs.current[pageNum] = el }}
                  className="pdf-page-wrapper"
                >
                  {isNear ? (
                    <Page
                      pageNumber={pageNum}
                      scale={fitMode === 'width' ? undefined : scale}
                      width={pageWidth}
                      renderTextLayer={true}
                      renderAnnotationLayer={true}
                      loading={
                        <div className="pdf-page-placeholder" style={{ height: 800 }}>
                          第 {pageNum} 页加载中…
                        </div>
                      }
                    />
                  ) : (
                    <div className="pdf-page-placeholder" style={{ height: 800 }}>
                      第 {pageNum} 页
                    </div>
                  )}
                </div>
              )
            })}
          </Document>
        )}
      </div>
    </div>
  )
}
