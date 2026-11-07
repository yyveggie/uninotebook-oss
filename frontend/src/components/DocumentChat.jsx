import { useState, useRef, useEffect, forwardRef, useImperativeHandle, useMemo, lazy, Suspense } from 'react'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import { MarkdownRenderer } from './MarkdownRenderer'
import { apiRequest, apiGet, getApiErrorMessage, isLocalRuntime } from '../utils/api'
import useDocumentStore from '../stores/documentStore'
import { useMermaidPatch, cleanupOrphanedMermaidElements } from '../hooks/useMermaidPatch'
import { useAutoScroll } from '../hooks/useAutoScroll'
import { getMermaid } from '../utils/mermaidLoader'
import { parseTaskErrorResponse, normalizeTaskErrorMessage, isInsufficientCreditsText } from '../utils/taskError'
import { escapeHtml } from '../lib/utils'
import KeyTermEditor from './KeyTermEditor'
import './DocumentChat.css'
import ChartCard from './ChartCard'
import { SkeletonKeywords, SkeletonChatMessage } from './Skeleton'
import useFirstEntrance from '../hooks/useFirstEntrance'
import { useNavigate } from 'react-router-dom'
import useRuntimeStore from '../stores/runtimeStore'

const DataChart = lazy(() => import('./DataChart'))

/**
 * 单个流式回答的 token 缓冲：约每 30ms 批量写入一次消息，渲染更平滑。
 * 每次请求独立创建——以前挂在 window 上的全局缓冲会被多个对话共用，
 * 且「结束」类事件（done / clarification / error / 流中断）前不刷新，最后一批 token 会丢。
 */
const createTokenBuffer = (applyChunk, intervalMs = 30) => {
  let pending = ''
  let timer = null
  let lastFlushAt = 0

  /** 取出尚未写入的内容（由调用方自行合并，如 status 事件前的强制刷新） */
  const take = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    const chunk = pending
    pending = ''
    lastFlushAt = performance.now()
    return chunk
  }

  const flush = () => {
    const chunk = take()
    if (chunk) applyChunk(chunk)
  }

  const push = (text) => {
    if (!text) return
    pending += text
    if (timer) return
    const delay = Math.max(0, intervalMs - (performance.now() - lastFlushAt))
    timer = setTimeout(flush, delay)
  }

  /** 丢弃未写入的内容（流被中止、视图已切换时使用） */
  const cancel = () => {
    if (timer) clearTimeout(timer)
    timer = null
    pending = ''
  }

  return { push, flush, take, cancel }
}

/** SSE 中 error 事件的数据可能是字符串或对象 */
const getStreamErrorText = (data) => {
  if (typeof data === 'string') return data.trim() || '生成失败'
  return getApiErrorMessage(data, '生成失败')
}

/** 构造一个「流内错误」：用于穿透逐行解析的 try/catch，交给外层统一展示 */
const createStreamError = (data) => {
  const error = new Error(getStreamErrorText(data))
  error.isStreamError = true
  return error
}

// 初始化 Mermaid
// useMermaidPatch 会处理全局初始化和错误拦截


// Mermaid 渲染组件 (用于预览)
const MermaidPreview = ({ code }) => {
  const containerRef = useRef(null)

  useEffect(() => {
    if (containerRef.current && code) {
      containerRef.current.innerHTML = '<div style="color:#666; font-size:12px">Loading Chart...</div>'
      const id = `mermaid-preview-${Math.random().toString(36).substr(2, 9)}`

      // 🔧 通用清理函数：清理本次渲染残留的 Mermaid 临时元素
      // 只按 id / Mermaid 自身的错误容器精确匹配——以前按「id 以 d 开头」之类的宽泛规则
      // 扫描整个文档，可能误删应用自身的节点
      const cleanupMermaidElements = () => {
        const tempElement = document.getElementById(id)
        if (tempElement) {
          tempElement.remove()
        }
        cleanupOrphanedMermaidElements()
      }

      const render = async () => {
        const mermaid = await getMermaid()

        // 🔧 主渲染尝试：显式创建临时 DOM 元素
        const tempDiv = document.createElement('div')
        tempDiv.id = id
        tempDiv.style.position = 'absolute'
        tempDiv.style.visibility = 'hidden'
        tempDiv.style.pointerEvents = 'none'
        tempDiv.style.top = '-9999px'
        document.body.appendChild(tempDiv)

        try {
          // 等待下一帧，确保 DOM 已更新
          await new Promise(resolve => requestAnimationFrame(resolve))

          const { svg } = await mermaid.render(id, code)

          if (containerRef.current) {
            containerRef.current.innerHTML = svg
            // 调整 SVG 样式
            const svgElement = containerRef.current.querySelector('svg')
            if (svgElement) {
              const bbox = svgElement.getBBox()
              const padding = 20
              const newViewBox = `${bbox.x - padding} ${bbox.y - padding} ${bbox.width + padding * 2} ${bbox.height + padding * 2}`
              svgElement.setAttribute('viewBox', newViewBox)
              svgElement.removeAttribute('width')
              svgElement.removeAttribute('height')
              svgElement.style.width = '100%'
              svgElement.style.height = 'auto'
              svgElement.style.maxHeight = '500px'
            }
          }
        } catch (err) {
          console.warn('First mermaid attempt failed:', err)

          // 🔧 Retry Logic
          const retryId = `mermaid-retry-${Math.random().toString(36).substr(2, 9)}`
          const retryDiv = document.createElement('div')
          retryDiv.id = retryId
          retryDiv.style.position = 'absolute'
          retryDiv.style.visibility = 'hidden'
          retryDiv.style.top = '-9999px'
          document.body.appendChild(retryDiv)

          try {
            await new Promise(resolve => setTimeout(resolve, 50))
            const { svg } = await mermaid.render(retryId, code)

            if (containerRef.current) {
              containerRef.current.innerHTML = svg
              const svgElement = containerRef.current.querySelector('svg')
              if (svgElement) {
                svgElement.style.width = '100%'; svgElement.style.height = 'auto';
              }
            }
          } catch (retryErr) {
            // 🔧 两次都失败了，显示错误
            cleanupMermaidElements()
            if (containerRef.current) {
              // Mermaid 的报错会原样回显图表源码（来自 LLM / 文档内容），
              // 必须用 textContent 构建，不能拼进 innerHTML
              const box = document.createElement('div')
              box.style.cssText = 'color:#d32f2f; font-size:12px; padding:12px; border:1px solid #ffcdd2; background:#ffebee; border-radius:4px; text-align:left; overflow:auto;'
              const title = document.createElement('div')
              title.style.cssText = 'font-weight:bold; margin-bottom:4px;'
              title.textContent = '⚠️ 图表渲染失败'
              const detail = document.createElement('pre')
              detail.style.cssText = 'margin:0; white-space:pre-wrap; font-family:monospace;'
              detail.textContent = retryErr?.message || String(retryErr)
              box.append(title, detail)
              containerRef.current.replaceChildren(box)
            }
          } finally {
            if (document.body.contains(retryDiv)) document.body.removeChild(retryDiv)
          }
        } finally {
          if (document.body.contains(tempDiv)) document.body.removeChild(tempDiv)
        }
      }
      render()

      // 🔧 清理函数：组件卸载时清理可能残留的临时元素
      return () => {
        // 仅清理当前组件实例生成的 ID
        const currentEl = document.getElementById(id)
        if (currentEl) {
          currentEl.remove()
        }
      }
    }
  }, [code]);

  // 使用 Mermaid 补丁 Hook
  useMermaidPatch()

  return (
    <div
      ref={containerRef}
      className="mermaid-wrapper mermaid-preview-wrapper mermaid-preview-min-height"
    />
  )
}

// 状态指示器组件 - 显示 LLM 当前工作状态
const StatusIndicator = ({ status }) => {
  if (!status) return null

  // 支持字符串格式（向后兼容）和对象格式
  const statusText = typeof status === 'string' ? status : status.text
  const statusIcon = typeof status === 'object' ? status.icon : 'thinking'
  const statusPhase = typeof status === 'object' ? status.phase : 'init'

  if (!statusText) return null

  // 根据 icon 类型选择图标
  const iconMap = {
    'thinking': '🤔',
    'search': '🔍',
    'flowchart': '🗺️',
    'chart': '📊',
    'analyze': '🧠',
    'evaluate': '⚖️',
    'refine': '✍️',
    'writing': '💬',
    'done': '✅',
  }

  const icon = iconMap[statusIcon] || '🫧'

  return (
    <div className={`status-indicator status-phase-${statusPhase}`}>
      <span className="status-icon">{icon}</span>
      <span className="status-text">{statusText}</span>
      <span className="status-dots">
        <span className="dot">.</span>
        <span className="dot">.</span>
        <span className="dot">.</span>
      </span>
    </div>
  )
}

// 🆕 状态历史面板 - 列表展示，无折叠，无 Emoji，去重，支持详情折叠
const StatusHistoryPanel = ({ history, isStreaming }) => {
  const [isExpanded, setIsExpanded] = useState(false)
  const scrollRef = useRef(null)

  // 去重：合并连续相同文案
  const uniqueHistory = useMemo(() => {
    const result = []
    let lastText = null
    for (const item of history || []) {
      if (item.text !== lastText) {
        result.push(item)
      }
      lastText = item.text
    }
    return result
  }, [history])

  // 自动滚动到底部
  useEffect(() => {
    if (scrollRef.current && !isExpanded) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [uniqueHistory.length, isExpanded])

  if (!history || history.length === 0) return null

  const COLLAPSED_HEIGHT = 90 // 约 3 行
  const shouldCollapse = uniqueHistory.length > 3

  return (
    <div className="status-scroll-panel">
      <div
        ref={scrollRef}
        className="status-scroll-area"
        style={{
          maxHeight: isExpanded ? 'none' : `${COLLAPSED_HEIGHT}px`,
          overflow: isExpanded ? 'visible' : 'hidden',
        }}
      >
        {uniqueHistory.map((item, index) => (
          <div key={index} className="status-scroll-line">
            <span className={`status-scroll-text ${(!item.isComplete && isStreaming) ? 'thinking-text-shimmer' : ''}`}>{item.text}</span>
          </div>
        ))}
      </div>
      {shouldCollapse && !isStreaming && (
        <button
          className="status-expand-btn"
          onClick={() => setIsExpanded(!isExpanded)}
        >
          {isExpanded ? '收起' : `展开全部 (${uniqueHistory.length})`}
          <svg
            width="12" height="12" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            className="status-expand-icon"
            style={{ transform: isExpanded ? 'rotate(180deg)' : 'none' }}
          >
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </button>
      )}
    </div>
  )
}

// 🆕 Plan Item Component (Styled like Status Item)
const PlanItemRenderer = ({ item }) => {
  const planSteps = item.steps || item.planData || []
  if (!planSteps.length) return null

  return (
    <div className="thinking-line">
      <div className="thinking-line-content">
        <span className={`thinking-line-text ${(!item.isComplete && item.isStreaming) ? 'thinking-text-shimmer' : ''}`}>目标规划</span>
        <div className="thinking-details">
          {planSteps.map((step, index) => (
            <div key={index} className="thinking-detail-item">
              <span className="thinking-detail-label">Step {index + 1}</span>
              <span className="thinking-detail-value">{step.title}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// 🆕 时间线渲染器：按时间顺序交错显示思考块和回答内容
const TimelineRenderer = ({ timeline, isStreaming, flowchart, chartData }) => {
  const [showThinking, setShowThinking] = useState(false)
  const [collapsedBlocks, setCollapsedBlocks] = useState(new Set())
  const scrollRefs = useRef({})

  const safeTimeline = timeline || []
  const hasTimeline = safeTimeline.length > 0

  // isStreaming 开始后延迟 1 秒显示"我正在思考中..."
  useEffect(() => {
    if (isStreaming) {
      const timer = setTimeout(() => setShowThinking(true), 1000)
      return () => clearTimeout(timer)
    }
  }, [isStreaming])

  // 将 timeline 按顺序分段：连续的 status/plan 归为 thinking 段，content 归为 content 段
  const segments = useMemo(() => {
    const result = []
    let currentGroup = null
    let lastStatusText = null

    for (const item of safeTimeline) {
      // 🆕 修复：如果 content 仅仅是一个点或微小的标点，直接忽略，
      // 既不渲染也不打断 thinking 块的连续性
      if (item.type !== 'status' && item.type !== 'plan') {
        const text = (item.text || '').trim()
        if (text === '.' || text === '。' || !text) {
          continue
        }
      }

      const category = (item.type === 'status' || item.type === 'plan' || item.type === 'thinking_stream' || item.phase === 'evaluate') ? 'thinking' : 'content'

      if (!currentGroup || currentGroup.category !== category) {
        currentGroup = { category, items: [] }
        result.push(currentGroup)
        lastStatusText = null
      }

      // 去重连续相同的 status 文本
      if (item.type === 'status') {
        if (item.text === lastStatusText) continue
        lastStatusText = item.text
      } else {
        lastStatusText = null
      }

      currentGroup.items.push(item)
    }
    return result
  }, [safeTimeline])

  // 找到最后一个 thinking 段的索引
  const lastThinkingIdx = useMemo(() => {
    for (let i = segments.length - 1; i >= 0; i--) {
      if (segments[i].category === 'thinking') return i
    }
    return -1
  }, [segments])

  // 找到最后一个 content 段的索引（用于流式光标）
  const lastContentSegIdx = useMemo(() => {
    for (let i = segments.length - 1; i >= 0; i--) {
      if (segments[i].category === 'content') return i
    }
    return -1
  }, [segments])

  // 流式结束后自动收起所有思考块
  useEffect(() => {
    if (!isStreaming && segments.length > 0) {
      const allThinkingIndices = new Set()
      segments.forEach((s, i) => { if (s.category === 'thinking') allThinkingIndices.add(i) })
      setCollapsedBlocks(allThinkingIndices)
    }
  }, [isStreaming])

  // 🆕 自动滚动思考块到底部
  useEffect(() => {
    segments.forEach((segment, index) => {
      if (segment.category === 'thinking') {
        const el = scrollRefs.current[index]
        if (el) {
          // 加上 requestAnimationFrame 确保在 DOM 更新后执行
          requestAnimationFrame(() => {
            if (el) {
              const { scrollHeight, clientHeight, scrollTop } = el;

              // 判断是否是当前正在生成的最后一个思考块
              const isActiveThinkingBlock = isStreaming && index === lastThinkingIdx;

              // 如果是当前正在生成的块，强制跟随滚动
              if (isActiveThinkingBlock) {
                el.scrollTop = scrollHeight;
              } else {
                // 对于历史块，仅当用户已经在底部附近时才跟随（避免误触）
                const isNearBottom = scrollHeight - clientHeight - scrollTop < 50;
                if (isNearBottom) {
                  el.scrollTop = scrollHeight;
                }
              }
            }
          })
        }
      }
    })
  }, [segments, isStreaming, lastThinkingIdx])

  // 所有 hooks 已声明完毕，现在可以做条件返回
  if (!hasTimeline && !isStreaming) return null
  if (!hasTimeline && isStreaming && !showThinking) return null

  const isBlockExpanded = (segIdx) => {
    // collapsedBlocks 表示"用户手动切换过"的块
    if (isStreaming) {
      // 默认状态：最后一个 thinking 块展开，其余收起
      const defaultExpanded = segIdx === lastThinkingIdx
      // 如果用户手动切换过，反转默认状态
      const userToggled = collapsedBlocks.has(segIdx)
      return userToggled ? !defaultExpanded : defaultExpanded
    }
    // 非流式：collapsedBlocks 中的块收起（流结束时自动全部收起，用户可手动展开）
    return !collapsedBlocks.has(segIdx)
  }

  const toggleBlock = (segIdx) => {
    setCollapsedBlocks(prev => {
      const next = new Set(prev)
      if (next.has(segIdx)) next.delete(segIdx)
      else next.add(segIdx)
      return next
    })
  }

  // 渲染内容项
  const renderContent = (item, index, isLastStreamingContent = false) => {
    const content = item.text || ''

    const parts = content.split(/(<<<FLOWCHART_PLACEHOLDER>>>|<<<DATA_CHART_PLACEHOLDER>>>)/)
    return (
      <div key={`content-${index}`} className={`timeline-content-item${isLastStreamingContent ? ' is-streaming-latest' : ''}`}>
        {parts.map((part, pi) => {
          if (part === '<<<FLOWCHART_PLACEHOLDER>>>' && flowchart) {
            return (
              <Motion.div key={`fc-${pi}`} className="message-flowchart timeline-media-block"
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}
              >
                <ChartCard title={flowchart.title || '知识路径'} icon="🗺️" minimal={true}>
                  <div className="mermaid-wrapper"><MermaidPreview code={flowchart.mermaid_code || ''} /></div>
                </ChartCard>
              </Motion.div>
            )
          }
          if (part === '<<<DATA_CHART_PLACEHOLDER>>>' && chartData) {
            return (
              <Motion.div key={`dc-${pi}`} className="message-chart timeline-media-block"
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}
              >
                <ChartCard title={chartData.title || '数据图表'} icon="📊">
                  <DataChart data={chartData.data} type={chartData.type}
                    xKey={chartData.xKey} dataKeys={chartData.dataKeys} unit={chartData.unit} />
                </ChartCard>
              </Motion.div>
            )
          }
          if (part && part.trim()) return <MarkdownRenderer key={`md-${pi}`}>{part}</MarkdownRenderer>
          return null
        })}
      </div>
    )
  }

  // 渲染一个思考块
  const renderThinkingBlock = (segment, segIdx) => {
    const expanded = isBlockExpanded(segIdx)
    const isLastAndStreaming = isStreaming && segIdx === lastThinkingIdx
    // 第一个 thinking 块：显示"我正在思考中..."或"思考过程"
    // 后续 thinking 块：显示"继续思考..."或"补充思考"
    const isFirst = segments.findIndex(s => s.category === 'thinking') === segIdx

    return (
      <div key={`thinking-${segIdx}`} className={`thinking-block ${expanded ? 'expanded' : 'collapsed'}`}>
        <div className="thinking-block-header" onClick={() => toggleBlock(segIdx)}>
          <span className="thinking-block-title">
            {isLastAndStreaming ? (
              <span className="thinking-text-shimmer">{isFirst ? '我正在思考中...' : '继续思考中...'}</span>
            ) : (
              <>{isFirst ? '思考过程' : '补充思考'}</>
            )}
          </span>
          <svg className="thinking-block-chevron" width="16" height="16" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)' }}>
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </div>

        <AnimatePresence>
          {expanded && (
            <Motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.25, ease: 'easeInOut' }}
              className="thinking-block-motion"
            >
              <div ref={el => { scrollRefs.current[segIdx] = el }} className="thinking-block-body">
                {segment.items.map((item, index) => {
                  if (item.type === 'plan') {
                    return <PlanItemRenderer key={`plan-${index}`} item={item} />
                  }
                  const hasDetails = item.details && (item.details.queries?.length > 0 || item.details.keywords?.length > 0)
                  return (
                    <div key={index} className="thinking-line">
                      <div className="thinking-line-content">
                        <span className={`thinking-line-text ${(!item.isComplete && isStreaming) ? 'thinking-text-shimmer' : ''}`}>{item.text}</span>
                        {hasDetails && (
                          <div className="thinking-details">
                            {item.details.queries?.map((q, i) => (
                              <div key={`q-${i}`} className="thinking-detail-item">
                                <span className="thinking-detail-label">Query</span>
                                <span className="thinking-detail-value">{q}</span>
                              </div>
                            ))}
                            {item.details.keywords?.map((k, i) => (
                              <div key={`k-${i}`} className="thinking-detail-item">
                                <span className="thinking-detail-label">Keyword</span>
                                <span className="thinking-detail-value">{k}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </Motion.div>
          )}
        </AnimatePresence>
      </div>
    )
  }

  return (
    <div className="timeline-renderer">
      {/* 初始占位：streaming 中但尚无实际 timeline 内容 */}


      {/* 按时间顺序交错渲染 thinking 块和 content */}
      {segments.map((segment, segIdx) => {
        if (segment.category === 'thinking') {
          return renderThinkingBlock(segment, segIdx)
        }
        return segment.items.map((item, ci) => {
          const isLastStreamingItem = isStreaming && segIdx === lastContentSegIdx && ci === segment.items.length - 1
          return renderContent(item, `${segIdx}-${ci}`, isLastStreamingItem)
        })
      })}


    </div>
  )
}

const ChatDropdown = ({ label, value, options, onChange, disabled }) => {
  const [open, setOpen] = useState(false)
  const containerRef = useRef(null)

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const selected = options?.find((opt) => opt.value === value) || options?.[0] || null

  const handleSelect = (opt) => {
    if (disabled || opt.disabled) return
    if (onChange) {
      onChange(opt.value)
    }
    setOpen(false)
  }

  return (
    <div className="chat-dropdown" ref={containerRef}>
      <button
        type="button"
        className={`chat-dropdown-trigger ${disabled ? 'disabled' : ''}`}
        onClick={() => !disabled && setOpen((prev) => !prev)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {label && <span className="chat-dropdown-label">{label}</span>}
        <span className="chat-dropdown-value">{selected ? selected.label : ''}</span>
        <span className="chat-dropdown-icon" aria-hidden="true">▾</span>
      </button>
      {open && options && options.length > 0 && (
        <div className="chat-dropdown-menu" role="listbox">
          {options.map((opt) => {
            // 推理模型特殊提示
            const tooltipText = opt.is_reasoning
              ? '推理模型暂不支持问答（不支持工具调用）'
              : ''

            return (
              <button
                key={opt.value}
                type="button"
                className={`chat-dropdown-option ${opt.value === value ? 'active' : ''} ${opt.disabled ? 'disabled' : ''} ${opt.is_reasoning ? 'reasoning-mode' : ''}`}
                onClick={() => handleSelect(opt)}
                disabled={opt.disabled}
                title={tooltipText}
              >
                {opt.label}
                {opt.is_reasoning && (
                  <span className="chat-reasoning-tag">
                    深度思考
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}


const DocumentChat = forwardRef(({ documentId, summary, flowchart, sessionId, initialMessage, onNoteSaved, onHistoryUpdated, eurekaId, eurekaData, selectedSourceIds = [] }, ref) => {
  const summaryAnimateIn = useFirstEntrance()
  const navigate = useNavigate()
  const isLocalMode = useRuntimeStore((s) => s.deployMode === 'local')
  // 1. 基本常量与派生状态
  const isEurekaMode = !!eurekaId
  const effectiveId = isEurekaMode ? eurekaId : documentId
  const apiBase = isEurekaMode ? `/api/knowledge-graph/eureka/${eurekaId}` : `/api/document/${documentId}`
  const maxIterations = 4

  // 2. 所有的 useState 声明
  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isLoadingHistory, setIsLoadingHistory] = useState(true)
  const [editingMessageId, setEditingMessageId] = useState(null)
  const [copySuccess, setCopySuccess] = useState(false)
  const [savingAIToNotes, setSavingAIToNotes] = useState(false)
  const [expandedSources, setExpandedSources] = useState({})
  const [collapsedSourceBlocks, setCollapsedSourceBlocks] = useState({})
  const [hoveredMessageIndex, setHoveredMessageIndex] = useState(null)
  const [retrievalMode] = useState('auto')
  const [isMultiline, setIsMultiline] = useState(false)
  const [showScrollButton, setShowScrollButton] = useState(false)
  const [scrollTrigger, setScrollTrigger] = useState(0)
  const [showScrollSpacer, setShowScrollSpacer] = useState(false)
  const [userCredits, setUserCredits] = useState(null)
  const [referenceContent, setReferenceContent] = useState(null)
  const [currentSessionId, setCurrentSessionId] = useState(() => {
    return sessionId || `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
  })

  // 3. 所有的 useRef 声明
  const chatInputRef = useRef(null)
  const chatFormRef = useRef(null)
  const hoverTimeoutRef = useRef(null)
  const sessionClearTimeoutRef = useRef(null)
  const isUserScrollingRef = useRef(false)
  const pendingScrollIndexRef = useRef(null)
  const isUserSendingMessageRef = useRef(false)
  // 当前进行中的流式请求；切换文档 / 会话或卸载时中止，避免旧回答写进新对话
  const streamControllerRef = useRef(null)
  // 历史加载请求序号：只采纳最近一次请求的结果（快速切换文档/会话时丢弃迟到的旧响应）
  const historyRequestSeqRef = useRef(0)

  // 4. Store 与 Hook 实例
  const chatGenLoading = useDocumentStore(s => s.isChatGenerating)
  const setChatGenLoading = useDocumentStore(s => s.setIsChatGenerating)

  // 5. 核心工具函数 (确保在 useEffect/useMemo 之前定义)

  const fetchUserCredits = async () => {
    // 本地版不计费，后端也没有积分接口
    if (isLocalRuntime()) return
    try {
      const response = await apiGet('api/user/credits', { cacheMode: 'no-cache' })
      if (response && response.success) {
        setUserCredits(response.data.balance)
      }
    } catch (error) {
      console.error('[DocumentChat] Fetch credits failed:', error)
    }
  }

  const scheduleScrollToMessage = (index) => {
    pendingScrollIndexRef.current = index
    setScrollTrigger(prev => prev + 1)
  }

  const handleScroll = () => {
    if (!messagesContainerRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef.current
    const distanceToBottom = scrollHeight - scrollTop - clientHeight
    const isBottom = distanceToBottom < 50
    setShowScrollButton(!isBottom)
    isUserScrollingRef.current = distanceToBottom > 100
  }

  // 6. 复杂的 Memo (依赖上述声明)

  const shouldAutoScroll = useMemo(() => {
    const hasActiveClarification = messages.some(m => m.isClarification && !m.answerStarted && !m.selectedOption)
    if (hasActiveClarification || isUserSendingMessageRef.current) return false
    return true
  }, [messages])

  const { scrollRef: messagesContainerRef, scrollToBottom } = useAutoScroll([messages], shouldAutoScroll)

  // 7. 所有的 useEffect

  useEffect(() => {
    fetchUserCredits()
  }, [])

  // 卸载时中止进行中的流
  useEffect(() => {
    return () => {
      const active = streamControllerRef.current
      streamControllerRef.current = null
      active?.abort()
    }
  }, [])

  useEffect(() => {
    // 如果没有待处理的滚动请求，直接返回
    if (pendingScrollIndexRef.current === null) return

    // 如果容器还没准备好，等待下一次
    if (!messagesContainerRef.current) return

    const targetIndex = pendingScrollIndexRef.current

    const tryScroll = (retryCount = 0) => {
      if (!messagesContainerRef.current) return
      const container = messagesContainerRef.current
      const messageElements = container.querySelectorAll('.chat-message')
      const targetElement = messageElements[targetIndex]

      if (targetElement) {
        pendingScrollIndexRef.current = null
        const targetOffsetTop = targetElement.offsetTop
        const paddingTop = 76
        const extraMargin = 10
        const scrollTarget = Math.max(0, targetOffsetTop - paddingTop - extraMargin)
        container.scrollTo({ top: scrollTarget, behavior: 'smooth' })
      } else if (retryCount < 5) {
        requestAnimationFrame(() => {
          setTimeout(() => tryScroll(retryCount + 1), 50)
        })
      } else {
        pendingScrollIndexRef.current = null
      }
    }
    requestAnimationFrame(() => tryScroll())
  }, [scrollTrigger, messages])

  useEffect(() => {
    if (!isLoading) {
      // 检查是否有活跃的追问（等待用户选择）
      const hasActiveClarification = messages.some(m => m.isClarification && !m.answerStarted && !m.selectedOption)

      if (hasActiveClarification) {
        // 有追问时，保持占位符不变，保持锁定状态
        // 这是关键！如果此时移除占位符，会导致布局变化，浏览器会调整滚动位置
        return
      }

      if (showScrollSpacer) {
        // AI 回复真正完成（没有追问），延迟一点隐藏占位符，让内容先渲染
        setTimeout(() => {
          setShowScrollSpacer(false)
        }, 100)
      }
      // 解除置顶锁定，恢复正常的自动底部跟随
      isUserSendingMessageRef.current = false
    }
  }, [isLoading, showScrollSpacer, messages])

  // 🆕 处理初始消息（如从批注创建的新对话）
  useEffect(() => {
    if (initialMessage && !isLoading && messages.length === 0) {
      // 稍微延迟确保组件完全就绪
      setTimeout(() => {
        sendQuestion(initialMessage.question, initialMessage.internalMarker)
      }, 300)
    }
  }, [initialMessage]) // 仅在挂载或 initialMessage 变化时检查

  // 监听消息变化，自动滚动到底部（除非用户正在查看历史）
  // ⚠️ 注意：useAutoScroll 已经处理了大部分滚动逻辑，这里主要处理一些边界情况
  // 比如：加载历史后滚动、AI 回复完成后的状态重置等

  // 加载历史后滚动到底部
  useEffect(() => {
    if (messages.length > 0 && !isLoadingHistory) {
      if (isUserSendingMessageRef.current) {
        return
      }
      scrollToBottom('auto')
    }
  }, [isLoadingHistory])

  // 清理定时器和重置 hover 状态
  useEffect(() => {
    return () => {
      if (hoverTimeoutRef.current) {
        clearTimeout(hoverTimeoutRef.current)
      }
      setHoveredMessageIndex(null)
    }
  }, [messages])


  // 加载历史对话
  useEffect(() => {
    // 切换文档 / 会话：中止上一段对话仍在进行的流式回答
    const activeStream = streamControllerRef.current
    streamControllerRef.current = null
    activeStream?.abort()

    if (effectiveId) {
      if (sessionId) {
        // 如果有新的 sessionId，清除之前的清除定时器
        if (sessionClearTimeoutRef.current) {
          clearTimeout(sessionClearTimeoutRef.current)
          sessionClearTimeoutRef.current = null
        }

        setCurrentSessionId(sessionId)
        setEditingMessageId(null) // 切换会话时重置编辑状态
        setInputValue('') // 切换会话时清空输入框
        loadChatHistory(sessionId)
      } else {
        // 如果没有 sessionId，说明父组件可能正在初始化会话
        // 设置一个短暂的延时，如果短时间内 sessionId 恢复，就不显示骨架屏
        if (sessionClearTimeoutRef.current) {
          clearTimeout(sessionClearTimeoutRef.current)
        }

        // 作废仍在途中的历史请求，避免旧会话的记录晚到后写回
        historyRequestSeqRef.current += 1
        sessionClearTimeoutRef.current = setTimeout(() => {
          setIsLoadingHistory(true)
          setMessages([])
        }, 100) // 100ms 防抖
      }
    }

    // 清理函数
    return () => {
      if (sessionClearTimeoutRef.current) {
        clearTimeout(sessionClearTimeoutRef.current)
      }
    }
  }, [effectiveId, sessionId])

  const loadChatHistory = async (loadSessionId) => {
    // 只有最近一次请求的结果会被采纳：用户快速切换文档/会话时，
    // 先发出的请求可能后返回，不能把 A 文档的对话写进 B
    const requestSeq = ++historyRequestSeqRef.current
    const isStale = () => requestSeq !== historyRequestSeqRef.current

    try {
      setIsLoadingHistory(true)

      // 构建URL，如果有session_id就加上
      let url = `${apiBase}/history?limit=50`
      if (loadSessionId) {
        url += `&session_id=${loadSessionId}`
      }

      const response = await apiRequest(url)
      if (isStale()) return

      if (response.ok) {
        const data = await response.json()
        if (isStale()) return
        if (data.success && data.data) {
          // 转换历史记录为消息格式，保留数据库ID和元数据
          const historyMessages = []
          // 🔧 追踪已经处理过的记录索引（用于跳过被合并的追问后续）
          const processedIndices = new Set()

          for (let i = 0; i < data.data.length; i++) {
            // 跳过已处理的记录
            if (processedIndices.has(i)) {
              continue
            }

            const item = data.data[i]

            const isPlaceholderMessage = item.answer && item.answer.trim() === '会话已创建。'
            if (isPlaceholderMessage) {
              continue
            }

            // 如果是追问，去掉标记用于显示
            let displayContent = item.question
            const isAnnotationFollowup = item.question && item.question.includes('[ANNOTATION_FOLLOWUP]')
            if (isAnnotationFollowup) {
              displayContent = item.question.replace('[ANNOTATION_FOLLOWUP]', '').trim()
            }

            // 🔧 追问处理：向前扫描找出完整的追问链
            if (item.is_clarification) {
              // 🔧 首先添加用户的原始问题（触发第一个追问的问题）
              historyMessages.push({
                id: item.id,
                type: 'user',
                content: displayContent,
                timestamp: new Date(item.created_at)
              })

              // 向前扫描，收集所有连续的追问记录
              const clarificationChain = []
              let scanIndex = i

              while (scanIndex < data.data.length && data.data[scanIndex].is_clarification) {
                clarificationChain.push(data.data[scanIndex])
                processedIndices.add(scanIndex)
                scanIndex++
              }

              // scanIndex 现在指向第一个非追问记录（最终回答）或数组末尾
              const finalAnswerRecord = scanIndex < data.data.length ? data.data[scanIndex] : null
              if (finalAnswerRecord && !finalAnswerRecord.is_clarification) {
                processedIndices.add(scanIndex)
              }

              // 构建合并的 AI 消息
              const messageObj = {
                type: 'ai',
                content: '', // 会被设置为最终回答或最后一个追问问题
                sources: item.sources,
                flowchart: null,
                chartData: null,
                usedRag: false,
                iterations: 0,
                duration: 0,
                statusHistory: [],
                timestamp: new Date(item.created_at),
                isClarification: true,
                clarificationContent: '', // 当前追问的问题
                clarificationOptions: [], // 当前追问的选项
                clarificationHistory: [], // 历史追问链
                selectedOptions: [], // 🔧 改为数组支持多选
                isSubmitted: false, // 🔧 新增：是否已提交
                answerStarted: false,
                id: item.id,
                timeline: [] // 🆕 Initialize timeline for history messages
              }

              // 构建追问历史链
              for (let j = 0; j < clarificationChain.length; j++) {
                const clarItem = clarificationChain[j]
                const nextInChain = clarificationChain[j + 1]

                // 选中的选项是下一条记录的 question（如果存在）
                const selectedOpt = nextInChain ? nextInChain.question : (finalAnswerRecord ? finalAnswerRecord.question : null)

                if (j < clarificationChain.length - 1) {
                  // 不是最后一个追问 -> 添加到历史
                  messageObj.clarificationHistory.push({
                    question: clarItem.answer, // AI 的追问问题
                    selectedOption: selectedOpt // 用户选择的选项
                  })
                } else {
                  // 最后一个追问 -> 设置为当前显示的追问
                  messageObj.clarificationContent = clarItem.answer
                  messageObj.clarificationOptions = clarItem.clarification_options || []
                  // 🔧 将选项转为数组格式
                  messageObj.selectedOptions = selectedOpt ? [selectedOpt] : []
                  messageObj.isSubmitted = !!selectedOpt // 有选项说明已提交

                  // 累计元数据
                  messageObj.iterations += clarItem.search_count || 0
                  messageObj.duration += clarItem.elapsed_time || 0
                  if (clarItem.status_history) {
                    const filteredSH = clarItem.status_history.filter(s => s.type !== 'content_boundary')
                    messageObj.statusHistory.push(...filteredSH.map(s => ({ ...s, isComplete: true })))
                    messageObj.timeline.push(...filteredSH.map(s => ({ type: 'status', ...s, isComplete: true, timestamp: new Date(clarItem.created_at) })))
                  }
                }
              }

              // 合并最终回答
              if (finalAnswerRecord && !finalAnswerRecord.is_clarification) {
                messageObj.content = finalAnswerRecord.answer
                messageObj.answerStarted = true
                messageObj.usedRag = (finalAnswerRecord.search_count || 0) > 0
                messageObj.iterations += finalAnswerRecord.search_count || 0
                messageObj.duration += finalAnswerRecord.elapsed_time || 0
                if (finalAnswerRecord.flowchart) messageObj.flowchart = finalAnswerRecord.flowchart
                if (finalAnswerRecord.chart_data) messageObj.chartData = finalAnswerRecord.chart_data
                if (finalAnswerRecord.sources) messageObj.sources = finalAnswerRecord.sources
                if (finalAnswerRecord.status_history) {
                  messageObj.statusHistory.push(...finalAnswerRecord.status_history.map(s => ({ ...s, isComplete: true })))

                  // 🆕 Add plan if exists (Important for persistence!)
                  if (finalAnswerRecord.plan) {
                    messageObj.timeline.push({
                      type: 'plan',
                      steps: finalAnswerRecord.plan,
                      currentStep: finalAnswerRecord.plan.length,
                      isComplete: true,
                      timestamp: new Date(finalAnswerRecord.created_at)
                    })
                    messageObj.plan = finalAnswerRecord.plan
                  }

                  // 🆕 利用 content_boundary 标记重建交错时间线
                  const sh = finalAnswerRecord.status_history
                  const ansText = finalAnswerRecord.answer || ''
                  const hasBnd = sh.some(s => s.type === 'content_boundary')

                  if (hasBnd && ansText) {
                    let curGroup = []
                    const offsets = []
                    const groups = []
                    for (const entry of sh) {
                      if (entry.type === 'content_boundary') {
                        groups.push(curGroup)
                        offsets.push(entry.offset || 0)
                        curGroup = []
                      } else {
                        curGroup.push(entry)
                      }
                    }
                    if (curGroup.length > 0) groups.push(curGroup)

                    if (groups[0]?.length > 0) {
                      messageObj.timeline.push(...groups[0].map(s => ({
                        type: 'status', ...s, isComplete: true, timestamp: new Date(finalAnswerRecord.created_at)
                      })))
                    }
                    for (let i = 0; i < offsets.length; i++) {
                      const sOff = i === 0 ? 0 : offsets[i - 1]
                      const slice = ansText.slice(sOff, offsets[i])
                      if (slice) {
                        messageObj.timeline.push({ type: 'content', text: slice, timestamp: new Date(finalAnswerRecord.created_at) })
                      }
                      const ng = groups[i + 1]
                      if (ng?.length > 0) {
                        messageObj.timeline.push(...ng.map(s => ({
                          type: 'status', ...s, isComplete: true, timestamp: new Date(finalAnswerRecord.created_at)
                        })))
                      }
                    }
                    const lastOff = offsets[offsets.length - 1]
                    const rem = ansText.slice(lastOff)
                    if (rem) {
                      messageObj.timeline.push({ type: 'content', text: rem, timestamp: new Date(finalAnswerRecord.created_at) })
                    }
                  } else {
                    messageObj.timeline.push(...sh
                      .filter(s => s.type !== 'content_boundary')
                      .map(s => ({ type: 'status', ...s, isComplete: true, timestamp: new Date(finalAnswerRecord.created_at) }))
                    )
                    if (ansText) {
                      messageObj.timeline.push({ type: 'content', text: ansText, timestamp: new Date(finalAnswerRecord.created_at) })
                    }
                  }
                } else if (messageObj.content) {
                  // 没有 status_history 但有内容
                  messageObj.timeline.push({ type: 'content', text: messageObj.content, timestamp: new Date(finalAnswerRecord.created_at) })
                }
              } else {
                // 没有最终回答（追问链未完成），显示最后一个追问
                messageObj.content = messageObj.clarificationContent
                messageObj.answerStarted = false
              }

              historyMessages.push(messageObj)
              continue // 完成追问消息处理
            }

            // --- 非追问：普通消息处理 ---
            // 添加用户消息
            historyMessages.push({
              id: item.id,
              type: 'user',
              content: displayContent,
              timestamp: new Date(item.created_at)
            })

            // --- 普通 AI 消息处理 ---
            const aiMessage = {
              type: 'ai',
              content: item.answer,
              sources: item.sources,
              flowchart: item.flowchart || null,
              chartData: item.chart_data || null,
              usedRag: item.search_count > 0,
              iterations: item.search_count,
              duration: item.elapsed_time,
              statusHistory: (item.status_history || []).filter(s => s.type !== 'content_boundary').map(s => ({ ...s, isComplete: true })),
              timestamp: new Date(item.created_at),
              isClarification: false,
              clarificationContent: null,
              clarificationOptions: [],
              selectedOption: null,
              answerStarted: true, // For historical messages, answer has started and completed
              timeline: [] // 🆕 Initialize timeline
            }

            // 🆕 Populate timeline for historical AI messages
            if (item.plan) {
              aiMessage.timeline.push({
                type: 'plan',
                steps: item.plan,
                currentStep: item.plan.length,
                isComplete: true,
                timestamp: new Date(item.created_at)
              })
              aiMessage.plan = item.plan
            }

            // 🆕 利用 content_boundary 标记重建交错时间线
            if (item.status_history && item.status_history.length > 0) {
              const statusHistory = item.status_history
              const answerText = item.answer || ''

              // 检查是否包含 content_boundary 标记
              const hasBoundaries = statusHistory.some(s => s.type === 'content_boundary')

              if (hasBoundaries && answerText) {
                // 按 content_boundary 分段重建交错时间线
                let currentStatusGroup = []
                const contentOffsets = [] // 收集所有 boundary 的 offset
                const statusGroups = []   // 每个 boundary 之前的 status 组

                for (const entry of statusHistory) {
                  if (entry.type === 'content_boundary') {
                    statusGroups.push(currentStatusGroup)
                    contentOffsets.push(entry.offset || 0)
                    currentStatusGroup = []
                  } else {
                    currentStatusGroup.push(entry)
                  }
                }
                // 最后剩余的 status（boundary 之后的）
                if (currentStatusGroup.length > 0) {
                  statusGroups.push(currentStatusGroup)
                }

                // 第一组 status（content 开始之前的）
                if (statusGroups.length > 0 && statusGroups[0].length > 0) {
                  aiMessage.timeline.push(...statusGroups[0].map(s => ({
                    type: 'status', ...s, isComplete: true, timestamp: new Date(item.created_at)
                  })))
                }

                // 交错插入 content 段和后续 status 组
                for (let i = 0; i < contentOffsets.length; i++) {
                  const startOffset = i === 0 ? 0 : contentOffsets[i - 1]
                  const endOffset = contentOffsets[i]
                  const contentSlice = answerText.slice(startOffset, endOffset)
                  if (contentSlice) {
                    aiMessage.timeline.push({
                      type: 'content', text: contentSlice, timestamp: new Date(item.created_at)
                    })
                  }
                  // 这个 boundary 之后的 status 组（索引 i+1）
                  const nextGroup = statusGroups[i + 1]
                  if (nextGroup && nextGroup.length > 0) {
                    aiMessage.timeline.push(...nextGroup.map(s => ({
                      type: 'status', ...s, isComplete: true, timestamp: new Date(item.created_at)
                    })))
                  }
                }

                // 最后一个 boundary offset 之后的剩余内容
                const lastOffset = contentOffsets[contentOffsets.length - 1]
                const remainingContent = answerText.slice(lastOffset)
                if (remainingContent) {
                  aiMessage.timeline.push({
                    type: 'content', text: remainingContent, timestamp: new Date(item.created_at)
                  })
                }
              } else {
                // 无 boundary 标记 — 旧数据，回退到原始逻辑
                aiMessage.timeline.push(...statusHistory
                  .filter(s => s.type !== 'content_boundary')
                  .map(s => ({ type: 'status', ...s, isComplete: true, timestamp: new Date(item.created_at) }))
                )
                if (answerText) {
                  aiMessage.timeline.push({ type: 'content', text: answerText, timestamp: new Date(item.created_at) })
                }
              }
            } else if (item.answer) {
              aiMessage.timeline.push({ type: 'content', text: item.answer, timestamp: new Date(item.created_at) })
            }

            historyMessages.push(aiMessage)
          }

          setMessages(historyMessages)


        }
      }
    } catch (err) {
      void err;
    } finally {
      if (!isStale()) {
        setIsLoadingHistory(false)
      }
    }
  }

  // 编辑消息
  const handleEditMessage = (index) => {
    const message = messages[index]
    setEditingMessageId(index)
    // 填充到底部输入框
    setInputValue(message.content)
    // 聚焦输入框
    setTimeout(() => {
      const textarea = document.querySelector('.chat-input-textarea')
      if (textarea) {
        textarea.focus()
        // 自动调整高度
        textarea.style.height = 'auto'
        textarea.style.height = `${textarea.scrollHeight}px`
      }
    }, 100)
  }

  // 取消编辑
  const handleCancelEdit = () => {
    setEditingMessageId(null)
    setInputValue('')
    if (chatInputRef.current) {
      chatInputRef.current.style.height = ''
    }
  }

  // 保存AI 回复到笔记（普通对话下的"添加到笔记"按钮）
  const handleSaveToNotes = async (content) => {
    if (savingAIToNotes) return

    setSavingAIToNotes(true)
    try {
      let endpoint = '/api/notes'
      let noteData = {}

      if (isEurekaMode) {
        endpoint = `/api/eureka/${eurekaId}/notes`
        noteData = {
          title: '灵感讨论笔记',
          content: content
        }
      } else {
        noteData = {
          document_id: documentId,
          content: content,
          type: 'ai_response',
          title: 'AI 回复'
        }
      }

      const response = await apiRequest(endpoint, {
        method: 'POST',
        body: JSON.stringify(noteData)
      })

      if (!response.ok) {
        // 尝试解析错误信息（后端统一错误体为 {code, message, details}）
        let detail = '保存失败'
        try {
          const errorData = await response.json()
          detail = getApiErrorMessage(errorData, detail)
        } catch {
          // ignore parse error
        }
        throw new Error(detail)
      }

      const data = await response.json()
      if (!data.success) {
        throw new Error(data.message || '保存失败')
      }

      // 成功：通知父组件刷新右侧笔记
      if (onNoteSaved) {
        onNoteSaved()
      }
    } catch (err) {
      const message = err?.message || ''
      if (message.includes('登录已过期') || message.includes('未授权')) {
        alert('登录已过期，请重新登录后再保存到笔记')
        window.location.reload()
        return
      }
      alert(message || '保存笔记失败，请重试')
    } finally {
      setSavingAIToNotes(false)
    }
  }

  // 对话生成（闪卡/测验/提炼）
  const handleChatGenerate = async (type) => {
    if (chatGenLoading || !documentId || !currentSessionId) return

    setChatGenLoading(type)
    try {
      const endpoint = `/api/document/${documentId}/generate-chat-${type}`
      const response = await apiRequest(endpoint, {
        method: 'POST',
        body: JSON.stringify({ session_id: currentSessionId })
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '提交任务失败'))
      }

      const data = await response.json()
      if (!data.success || !data.task_id) throw new Error('任务提交失败')

      // 轮询任务状态
      const maxAttempts = 60
      let attempts = 0

      const poll = async () => {
        attempts++
        try {
          const statusRes = await apiRequest(`/api/generation-task/${data.task_id}/status`)
          if (!statusRes.ok) {
            if (attempts < maxAttempts) { setTimeout(poll, 2000); return }
            throw new Error('轮询超时')
          }
          const statusData = await statusRes.json()

          if (statusData.status === 'completed') {
            setChatGenLoading(null)
            // 失效对应类型的历史缓存，触发 RightPanel 实时刷新
            const store = useDocumentStore.getState()
            store.invalidateDocumentHistoryCache(documentId, type)
          } else if (statusData.status === 'failed') {
            throw new Error(statusData.error || '生成失败')
          } else if (attempts < maxAttempts) {
            setTimeout(poll, 2000)
          } else {
            throw new Error('生成超时，请稍后在记录中查看')
          }
        } catch (err) {
          setChatGenLoading(null)
          alert(err.message || '生成失败')
        }
      }

      setTimeout(poll, 2000)
    } catch (err) {
      setChatGenLoading(null)
      alert(err.message || '生成失败，请重试')
    }
  }

  // 保存编辑并重新生成
  const handleSaveEdit = async () => {
    if (editingMessageId === null) return

    const index = editingMessageId
    const message = messages[index]
    const contentToSend = inputValue // 使用底部输入框的内容

    try {
      // 如果消息有 id（已保存的历史消息），需要先删除后续对话
      if (message.id) {
        const response = await apiRequest(`${apiBase}/history/after/${message.id}`, {
          method: 'DELETE'
        })

        if (!response.ok) {
          throw new Error('删除历史记录失败')
        }
      }

      // 截断本地消息到编辑点
      const newMessages = messages.slice(0, index)
      setMessages(newMessages)

      // 退出编辑模式
      setEditingMessageId(null)
      setInputValue('')
      if (chatInputRef.current) {
        chatInputRef.current.style.height = ''
      }

      // 重新提问
      await sendQuestion(contentToSend, null, newMessages)

    } catch (err) {
      console.error(err)
      alert('编辑失败，请重试')
    }
  }

  // 引用内容（Send to Chat功能）

  // 暴露方法给父组件
  useImperativeHandle(ref, () => ({
    sendQuestion,
    setReferenceContent, // 新增：设置引用内容
    setInputText: (text) => {
      setInputValue(text)
      setTimeout(() => {
        if (chatInputRef.current) {
          chatInputRef.current.style.height = 'auto'
          chatInputRef.current.style.height = Math.min(chatInputRef.current.scrollHeight, 120) + 'px'
          chatInputRef.current.focus()
        }
      }, 0)
    }
  }))
  // 🆕 处理追问多选提交 - 合并所有选中的选项发送
  const handleClarificationSubmit = async (messageIndex, selectedOptions) => {
    if (isLoading || !selectedOptions || selectedOptions.length === 0) return

    // 合并选项为单个问题字符串
    const combinedQuestion = selectedOptions.join('、')

    // 1. 标记已提交状态并开启流式状态，初始化 timeline
    setMessages(prev => {
      const newMsgs = [...prev]
      if (messageIndex >= 0 && newMsgs[messageIndex]) newMsgs[messageIndex] = {
        ...newMsgs[messageIndex],
        selectedOptions: selectedOptions,
        isSubmitted: true,
        isStreaming: true,
        status: '',
        statusHistory: [],
        // 🆕 初始化 timeline 用于交错显示状态和内容
        timeline: [],
        hasContentStarted: false
      }
      return newMsgs
    })

    setIsLoading(true)

    // 每个流独立的中止控制与 token 缓冲（见 createTokenBuffer）
    streamControllerRef.current?.abort()
    const controller = new AbortController()
    streamControllerRef.current = controller
    const tokenBuffer = createTokenBuffer((bufferedContent) => {
      setMessages(prev => {
        const newMsgs = [...prev]
        if (messageIndex >= 0 && newMsgs[messageIndex]?.isStreaming) {
          const msg = { ...newMsgs[messageIndex] }

          if (!msg.answerStarted) {
            msg.clarificationContent = msg.clarificationContent || msg.content || ''
            msg.content = ''
            msg.answerStarted = true
            msg.hasContentStarted = true
          }

          msg.content = (msg.content || '') + bufferedContent
          msg.status = null

          // 🆕 推送内容到 timeline
          const timeline = [...(msg.timeline || [])]
          const lastItem = timeline.length > 0 ? timeline[timeline.length - 1] : null
          if (lastItem && lastItem.type === 'content') {
            // 追加到最后一个 content 项
            timeline[timeline.length - 1] = {
              ...lastItem,
              text: (lastItem.text || '') + bufferedContent,
              timestamp: Date.now()
            }
          } else {
            // 新建 content 项
            timeline.push({
              type: 'content',
              text: bufferedContent,
              timestamp: Date.now()
            })
          }
          msg.timeline = timeline

          newMsgs[messageIndex] = msg
        }
        return newMsgs
      })
    })

    try {
      // 2. 发送请求
      const formData = new FormData()
      formData.append('question', combinedQuestion)

      if (currentSessionId) {
        formData.append('session_id', currentSessionId)
      }

      // 准备对话历史
      const conversationHistory = []
      for (let i = 0; i < messages.length; i += 2) {
        if (messages[i] && messages[i].type === 'user' && messages[i + 1] && messages[i + 1].type === 'ai') {
          conversationHistory.push({
            question: messages[i].content,
            answer: messages[i + 1].content
          })
        }
      }

      // 将当前追问消息的内容添加到历史中
      const currentClarificationMsg = messages[messageIndex]
      if (currentClarificationMsg && currentClarificationMsg.type === 'ai' && currentClarificationMsg.isClarification) {
        let triggerQuestion = '用户的问题'
        for (let i = messageIndex - 1; i >= 0; i--) {
          if (messages[i] && messages[i].type === 'user') {
            triggerQuestion = messages[i].content
            break
          }
        }
        conversationHistory.push({
          question: triggerQuestion,
          answer: `我需要确认一下：${currentClarificationMsg.clarificationContent || currentClarificationMsg.content} 选项包括：${(currentClarificationMsg.clarificationOptions || []).join('、')}`
        })
      }

      const recentHistory = conversationHistory.slice(-10)
      formData.append('conversation_history', JSON.stringify(recentHistory))
      formData.append('selected_source_ids', JSON.stringify(selectedSourceIds || []))

      formData.append('use_iterative', 'true')
      formData.append('max_iterations', maxIterations.toString())
      formData.append('is_agent_mode', 'true')

      formData.append('stream', 'true')

      const response = await apiRequest(`${apiBase}/chat`, {
        method: 'POST',
        body: formData,
        signal: controller.signal,
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '问答失败'))
      }

      // 3. 流式读取响应
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue

          try {
            const jsonStr = line.slice(6).trim()
            if (!jsonStr) continue

            const payload = JSON.parse(jsonStr)
            const eventType = payload.event
            const data = payload.data

            switch (eventType) {
              case 'status':
                // 🧠 Kimi Thinking Stream Logic
                if (data.type === 'thinking_stream') {
                  setMessages(prev => {
                    const newMsgs = [...prev]
                    if (messageIndex >= 0 && newMsgs[messageIndex]?.isStreaming) {
                      const msg = { ...newMsgs[messageIndex] }

                      // Update Status History (Merge)
                      const prevHistory = [...(msg.statusHistory || [])]
                      const lastStatus = prevHistory.length > 0 ? prevHistory[prevHistory.length - 1] : null

                      const statusItem = { ...data, isComplete: false }

                      if (lastStatus && lastStatus.type === 'thinking_stream') {
                        // Append to existing thinking block (immutable update)
                        prevHistory[prevHistory.length - 1] = {
                          ...lastStatus,
                          text: `${lastStatus.text || ''}${data.text || ''}`
                        }
                      } else {
                        // New thinking block
                        // Mark previous as complete
                        prevHistory.forEach(item => item.isComplete = true)
                        prevHistory.push(statusItem)
                      }
                      msg.statusHistory = prevHistory
                      msg.status = "思考中..."

                      // Update Timeline (Merge)
                      const timeline = [...(msg.timeline || [])]
                      const lastTL = timeline.length > 0 ? timeline[timeline.length - 1] : null

                      if (lastTL && lastTL.type === 'thinking_stream') {
                        // Append (immutable update)
                        timeline[timeline.length - 1] = {
                          ...lastTL,
                          text: `${lastTL.text || ''}${data.text || ''}`,
                          timestamp: Date.now()
                        }
                      } else {
                        // New (Note: spread statusItem puts 'type'='thinking_stream' last, overwriting 'status')
                        timeline.push({ type: 'status', ...statusItem, timestamp: Date.now() })
                      }
                      msg.timeline = timeline

                      newMsgs[messageIndex] = msg
                    }
                    return newMsgs
                  })
                  break
                }

                // Standard Status Logic
                setMessages(prev => {
                  const newMsgs = [...prev]
                  if (messageIndex >= 0 && newMsgs[messageIndex]?.isStreaming) {
                    const msg = { ...newMsgs[messageIndex] }
                    const statusItem = { ...data, isComplete: false }

                    // 🔧 将之前的状态标记为完成（新状态到来意味着旧状态已完成）
                    const prevHistory = (msg.statusHistory || []).map(item => ({ ...item, isComplete: true }))
                    msg.statusHistory = [...prevHistory, statusItem]
                    msg.status = data.text

                    // 🆕 同时更新 timeline
                    const timeline = (msg.timeline || []).map(item =>
                      item.type === 'status' ? { ...item, isComplete: true } : item
                    )
                    timeline.push({ ...data, type: 'status', isComplete: false, timestamp: Date.now() })
                    msg.timeline = timeline

                    newMsgs[messageIndex] = msg
                  }
                  return newMsgs
                })
                break

              case 'token':
                tokenBuffer.push(data)
                break

              case 'flowchart':
                setMessages(prev => {
                  const newMsgs = [...prev]
                  if (messageIndex >= 0 && newMsgs[messageIndex]) {
                    const msg = { ...newMsgs[messageIndex] }
                    msg.flowchart = data
                    newMsgs[messageIndex] = msg
                  }
                  return newMsgs
                })
                break

              case 'chart':
                setMessages(prev => {
                  const newMsgs = [...prev]
                  if (messageIndex >= 0 && newMsgs[messageIndex]) {
                    const msg = { ...newMsgs[messageIndex] }
                    msg.chartData = data
                    newMsgs[messageIndex] = msg
                  }
                  return newMsgs
                })
                break

              case 'done':
                // 先写入缓冲中的最后一批 token，再结束流式状态
                tokenBuffer.flush()
                setMessages(prev => {
                  const newMsgs = [...prev]
                  if (messageIndex >= 0 && newMsgs[messageIndex]) {
                    const msg = { ...newMsgs[messageIndex] }
                    msg.isStreaming = false
                    msg.status = null
                    if (msg.statusHistory) {
                      msg.statusHistory = msg.statusHistory.map(item => ({ ...item, isComplete: true }))
                    }
                    // 🔧 同时更新 timeline 中的状态项 (包括 Plan)
                    if (msg.timeline) {
                      msg.timeline = msg.timeline.map(item => {
                        if (item.type === 'status') return { ...item, isComplete: true }
                        if (item.type === 'plan') return { ...item, isComplete: true }
                        return item
                      })
                    }
                    msg.usedRag = data.used_rag
                    msg.iterations = data.search_count || data.iterations || 0  // 优先使用 search_count
                    msg.duration = data.duration
                    newMsgs[messageIndex] = msg
                  }
                  return newMsgs
                })
                if (onHistoryUpdated) {
                  onHistoryUpdated()
                }
                break

              case 'clear_content':
                // Agent 调用工具前的思考过程 — 直接清空当前内容
                setMessages(prev => {
                  const newMsgs = [...prev]
                  if (messageIndex >= 0 && newMsgs[messageIndex]) {
                    const msg = { ...newMsgs[messageIndex] }
                    msg.content = ''
                    msg.answerStarted = false
                    newMsgs[messageIndex] = msg
                  }
                  return newMsgs
                })
                break

              case 'error':
                tokenBuffer.flush()
                throw createStreamError(data)

              case 'clarification':
                tokenBuffer.flush()
                // 如果后端再次触发追问，追加到历史
                setMessages(prev => {
                  const newMsgs = [...prev]
                  if (messageIndex >= 0 && newMsgs[messageIndex]) {
                    const msg = { ...newMsgs[messageIndex] }
                    const previousQuestion = msg.clarificationContent || msg.content
                    const previousSelection = msg.selectedOptions

                    msg.clarificationHistory = [
                      ...(msg.clarificationHistory || []),
                      { question: previousQuestion, selectedOption: previousSelection ? previousSelection.join('、') : null }
                    ]

                    msg.content = data.question
                    msg.clarificationContent = data.question
                    msg.clarificationOptions = data.options || []
                    msg.selectedOptions = []
                    msg.isSubmitted = false
                    msg.isStreaming = false
                    msg.answerStarted = false
                    newMsgs[messageIndex] = msg
                  }
                  return newMsgs
                })
                break

              default:
                break
            }
          } catch (e) {
            // 后端在流中报告的错误要交给外层展示，不能当作解析错误吞掉
            if (e?.isStreamError) throw e
            console.error('Parse error:', e, line)
          }
        }
      }

    } catch (err) {
      // 已切换文档/会话或组件卸载：静默结束
      if (controller.signal.aborted) return

      tokenBuffer.flush()
      const reason = err?.name === 'TypeError' ? '网络连接失败' : normalizeTaskErrorMessage(err?.message, '')
      setMessages(prev => {
        const newMsgs = [...prev]
        if (messageIndex >= 0 && newMsgs[messageIndex]) {
          newMsgs[messageIndex] = {
            ...newMsgs[messageIndex],
            isStreaming: false,
            status: null,
            content: (newMsgs[messageIndex].content || '') + (reason ? `\n\n抱歉，处理时出了点问题：${reason}` : '\n\n抱歉，处理时出了点问题，请重试。')
          }
        }
        return newMsgs
      })
    } finally {
      if (streamControllerRef.current === controller) {
        streamControllerRef.current = null
      }
      if (controller.signal.aborted) {
        // 视图已切换：丢弃残余 token；没有新的流接手时解除输入锁
        tokenBuffer.cancel()
        if (!streamControllerRef.current) setIsLoading(false)
      } else {
        // 流结束但未收到 done（后端断开等）：写入残余 token 并结束该消息的流式状态
        tokenBuffer.flush()
        setMessages(prev => {
          if (messageIndex < 0 || !prev[messageIndex]?.isStreaming) return prev
          const newMsgs = [...prev]
          newMsgs[messageIndex] = { ...newMsgs[messageIndex], isStreaming: false, status: null }
          return newMsgs
        })
        setIsLoading(false)
      }
    }
  }

  // 发送问题的核心函数（可被外部调用）
  // overrideMessages: 可选，用于在编辑重发时传入截断后的消息列表（避免 React state 异步更新问题）
  const sendQuestion = async (question, actualQuestion = null, overrideMessages = null) => {
    if (!question.trim() || isLoading) return

    // 新的交互会覆盖之前的关闭状态
    if (currentSessionId) {
      localStorage.removeItem(`annotation_closed_${currentSessionId}`)
    }

    const questionToSend = actualQuestion || question

    const isFirstMessage = messages.length === 0

    const userMessage = {
      type: 'user',
      content: question,
      timestamp: new Date()
    }

    // 计算用户消息将要插入的索引位置
    // 🎯 修复：如果是编辑模式 (overrideMessages)，应该基于截断后的长度计算索引
    const currentMessagesLength = overrideMessages ? overrideMessages.length : messages.length
    const userMessageIndex = isFirstMessage ? 0 : currentMessagesLength

    // 🎯 标记正在发送用户消息，防止 scrollToBottom 覆盖
    isUserSendingMessageRef.current = true
    setShowScrollSpacer(true) // 显示占位符，让用户消息能滚动到顶部

    // 🎯 立即创建 AI 消息占位符，显示"正在思考中..."
    const aiMessagePlaceholder = {
      type: 'ai',
      content: '',
      sources: [],
      timestamp: new Date(),
      isStreaming: true,
      status: '',
      statusHistory: [],
      // 🆕 时间线数组：按时间顺序存储状态和内容片段
      timeline: [],
      // 🆕 标记是否已开始输出内容（用于判断后续状态是否应该插入到内容后面）
      hasContentStarted: false
    }

    // 同时添加用户消息和 AI 占位符
    if (isFirstMessage) {
      setMessages([userMessage, aiMessagePlaceholder])
    } else {
      setMessages(prev => [...prev, userMessage, aiMessagePlaceholder])
    }

    // 🎯 关键：预约滚动，等 DOM 更新后执行
    scheduleScrollToMessage(userMessageIndex)

    setIsLoading(true)

    // 每个流独立的中止控制与 token 缓冲（见 createTokenBuffer）
    streamControllerRef.current?.abort()
    const controller = new AbortController()
    streamControllerRef.current = controller
    const tokenBuffer = createTokenBuffer((bufferedContent) => {
      setMessages(prev => {
        const newMsgs = [...prev]
        const lastIndex = newMsgs.length - 1
        if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
          const lastMsg = { ...newMsgs[lastIndex] }
          lastMsg.content = (lastMsg.content || '') + bufferedContent
          lastMsg.status = null // 清除状态，直接显示内容

          // 🆕 更新 timeline 中的内容片段
          const timeline = [...(lastMsg.timeline || [])]

          // 首次收到内容时，标记并添加内容条目
          if (!lastMsg.hasContentStarted) {
            lastMsg.hasContentStarted = true
            timeline.push({
              type: 'content',
              text: lastMsg.content,
              timestamp: Date.now()
            })
          } else {
            // 已有内容，更新最后一个 content 条目
            const lastTimelineItem = timeline.length > 0 ? timeline[timeline.length - 1] : null
            if (lastTimelineItem && lastTimelineItem.type === 'content') {
              timeline[timeline.length - 1] = {
                ...lastTimelineItem,
                text: (lastTimelineItem.text || '') + bufferedContent,
                timestamp: Date.now()
              }
            } else {
              // 如果最后一条不是 content（可能是后续状态后的新内容），添加新的 content 条目
              timeline.push({
                type: 'content',
                text: bufferedContent,
                timestamp: Date.now()
              })
            }
          }
          lastMsg.timeline = timeline

          newMsgs[lastIndex] = lastMsg
        }
        return newMsgs
      })
    })

    try {
      // 确定最终发送的问题文本
      let finalQuestionToSend = questionToSend

      let response

      if (isEurekaMode) {
        // 灵感模式使用 JSON 请求
        const jsonBody = {
          question: finalQuestionToSend,
          session_id: currentSessionId || ''
        }

        response = await apiRequest(`${apiBase}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(jsonBody),
          signal: controller.signal,
        })
      } else {
        // 文档模式使用 FormData
        const formData = new FormData()
        formData.append('question', finalQuestionToSend)

        if (currentSessionId) {
          formData.append('session_id', currentSessionId)
        }

        // 准备对话历史（只发送问题和答案，不发送其他信息）
        // 🆕 如果有 overrideMessages，使用它（编辑重发场景），否则使用 state 中的 messages
        const conversationHistory = []
        const effectiveMessages = overrideMessages !== null ? overrideMessages : messages
        const messagesToCheck = (overrideMessages !== null ? effectiveMessages : (isFirstMessage ? [] : messages))
        for (let i = 0; i < messagesToCheck.length; i += 2) {
          if (messagesToCheck[i] && messagesToCheck[i].type === 'user' && messagesToCheck[i + 1] && messagesToCheck[i + 1].type === 'ai') {
            conversationHistory.push({
              question: messagesToCheck[i].content,
              answer: messagesToCheck[i + 1].content
            })
          }
        }

        // 只保留最近 10 轮对话发送给后端
        const recentHistory = conversationHistory.slice(-10)
        formData.append('conversation_history', JSON.stringify(recentHistory))
        formData.append('selected_source_ids', JSON.stringify(selectedSourceIds || []))

        // 启用多轮迭代模式
        formData.append('use_iterative', 'true')
        formData.append('max_iterations', maxIterations.toString())
        formData.append('is_agent_mode', 'true')

        // 添加检索模式
        if (retrievalMode) {
          formData.append('retrieval_mode', retrievalMode)
        }

        // 🆕 开启流式输出
        formData.append('stream', 'true')

        response = await apiRequest(`${apiBase}/chat`, {
          method: 'POST',
          body: formData,
          signal: controller.signal,
        })
      }

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '问答失败'))
      }

      // AI 消息占位符已在发送时创建（显示"正在思考中..."），这里直接开始读取流
      // 2. 读取流
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n\n')
          buffer = lines.pop() // 保留未完成的行

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const jsonStr = line.slice(6)
                const payload = JSON.parse(jsonStr)
                const { event, data } = payload

                switch (event) {
                  case 'plan':
                    setMessages(prev => {
                      const newMsgs = [...prev]
                      const lastIndex = newMsgs.length - 1
                      if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
                        const lastMsg = { ...newMsgs[lastIndex] }
                        // Avoid duplicates
                        const timeline = [...(lastMsg.timeline || [])]
                        if (!timeline.some(item => item.type === 'plan')) {
                          timeline.push({
                            type: 'plan',
                            steps: data,
                            currentStep: 0,
                            isComplete: false,
                            timestamp: Date.now()
                          })
                          lastMsg.timeline = timeline
                          lastMsg.plan = data
                          newMsgs[lastIndex] = lastMsg
                        }
                      }
                      return newMsgs
                    })
                    break;

                  case 'status': {
                    // 🔧 关键修复：在添加状态前，强制刷新缓冲区中的内容
                    // 防止状态"插队"到尚未渲染的回答内容前面
                    const forcedBuffer = tokenBuffer.take()

                    // 🧠 Kimi Thinking Stream Logic
                    if (data.type === 'thinking_stream') {
                      setMessages(prev => {
                        const newMsgs = [...prev]
                        const lastIndex = newMsgs.length - 1
                        if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
                          const lastMsg = { ...newMsgs[lastIndex] }

                          // 1. Flush Forced Content
                          if (forcedBuffer) {
                            lastMsg.content = (lastMsg.content || '') + forcedBuffer
                            lastMsg.status = null
                            const timeline = [...(lastMsg.timeline || [])]
                            if (!lastMsg.hasContentStarted) {
                              lastMsg.hasContentStarted = true
                              timeline.push({ type: 'content', text: forcedBuffer, timestamp: Date.now() })
                            } else {
                              const lastTL = timeline.length > 0 ? timeline[timeline.length - 1] : null
                              if (lastTL && lastTL.type === 'content') {
                                timeline[timeline.length - 1] = { ...lastTL, text: (lastTL.text || '') + forcedBuffer, timestamp: Date.now() }
                              } else {
                                timeline.push({ type: 'content', text: forcedBuffer, timestamp: Date.now() })
                              }
                            }
                            lastMsg.timeline = timeline
                          }

                          // 2. Update Status History (Merge)
                          const prevHistory = [...(lastMsg.statusHistory || [])]
                          const lastStatus = prevHistory.length > 0 ? prevHistory[prevHistory.length - 1] : null
                          const statusItem = { ...data, isComplete: false }

                          if (lastStatus && lastStatus.type === 'thinking_stream') {
                            prevHistory[prevHistory.length - 1] = {
                              ...lastStatus,
                              text: `${lastStatus.text || ''}${data.text || ''}`
                            }
                          } else {
                            prevHistory.forEach(item => item.isComplete = true)
                            // Clean up text if needed? No, logic handles it.
                            prevHistory.push(statusItem)
                          }
                          lastMsg.statusHistory = prevHistory
                          lastMsg.status = "思考中..."

                          // 3. Update Timeline (Merge)
                          const timeline = [...(lastMsg.timeline || [])]
                          const lastTL = timeline.length > 0 ? timeline[timeline.length - 1] : null

                          if (lastTL && lastTL.type === 'thinking_stream') {
                            timeline[timeline.length - 1] = {
                              ...lastTL,
                              text: `${lastTL.text || ''}${data.text || ''}`,
                              timestamp: Date.now()
                            }
                          } else {
                            timeline.push({ type: 'status', ...statusItem, timestamp: Date.now() })
                          }
                          lastMsg.timeline = timeline

                          newMsgs[lastIndex] = lastMsg
                        }
                        return newMsgs
                      })
                      break;
                    }

                    // 🆕 累积状态历史（而非替换）
                    setMessages(prev => {
                      const newMsgs = [...prev]
                      const lastIndex = newMsgs.length - 1
                      if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
                        const lastMsg = { ...newMsgs[lastIndex] }

                        // 1. 先应用强制刷新的内容（如果有）
                        if (forcedBuffer) {
                          lastMsg.content = (lastMsg.content || '') + forcedBuffer
                          lastMsg.status = null

                          const timeline = [...(lastMsg.timeline || [])]
                          if (!lastMsg.hasContentStarted) {
                            lastMsg.hasContentStarted = true
                            timeline.push({
                              type: 'content',
                              text: forcedBuffer,
                              timestamp: Date.now()
                            })
                          } else {
                            const lastTimelineItem = timeline.length > 0 ? timeline[timeline.length - 1] : null
                            if (lastTimelineItem && lastTimelineItem.type === 'content') {
                              timeline[timeline.length - 1] = {
                                ...lastTimelineItem,
                                text: (lastTimelineItem.text || '') + forcedBuffer,
                                timestamp: Date.now()
                              }
                            } else {
                              // 🔧 修复：使用 forcedBuffer 而不是 bufferedContent
                              timeline.push({
                                type: 'content',
                                text: forcedBuffer,
                                timestamp: Date.now()
                              })
                            }
                          }
                          lastMsg.timeline = timeline
                        }

                        const prevHistory = lastMsg.statusHistory || []
                        const timeline = [...(lastMsg.timeline || [])]

                        // ... 后续状态处理逻辑 ...

                        // 🆕 去重逻辑优化：
                        const lastItem = prevHistory.length > 0 ? prevHistory[prevHistory.length - 1] : null
                        const lastStatusText = lastItem ? lastItem.text : ''

                        // 检查是否完全相同（文案 + 详情）
                        const isTextSame = lastStatusText === data.text
                        // 简单的详情比较（假设 details 结构简单）
                        const getLastDetails = (item) => item?.details ? JSON.stringify(item.details) : ''
                        const getCurrentDetails = (data) => data.details ? JSON.stringify(data.details) : ''
                        const isDetailsSame = getLastDetails(lastItem) === getCurrentDetails(data)

                        if (isTextSame) {
                          if (isDetailsSame) {
                            return prev // 完全相同，跳过
                          } else {
                            // 文案相同但详情不同（例如检索出新词），更新最后一条
                            const updatedHistory = [...prevHistory]
                            updatedHistory[updatedHistory.length - 1] = {
                              ...updatedHistory[updatedHistory.length - 1],
                              details: data.details,
                              timestamp: new Date()
                            }
                            lastMsg.statusHistory = updatedHistory

                            // 🆕 同时更新 timeline 中的最后一个 status 项
                            for (let i = timeline.length - 1; i >= 0; i--) {
                              if (timeline[i].type === 'status' && timeline[i].text === data.text) {
                                timeline[i] = { ...timeline[i], details: data.details, timestamp: Date.now() }
                                break
                              }
                            }
                            lastMsg.timeline = timeline

                            lastMsg.status = data
                            newMsgs[lastIndex] = lastMsg
                            return newMsgs
                          }
                        }

                        // 🔧 修复：不再用 lastMsg.content 替换 timeline 中的 content
                        // 因为 lastMsg.content 是累积的全部内容，而 timeline 中每个 content 条目
                        // 只应该包含该轮次生成的内容。直接替换会导致重复显示。
                        // 如果需要确保 timeline 内容完整，应该在 token 事件中处理，而不是在 status 事件中。

                        // 文案不同，追加新状态
                        const statusItem = {
                          id: Date.now(),
                          text: data.text,
                          icon: data.icon,
                          phase: data.phase,
                          details: data.details || null,
                          timestamp: new Date(),
                          isComplete: false
                        }

                        // 标记前一个状态为完成
                        const updatedHistory = prevHistory.map(item => ({ ...item, isComplete: true }))

                        // 添加新状态
                        lastMsg.statusHistory = [...updatedHistory, statusItem]

                        // 🆕 标记 timeline 中之前的状态为完成，然后添加新状态
                        const updatedTimeline = timeline.map(item =>
                          item.type === 'status' ? { ...item, isComplete: true } : item
                        )
                        updatedTimeline.push({
                          type: 'status',
                          text: data.text,
                          icon: data.icon,
                          phase: data.phase,
                          details: data.details || null,
                          timestamp: Date.now(),
                          isComplete: false
                        })
                        lastMsg.timeline = updatedTimeline

                        // 检测步骤变化：如果 status 文本包含 "Step X"，更新 plan 的 currentStep
                        if (data.text && data.text.toLowerCase().includes('step')) {
                          const planItem = updatedTimeline.find(i => i.type === 'plan')
                          if (planItem) {
                            // 简单提取数字: "Step 2" -> 1 (index)
                            const match = data.text.match(/Step (\d+)/i)
                            if (match) {
                              planItem.currentStep = parseInt(match[1]) - 1
                            }
                          }
                        }

                        lastMsg.status = data
                        newMsgs[lastIndex] = lastMsg
                      }
                      return newMsgs
                    })
                    break
                  }

                  case 'router':
                    // 可选：显示路由决策（如需调试）
                    break

                  case 'sources':
                    // 更新引用源（不再硬编码状态消息）
                    setMessages(prev => {
                      const newMsgs = [...prev]
                      const lastIndex = newMsgs.length - 1
                      if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
                        const lastMsg = { ...newMsgs[lastIndex] }
                        lastMsg.sources = data
                        newMsgs[lastIndex] = lastMsg
                      }
                      return newMsgs
                    })
                    break

                  case 'token':
                    // 按 ~30ms 批量写入（每个流独立的缓冲，见 createTokenBuffer）
                    tokenBuffer.push(data)
                    break

                  case 'done':
                    // 完成：先写入缓冲中的最后一批 token，再结束流式状态
                    tokenBuffer.flush()
                    setMessages(prev => {
                      const newMsgs = [...prev]
                      const lastIndex = newMsgs.length - 1
                      if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
                        const lastMsg = { ...newMsgs[lastIndex] }
                        lastMsg.isStreaming = false
                        lastMsg.status = null // 清除状态
                        // 🆕 标记所有状态历史项为完成
                        if (lastMsg.statusHistory) {
                          lastMsg.statusHistory = lastMsg.statusHistory.map(item => ({ ...item, isComplete: true }))
                        }
                        // 🆕 标记 timeline 中的状态为完成
                        if (lastMsg.timeline) {
                          lastMsg.timeline = lastMsg.timeline.map(item => {
                            if (item.type === 'status') return { ...item, isComplete: true }
                            if (item.type === 'plan') return { ...item, isComplete: true }
                            return item
                          })
                        }
                        lastMsg.usedRag = data.used_rag
                        lastMsg.iterations = data.search_count || data.iterations || 0
                        lastMsg.duration = data.duration
                        // 🆕 保存从后端返回的数据库 ID，用于后续编辑删除
                        if (data.message_id) {
                          lastMsg.id = data.message_id
                          // 问题和回答存在同一条记录里：刚发出的问题也要带上这个 ID，
                          // 否则编辑它时不会删掉旧记录，旧问答会继续留在上下文里
                          const userIndex = lastIndex - 1
                          if (userIndex >= 0 && newMsgs[userIndex].type === 'user' && !newMsgs[userIndex].id) {
                            newMsgs[userIndex] = { ...newMsgs[userIndex], id: data.message_id }
                          }
                        }
                        newMsgs[lastIndex] = lastMsg
                      }
                      return newMsgs
                    })
                    if (onHistoryUpdated) {
                      onHistoryUpdated()
                    }
                    break

                  case 'flowchart':
                    setMessages(prev => {
                      const newMsgs = [...prev]
                      const lastIndex = newMsgs.length - 1
                      if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
                        const lastMsg = { ...newMsgs[lastIndex] }
                        lastMsg.flowchart = data
                        // 确保状态更新，触发重绘
                        newMsgs[lastIndex] = lastMsg
                      }
                      return newMsgs
                    })
                    break

                  case 'chart':
                    setMessages(prev => {
                      const newMsgs = [...prev]
                      const lastIndex = newMsgs.length - 1
                      if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
                        const lastMsg = { ...newMsgs[lastIndex] }
                        lastMsg.chartData = data
                        newMsgs[lastIndex] = lastMsg
                      }
                      return newMsgs
                    })
                    break

                  // 🆕 处理追问事件
                  case 'clarification':
                    tokenBuffer.flush()
                    setMessages(prev => {
                      const newMsgs = [...prev]
                      const lastIndex = newMsgs.length - 1
                      if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
                        const lastMsg = { ...newMsgs[lastIndex] }
                        lastMsg.content = data.question  // 追问内容作为 AI 消息
                        lastMsg.isClarification = true   // 标记为追问消息
                        lastMsg.clarificationType = data.type || 'input'  // 追问类型
                        lastMsg.clarificationOptions = data.options || []  // 选项列表
                        lastMsg.isStreaming = false      // 结束流式状态
                        lastMsg.status = null
                        // 清除状态历史，追问不需要显示步骤
                        lastMsg.statusHistory = []
                        newMsgs[lastIndex] = lastMsg
                      }
                      return newMsgs
                    })
                    break

                  case 'error':
                    tokenBuffer.flush()
                    throw createStreamError(data)
                }
              } catch (e) {
                // 后端在流中报告的错误必须交给外层展示，不能当作解析错误吞掉（否则只剩一个空气泡）
                if (e?.isStreamError) throw e
                continue
              }
            }
          }
        }

    } catch (err) {
      // 已切换文档/会话或组件卸载：静默结束，不往新对话里写错误
      if (controller.signal.aborted) return

      // 先写入已收到的内容，再提示错误
      tokenBuffer.flush()

      // 友好的错误提示
      let friendlyMessage = '抱歉，刚才出了点小问题，请再试一次吧~'
      if (err?.isStreamError) {
        // 后端在流中明确给出了原因：直接展示
        friendlyMessage = normalizeTaskErrorMessage(err.message, friendlyMessage)
      } else if (err.name === 'TypeError' && err.message === 'Failed to fetch') {
        friendlyMessage = '网络好像开小差了，检查一下网络连接再试试？'
      } else if (err.message?.includes('timeout') || err.message?.includes('超时')) {
        friendlyMessage = '服务器有点忙，让它喘口气再试试吧~'
      } else if (err.message?.includes('500') || err.message?.includes('502') || err.message?.includes('503')) {
        friendlyMessage = '服务器打了个盹，稍等片刻再试试~'
      } else if (err.message && !err.message.includes('fetch')) {
        friendlyMessage = normalizeTaskErrorMessage(err.message, friendlyMessage)
      }

      // 积分不足时：统一提示 + 归零积分并阻断输入
      if (isInsufficientCreditsText(err.message)) {
        setUserCredits(0)
        friendlyMessage = normalizeTaskErrorMessage(err.message, friendlyMessage)
      }

      const errorMessage = {
        type: 'error',
        content: friendlyMessage,
        timestamp: new Date()
      }
      setMessages(prev => {
        const newMsgs = [...prev]
        const lastIndex = newMsgs.length - 1
        const last = newMsgs[lastIndex]
        if (last?.type === 'ai' && last.isStreaming && !last.content && !last.flowchart && !last.chartData) {
          // 占位气泡还没有任何内容：直接替换为错误提示，避免留下一个空气泡
          newMsgs[lastIndex] = errorMessage
          return newMsgs
        }
        if (last?.isStreaming) {
          newMsgs[lastIndex] = { ...last, isStreaming: false, status: null }
        }
        return [...newMsgs, errorMessage]
      })
    } finally {
      const wasAborted = controller.signal.aborted
      if (streamControllerRef.current === controller) {
        streamControllerRef.current = null
      }
      if (wasAborted) {
        // 视图已切换：丢弃残余 token，不再改动新对话的消息；没有新的流接手时解除输入锁
        tokenBuffer.cancel()
        if (!streamControllerRef.current) setIsLoading(false)
      } else {
      // 先写入缓冲中最后一批 token，再结束流式状态，避免回答末尾丢字
      tokenBuffer.flush()
      setIsLoading(false)
      // ⚠️ 关键修正：无论请求是否成功，或者是否收到了 done 信号，都要强制结束流式状态
      // 这样可以避免如果后端突然断开或没发送 done 信号，导致 input 一直被禁用或消息一直显示 loading
      setMessages(prev => {
        const newMsgs = [...prev]
        const lastIndex = newMsgs.length - 1
        if (lastIndex >= 0 && newMsgs[lastIndex].isStreaming) {
          const lastMsg = { ...newMsgs[lastIndex] }
          lastMsg.isStreaming = false
          // 如果没有状态且内容为空，提示异常
          if (!lastMsg.status && !lastMsg.content && !lastMsg.flowchart) {
            lastMsg.content = '（生成意外中断，请重试）'
          }
          // 清除 loading 状态文本
          lastMsg.status = null
          newMsgs[lastIndex] = lastMsg
        }
        return newMsgs
      })
      }
    }
  }



  const handleSubmit = async (e) => {
    e.preventDefault()

    // 如果有引用内容，合并发送
    if (referenceContent) {
      if (isLoading || !inputValue.trim()) return

      const label = referenceContent.title || '原文选段'
      const apiMessage = `用户选中了原文：\n${referenceContent.content}\n\n附加问题：${inputValue}`
      const displayMessage = `📄 ${label}\n> ${referenceContent.content.slice(0, 200)}${referenceContent.content.length > 200 ? '...' : ''}\n\n${inputValue}`
      setReferenceContent(null)
      setInputValue('')
      if (chatInputRef.current) {
        chatInputRef.current.style.height = 'auto'
      }
      await sendQuestion(displayMessage, apiMessage)
      return
    }

    // 普通问题
    if (inputValue.trim()) {
      if (isLoading) return

      const question = inputValue.trim()
      setInputValue('')
      if (chatInputRef.current) {
        chatInputRef.current.style.height = ''
      }

      await sendQuestion(question)
    }
  }


  // 根据模式选择显示的摘要内容
  const displaySummary = isEurekaMode ? (eurekaData?.content || eurekaData?.reasoning) : summary

  // 格式化节点标签
  const formatNodeLabel = (raw) => {
    if (!raw) return ''
    const str = String(raw)
    const parts = str.split(/\s*\/\s*/)
    if (parts.length >= 2) {
      return `${parts[0].trim()} / ${parts.slice(1).join(' / ').trim()}`
    }
    return str
  }

  return (
    <div className="document-chat-container">

      {/* 对话消息区域 */}
      <div className="chat-messages" ref={messagesContainerRef} onScroll={handleScroll}>
        {/* 灵感模式：显示灵感内容和连接点 */}
        {isEurekaMode && eurekaData && (
          <Motion.div
            className="document-summary-inline eureka-summary"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            <div className="summary-content">
              {/* 连接点 */}
              {eurekaData.source_nodes && eurekaData.source_nodes.length > 0 && (
                <div className="eureka-connections">
                  <div className="eureka-section-header">
                    <span className="eureka-section-icon">🔗</span>
                    <span className="eureka-section-title">连接点</span>
                  </div>
                  <div className="eureka-nodes-list">
                    {eurekaData.source_nodes.map((node, idx) => (
                      <span key={node.id || idx} className="eureka-node-tag">
                        {formatNodeLabel(node.label || node.term)}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* 灵感内容 */}
              <div className="eureka-content-section">
                <div className="eureka-section-header">
                  <span className="eureka-section-icon">💡</span>
                  <span className="eureka-section-title">灵感内容</span>
                </div>
                <div className="summary-text eureka-content-text">
                  <MarkdownRenderer>
                    {displaySummary || '暂无内容'}
                  </MarkdownRenderer>
                </div>
              </div>
            </div>
          </Motion.div>
        )}

        {/* 文档模式：显示 AI 摘要 */}
        {!isEurekaMode && displaySummary && (
          <Motion.div
            className="document-summary-inline"
            initial={summaryAnimateIn ? { opacity: 0, y: 20 } : false}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
          >
            <div className="summary-content">
              <div className="summary-text">
                <MarkdownRenderer>
                  {displaySummary}
                </MarkdownRenderer>
                <KeyTermEditor documentId={documentId} />
              </div>
            </div>
          </Motion.div>
        )}


        {/* 滚动到底部按钮 */}
        <AnimatePresence>
          {showScrollButton && (
            <Motion.button
              className="scroll-to-bottom-btn"
              initial={{ opacity: 0, y: 10, x: '-50%' }}
              animate={{ opacity: 1, y: 0, x: '-50%' }}
              exit={{ opacity: 0, y: 10, x: '-50%' }}
              onClick={() => scrollToBottom('smooth')}
              title="滚动到底部"
            >
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 14l-7 7m0 0l-7-7m7 7V3" />
              </svg>
            </Motion.button>
          )}
        </AnimatePresence>

        {isLoadingHistory ? (
          <div className="chat-empty">
            <div className="chat-history-skeleton-wrap">
              <SkeletonChatMessage isAi={false} />
              <SkeletonChatMessage isAi={true} />
            </div>
          </div>
        ) : messages.length === 0 && !flowchart ? (
          <div className="chat-empty">
            <div className="chat-empty-icon">💬</div>
            <p className="chat-empty-text">
              {summary ? '已为你生成文档摘要' : '对文档内容有疑问？'}
              <br />
              开始提问吧！
            </p>
          </div>
        ) : (
          <AnimatePresence>
            {messages.map((message, index) => {
              // 编辑模式：隐藏被编辑消息及其之后的消息
              if (editingMessageId !== null && index >= editingMessageId) {
                return null
              }

              const prevMsg = index > 0 ? messages[index - 1] : null
              const currentTime = message.timestamp instanceof Date ? message.timestamp : null
              const prevTime = prevMsg?.timestamp instanceof Date ? prevMsg.timestamp : null
              const timeDiffMs = currentTime && prevTime ? currentTime - prevTime : Infinity
              const showTimeSeparator = index === 0 || timeDiffMs > 5 * 60 * 1000

              const formatTimeSeparator = (date) => {
                if (!date) return ''
                const now = new Date()
                const isToday = date.toDateString() === now.toDateString()
                const yesterday = new Date(now)
                yesterday.setDate(yesterday.getDate() - 1)
                const isYesterday = date.toDateString() === yesterday.toDateString()
                const timeStr = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
                const dateStr = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
                if (isToday) return `今天 ${timeStr}`
                if (isYesterday) return `昨天 ${timeStr}`
                return `${dateStr} ${timeStr}`
              }

              return (
                <div key={index}>
                  {showTimeSeparator && currentTime && (
                    <div className="chat-time-separator">
                      <span>{formatTimeSeparator(currentTime)}</span>
                    </div>
                  )}
                <Motion.div
                  className={`chat-message ${message.type}`}
                  initial={{ y: 10 }}
                  animate={{ y: 0 }}
                  exit={{ y: -10 }}
                  transition={{ duration: 0.3 }}
                  onMouseEnter={() => {
                    if (message.type === 'user') {
                      if (hoverTimeoutRef.current) {
                        clearTimeout(hoverTimeoutRef.current)
                        hoverTimeoutRef.current = null
                      }
                      setHoveredMessageIndex(index)
                    }
                  }}
                  onMouseLeave={() => {
                    if (message.type === 'user') {
                      if (hoverTimeoutRef.current) {
                        clearTimeout(hoverTimeoutRef.current)
                      }
                      hoverTimeoutRef.current = setTimeout(() => {
                        setHoveredMessageIndex(null)
                        hoverTimeoutRef.current = null
                      }, 100)
                    }
                  }}
                >
                  <div className="message-content">
                    {/* 编辑模式下，被编辑的消息及其后续消息会被隐藏（在上层 map 中处理），这里不再需要内联编辑框 */}

                    <div className={`message-text ${message.isStreaming ? 'streaming' : ''} ${message.isAnnotation && message.status && !message.content ? 'annotation-loading' : ''} ${message.isClarification ? 'clarification' : ''}`}>
                      {message.type === 'error' ? (
                        <div className="error-message-bubble">
                          <span>{message.content}</span>
                          {/* 本地版的 Key 问题可以自己解决：直接给出去设置页的入口 */}
                          {isLocalMode && /API Key|设置/.test(message.content || '') && (
                            <button type="button" className="error-message-settings-btn" onClick={() => navigate('/settings#chat')}>
                              前往设置
                            </button>
                          )}
                        </div>
                      ) : message.type === 'ai' ? (
                        <>
                          {/* 🆕 追问消息：简化渲染，问题和选项同行 */}
                          {message.isClarification ? (
                            <div className="clarification-content">
                              {/* 历史追问（如有） */}
                              {message.clarificationHistory && message.clarificationHistory.length > 0 && (
                                <div className="clarification-history clarification-history-muted">
                                  {message.clarificationHistory.map((item, hIdx) => (
                                    <div key={hIdx} className="clarification-history-item">
                                      <span className="clarification-history-question">{item.question}</span>
                                      {item.selectedOption && (
                                        <span className="clarification-options">
                                          <button className="clarification-option-btn selected" disabled>{item.selectedOption}</button>
                                        </span>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              )}

                              {/* 当前追问：问题 + 多选选项 */}
                              <div className="clarification-inline-row">
                                <span className="clarification-inline-text">{message.clarificationContent || message.content}</span>
                                {message.clarificationOptions && message.clarificationOptions.length > 0 && !message.isSubmitted && (
                                  <div className="clarification-options-wrapper clarification-options-wrapper-inline">
                                    {/* 选项列表（单选即提交） */}
                                    {message.clarificationOptions.map((option, optIdx) => {
                                      const isSelected = (message.selectedOptions || []).includes(option)
                                      return (
                                        <button
                                          key={optIdx}
                                          className={`clarification-option-btn ${isSelected ? 'selected' : ''}`}
                                          disabled={isLoading}
                                          onClick={() => handleClarificationSubmit(index, [option])}
                                        >
                                          {option}
                                        </button>
                                      )
                                    })}
                                  </div>
                                )}

                                {/* 已提交的选项显示 - 保持与未提交时相同的布局结构 */}
                                {message.isSubmitted && message.clarificationOptions && message.clarificationOptions.length > 0 && (
                                  <div className="clarification-options-wrapper clarification-options-wrapper-inline">
                                    {message.clarificationOptions.map((opt, optIdx) => {
                                      const isSelected = (message.selectedOptions || []).includes(opt)
                                      return (
                                        <button
                                          key={optIdx}
                                          className={`clarification-option-btn ${isSelected ? 'selected' : 'disabled'}`}
                                          disabled
                                        >
                                          {opt}
                                        </button>
                                      )
                                    })}
                                  </div>
                                )}
                              </div>

                              {/* 选中后显示状态/回答 - 使用与普通消息相同的 TimelineRenderer */}
                              {(message.isSubmitted || !message.clarificationOptions || message.clarificationOptions.length === 0) && (
                                <div className="clarification-answer-wrap">
                                  {(message.timeline && message.timeline.length > 0) || message.isStreaming ? (
                                    <TimelineRenderer
                                      timeline={message.timeline}
                                      isStreaming={message.isStreaming}
                                      flowchart={message.flowchart}
                                      chartData={message.chartData}
                                    />
                                  ) : (
                                    <>
                                      {/* 回退：旧模式 - 状态历史面板 */}
                                      {message.statusHistory && message.statusHistory.length > 0 && (
                                        <StatusHistoryPanel
                                          history={message.statusHistory}
                                          isStreaming={message.isStreaming}
                                        />
                                      )}
                                      {message.answerStarted && message.content && (
                                        <div className="clarification-answer-content">
                                          <MarkdownRenderer>
                                            {message.content}
                                          </MarkdownRenderer>
                                        </div>
                                      )}
                                    </>
                                  )}
                                </div>
                              )}
                            </div>
                          ) : (
                            <>
                              {/* 普通 AI 消息：使用时间线渲染器（有 timeline 或正在流式中），否则回退到旧模式 */}
                              {(message.timeline && message.timeline.length > 0) || message.isStreaming ? (
                                <TimelineRenderer
                                  timeline={message.timeline}
                                  isStreaming={message.isStreaming}
                                  flowchart={message.flowchart}
                                  chartData={message.chartData}
                                />
                              ) : (
                                <>
                                  {/* 回退：旧模式 - 状态历史面板 */}
                                  {message.statusHistory && message.statusHistory.length > 0 && (
                                    <StatusHistoryPanel
                                      history={message.statusHistory}
                                      isStreaming={message.isStreaming}
                                    />
                                  )}
                                  {(() => {
                                    const content = message.content || '';
                                    const parts = content.split(/(<<<FLOWCHART_PLACEHOLDER>>>|<<<DATA_CHART_PLACEHOLDER>>>)/);
                                    const hasEmbeddedFlowchart = parts.includes('<<<FLOWCHART_PLACEHOLDER>>>');
                                    const hasEmbeddedChart = parts.includes('<<<DATA_CHART_PLACEHOLDER>>>');

                                    return (
                                      <>
                                        {parts.map((part, index) => {
                                          if (part === '<<<FLOWCHART_PLACEHOLDER>>>') {
                                            if (message.flowchart) {
                                              return (
                                                <Motion.div
                                                  key={`flowchart-${index}`}
                                                  className="message-flowchart timeline-media-block"
                                                  initial={{ opacity: 0 }}
                                                  animate={{ opacity: 1 }}
                                                  transition={{ duration: 0.3 }}
                                                >
                                                  <ChartCard
                                                    title={message.flowchart.title || message.flowchart.topic || '知识路径'}
                                                    subtitle={message.flowchart.overview}
                                                    icon="🗺️"
                                                    minimal={true}
                                                    allowFullscreen={!!message.flowchart.mermaid_code}
                                                  >
                                                    <div className="flowchart-container flowchart-container-centered">
                                                      {message.flowchart.mermaid_code ? (
                                                        <MermaidPreview code={message.flowchart.mermaid_code} />
                                                      ) : (
                                                        <div className="chart-empty-preview">
                                                          (暂无预览图表)
                                                        </div>
                                                      )}
                                                    </div>
                                                  </ChartCard>
                                                </Motion.div>
                                              );
                                            }
                                            return null;
                                          }

                                          if (part === '<<<DATA_CHART_PLACEHOLDER>>>') {
                                            if (message.chartData) {
                                              return (
                                                <Motion.div
                                                  key={`chart-${index}`}
                                                  className="message-chart timeline-media-block timeline-media-block-full"
                                                  initial={{ opacity: 0, scale: 0.95 }}
                                                  animate={{ opacity: 1, scale: 1 }}
                                                  transition={{ duration: 0.4 }}
                                                >
                                                  <ChartCard
                                                    title={message.chartData.title || '数据图表'}
                                                    subtitle={message.chartData.unit ? `单位: ${message.chartData.unit}` : ''}
                                                    icon="📊"
                                                  >
                                                    <Suspense fallback={<div className="chart-loading-fallback">正在加载图表...</div>}>
                                                      <DataChart {...message.chartData} />
                                                    </Suspense>
                                                  </ChartCard>
                                                </Motion.div>
                                              );
                                            }
                                            return null;
                                          }

                                          if (!part.trim()) return null;

                                          return (
                                            <MarkdownRenderer key={`text-${index}`}>
                                              {part}
                                            </MarkdownRenderer>
                                          );
                                        })}

                                        {/* 兜底逻辑：处理没有占位符的旧消息 */}
                                        {message.flowchart && !hasEmbeddedFlowchart && (
                                          <Motion.div
                                            className="message-flowchart timeline-media-block"
                                            initial={{ opacity: 0 }}
                                            animate={{ opacity: 1 }}
                                            transition={{ duration: 0.3 }}
                                          >
                                            <ChartCard
                                              title={message.flowchart.title || message.flowchart.topic || '知识路径'}
                                              subtitle={message.flowchart.overview}
                                              icon="🗺️"
                                              allowFullscreen={!!message.flowchart.mermaid_code}
                                              onFullscreenRender={message.flowchart.mermaid_code ? async () => {
                                                const chartId = `fullscreen-mermaid-legacy-${Date.now()}`
                                                const mermaid = await getMermaid()
                                                try {
                                                  const { svg } = await mermaid.render(chartId, message.flowchart.mermaid_code)
                                                  return svg
                                                } catch {
                                                  try {
                                                    const tempDiv = document.createElement('div');
                                                    tempDiv.style.display = 'none';
                                                    tempDiv.id = chartId;
                                                    document.body.appendChild(tempDiv);
                                                    const { svg } = await mermaid.render(chartId, message.flowchart.mermaid_code);
                                                    document.body.removeChild(tempDiv);
                                                    return svg;
                                                  } catch (retryErr) {
                                                    // 该字符串会以 HTML 插入全屏层，报错内容（含图表源码）必须转义
                                                    return `<div style="color:red;padding:20px">渲染失败: ${escapeHtml(retryErr?.message || String(retryErr))}</div>`
                                                  }
                                                }
                                              } : undefined}
                                            >
                                              <div className="flowchart-container flowchart-container-centered">
                                                {message.flowchart.mermaid_code ? (
                                                  <MermaidPreview code={message.flowchart.mermaid_code} />
                                                ) : (
                                                  <div className="chart-empty-preview">
                                                    (暂无预览图表)
                                                  </div>
                                                )}
                                              </div>
                                            </ChartCard>
                                          </Motion.div>
                                        )}

                                        {message.chartData && !hasEmbeddedChart && (
                                          <Motion.div
                                            className="message-chart timeline-media-block timeline-media-block-full"
                                            initial={{ opacity: 0, scale: 0.95 }}
                                            animate={{ opacity: 1, scale: 1 }}
                                            transition={{ duration: 0.4 }}
                                          >
                                            <ChartCard
                                              title={message.chartData.title || '数据图表'}
                                              subtitle={message.chartData.unit ? `单位: ${message.chartData.unit}` : ''}
                                              icon="📊"
                                            >
                                              <Suspense fallback={<div className="chart-loading-fallback">正在加载图表...</div>}>
                                                <DataChart {...message.chartData} />
                                              </Suspense>
                                            </ChartCard>
                                          </Motion.div>
                                        )}
                                      </>
                                    );
                                  })()}

                                  {/* 兜底：如果没有 statusHistory 但有 status，显示简单指示器 */}
                                  {!message.statusHistory && message.status && !message.isStreaming && (
                                    <StatusIndicator status={message.status} />
                                  )}
                                </>
                              )}
                            </>
                          )}
                        </>
                      ) : (
                        message.content
                      )}
                    </div>
                    {/* AI 消息操作按钮和统计信息 - 只在回答完成后显示 */}
                    {message.type === 'ai' && !message.isStreaming && (!message.isClarification || message.answerStarted) && (
                      <div className="message-actions-row">
                        <button
                          className="message-action-btn copy"
                          onClick={async () => {
                            try {
                              await navigator.clipboard.writeText(message.content)
                              setCopySuccess(true)
                              setTimeout(() => setCopySuccess(false), 2000)
                            } catch (err) {
                              void err;
                            }
                          }}
                          title="复制消息"
                        >
                          {copySuccess ? '✓' : '⧉'}
                        </button>
                        <button
                          className="message-action-btn save-to-notes"
                          onClick={() => handleSaveToNotes(message.content)}
                          disabled={savingAIToNotes}
                        >
                          ✍️添加到笔记
                        </button>
                        {index === messages.length - 1 && (
                          <>
                            <button
                              className={`message-action-btn chat-gen ${chatGenLoading === 'flashcards' ? 'generating' : ''}`}
                              onClick={() => handleChatGenerate('flashcards')}
                              disabled={!!chatGenLoading}
                              title="基于全部对话内容生成闪卡"
                            >
                              {chatGenLoading === 'flashcards' ? '⏳' : '📇'} 对话闪卡
                            </button>
                            <button
                              className={`message-action-btn chat-gen ${chatGenLoading === 'quiz' ? 'generating' : ''}`}
                              onClick={() => handleChatGenerate('quiz')}
                              disabled={!!chatGenLoading}
                              title="基于全部对话内容生成测验"
                            >
                              {chatGenLoading === 'quiz' ? '⏳' : '📝'} 对话测验
                            </button>
                            <button
                              className={`message-action-btn chat-gen ${chatGenLoading === 'highlights' ? 'generating' : ''}`}
                              onClick={() => handleChatGenerate('highlights')}
                              disabled={!!chatGenLoading}
                              title="基于全部对话内容提炼要点"
                            >
                              {chatGenLoading === 'highlights' ? '⏳' : '💡'} 对话提炼
                            </button>
                          </>
                        )}

                        {/* 统计信息 */}
                        {(message.iterations !== undefined || message.duration !== undefined) && !message.isAnnotation && (
                          <div className="message-stats">
                            {message.usedRag ? (
                              <>
                                <span className="stat-item">查询 {message.iterations} 次</span>
                                <span className="stat-separator">·</span>
                              </>
                            ) : (
                              <>
                                <span className="stat-item">直接回答</span>
                                <span className="stat-separator">·</span>
                              </>
                            )}
                            <span className="stat-item">{message.duration}s</span>
                          </div>
                        )}

                      </div>
                    )}

                    {/* 显示引用的原文块 */}
                    {message.type === 'ai' && message.sources && message.sources.length > 0 && (
                      <div className="message-sources">
                        <div
                          className="sources-header clickable sources-header-inline"
                          onClick={() => {
                            setCollapsedSourceBlocks(prev => ({
                              ...prev,
                              [index]: !prev[index]
                            }))
                          }}
                        >
                          <span>{collapsedSourceBlocks[index] !== false ? '▶' : '▼'}</span>
                          <span>引用原文 ({message.sources.length})</span>
                        </div>
                        {collapsedSourceBlocks[index] !== false ? null : (
                          <div className="sources-list">
                            {message.sources.map((source, idx) => {
                              const sourceKey = `${index}-${idx}`
                              const isExpanded = expandedSources[sourceKey]
                              const maxPreviewLength = 200  // 增加预览长度到200字符
                              const needsTruncate = source.text && source.text.length > maxPreviewLength
                              const displayText = !source.text
                                ? '[引用内容不可用]'
                                : (isExpanded || !needsTruncate
                                  ? source.text
                                  : source.text.substring(0, maxPreviewLength) + '...')

                              return (
                                <div key={idx} className="source-item">
                                  <span className="source-label">[{idx + 1}]</span>
                                  <span className="source-text">{displayText}</span>
                                  {needsTruncate && (
                                    <button
                                      className="source-toggle-btn"
                                      onClick={() => {
                                        setExpandedSources(prev => ({
                                          ...prev,
                                          [sourceKey]: !prev[sourceKey]
                                        }))
                                      }}
                                    >
                                      {isExpanded ? '收起' : '展开'}
                                    </button>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )}
                    {message.type === 'user' && (
                      <div
                        className="message-actions"
                        style={{
                          visibility: hoveredMessageIndex === index ? 'visible' : 'hidden',
                          opacity: hoveredMessageIndex === index ? 1 : 0,
                          pointerEvents: hoveredMessageIndex === index ? 'auto' : 'none'
                        }}
                      >
                        <button
                          className="message-action-btn edit"
                          onClick={() => {
                            handleEditMessage(index)
                            if (hoverTimeoutRef.current) {
                              clearTimeout(hoverTimeoutRef.current)
                            }
                            setHoveredMessageIndex(null)
                          }}
                          title="编辑并重新生成"
                        >
                          ✎
                        </button>
                        <button
                          className="message-action-btn copy"
                          onClick={async () => {
                            try {
                              await navigator.clipboard.writeText(message.content)
                              setCopySuccess(true)
                              setTimeout(() => setCopySuccess(false), 2000)
                            } catch (err) {
                              void err;
                            }
                            if (hoverTimeoutRef.current) {
                              clearTimeout(hoverTimeoutRef.current)
                            }
                            setHoveredMessageIndex(null)
                          }}
                          title="复制消息"
                        >
                          {copySuccess ? '✓' : '⧉'}
                        </button>
                      </div>
                    )}

                  </div>
                </Motion.div>
                </div>
              );
            })}
          </AnimatePresence>
        )
        }

        {/* 🎯 底部占位空间：当用户发送消息后，需要足够的空间让用户消息能滚动到顶部 */}
        {/* 只在滚动过程中显示，滚动完成后立即隐藏，避免用户看到大量空白 */}
        {showScrollSpacer && (
          <div className="scroll-spacer scroll-spacer-dynamic scroll-spacer-large" />
        )}


      </div>
      {/* 输入区域（单一容器，分上下两行） */}
      <form
        className="chat-input-form"
        ref={chatFormRef}
        onSubmit={handleSubmit}
        style={{
          opacity: isLoadingHistory ? 0 : 1,
          visibility: isLoadingHistory ? 'hidden' : 'visible'
        }}
      >
        <div className="chat-input-box">
          {/* 编辑模式头部栏 */}
          {editingMessageId !== null && (
            <div className="edit-mode-header edit-mode-header-bar">
              <div className="edit-mode-title-row">
                <span>✎ 正在编辑消息</span>
              </div>
              <button
                type="button"
                onClick={handleCancelEdit}
                className="edit-mode-close-btn"
              >
                ✕
              </button>
            </div>
          )}

          <div className={`chat-input-wrapper ${isMultiline ? 'is-multiline' : ''} ${editingMessageId !== null ? 'edit-mode-active' : ''}`}>
            {/* 引用内容预览 - 当多行模式时显示在上方，单行模式时也占位 */}
            {referenceContent && (
              <div className="reference-preview-mini">
                <div className="ref-tag">引用: {referenceContent.title}</div>
                <button onClick={() => setReferenceContent(null)} className="ref-close-btn">×</button>
              </div>
            )}

            <div className="chat-input-main-container">
              {/* 输入区域 */}
              <div className="chat-textarea-container">
                <textarea
                  ref={chatInputRef}
                  className="chat-input-textarea"
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  placeholder={userCredits !== null && userCredits <= 0 ? "积分不足..." : '按Shift+Enter快捷回复'}
                  rows={1}
                  disabled={isLoading || (userCredits !== null && userCredits <= 0)}
                  onInput={(e) => {
                    if (!chatInputRef.current) return
                    chatInputRef.current.style.height = 'auto'
                    const scrollHeight = chatInputRef.current.scrollHeight
                    const newHeight = Math.min(scrollHeight, 300)
                    chatInputRef.current.style.height = newHeight + 'px'

                    // 阈值判断：如果高度明显超过一行，或者有分行符
                    const isMulti = newHeight > 42 || e.target.value.includes('\n')
                    if (isMulti !== isMultiline) setIsMultiline(isMulti)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault()
                      if (editingMessageId !== null) {
                        handleSaveEdit()
                      } else {
                        chatFormRef.current?.requestSubmit()
                      }
                    }
                  }}
                />
              </div>

              {/* 右侧工具栏 */}
              <div className="chat-input-right-tools">
                <div className="chat-status-mini">
                  {/* 可在此放置微型状态指示 */}
                </div>
                <button
                  type={editingMessageId !== null ? "button" : "submit"}
                  className={`chat-round-send-btn ${!inputValue.trim() ? 'is-empty' : ''}`}
                  disabled={isLoading || !inputValue.trim() || (userCredits !== null && userCredits <= 0)}
                  title={editingMessageId !== null ? "保存" : "发送"}
                  onClick={(e) => {
                    if (editingMessageId !== null) {
                      e.preventDefault()
                      handleSaveEdit()
                    }
                  }}
                >
                  {editingMessageId !== null ? (
                    <span className="check-icon">✓</span>
                  ) : (
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 19V5M5 12l7-7 7 7" />
                    </svg>
                  )}
                </button>
              </div>
            </div>

          </div>
        </div>
        <div className="chat-input-disclaimer">AI 的输出可能会有错误，请仔细甄别。</div>
      </form>
    </div >
  );
})

export default DocumentChat


