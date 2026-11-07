import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion as Motion } from 'framer-motion'
import * as d3 from 'd3'
import { apiGet, apiPost, apiPut, apiDelete } from '../utils/api'
import { escapeHtml } from '../lib/utils'
import { dataCache, CACHE_TTL } from '../utils/dataCache'
import useUIStore from '../stores/uiStore'
import useRuntimeStore from '../stores/runtimeStore'
import ConfirmDialog from './ConfirmDialog'
import MergeSuggestionsDialog from './KnowledgeGraph/MergeSuggestionsDialog'
import EurekaSetupDialog from './KnowledgeGraph/EurekaSetupDialog'
import EurekaDialog from './KnowledgeGraph/EurekaDialog'
import ManageKnowledgeDialog from './KnowledgeGraph/ManageKnowledgeDialog'
import KnowledgeGapDialog from './KnowledgeGraph/KnowledgeGapDialog'
import {
  pollCeleryTask,
  SKELETON_WIDTHS,
  formatDisplayLabel,
  splitTitleLabel,
  stripSummaryPrefix,
  filterNodesByQuery,
} from './KnowledgeGraph/utils'
import './KnowledgeGraphPage.css'
import './Skeleton.css'
import { SkeletonListItem } from './Skeleton'
import useFirstEntrance from '../hooks/useFirstEntrance'

const showInfo = (message) => useUIStore.getState().showToast(message, { type: 'info' })
const showError = (message) => useUIStore.getState().showToast(message, { type: 'error' })

// 关键词云默认只显示文档数最多的这些，其余点「查看全部」
const NODE_CLOUD_LIMIT = 40
// 超过这么多节点时，初始布局分片计算，不阻塞页面
const LARGE_GRAPH_NODES = 250

const costLabelOf = (cost) => (cost > 0 ? `${cost} 积分` : '')

// 导出的 SVG / PNG 脱离了页面样式表：把图上用到的关键样式写进文件里
const EXPORT_STYLES = `
  .kg-graph-node { stroke: #ecfeff; stroke-width: 1.2px; }
  .kg-graph-node.active { fill: #115e59; stroke: none; }
  .kg-graph-node.active-neighbor { fill: #2dd4bf; stroke: none; }
  .kg-graph-link { stroke-linecap: round; }
  .kg-graph-label { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; }
  .kg-graph-label.active { font-weight: 600; fill: #171717; }
`

const downloadUrl = (url, filename) => {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 立即 revoke 会让部分浏览器（Safari / Firefox）取消下载
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// 合并 S→T 之后，其余建议里引用 S 的要么失效（S 作为 source），要么改指向 T
const retargetSuggestions = (list, mergedId, intoId, intoLabel) => list.flatMap((s) => {
  if (s.source_id === mergedId) return []
  if (s.target_id === mergedId) {
    return s.source_id === intoId ? [] : [{ ...s, target_id: intoId, target_label: intoLabel }]
  }
  return [s]
})

function KnowledgeGraphPage({ onOpenDocument }) {
  const navigate = useNavigate()
  const animateIn = useFirstEntrance()
  const isLocalMode = useRuntimeStore((s) => s.deployMode === 'local')
  const [nodes, setNodes] = useState([])
  const [edges, setEdges] = useState([])
  const [orphanNodes, setOrphanNodes] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // AI Merge States
  const [aiDialogOpen, setAiDialogOpen] = useState(false)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState(null)
  const [aiMessage, setAiMessage] = useState('')
  const [aiMerging, setAiMerging] = useState(false)
  const [mergeSuggestions, setMergeSuggestions] = useState([])

  // Eureka States
  const [eurekaOpen, setEurekaOpen] = useState(false)
  const [eurekaSetupOpen, setEurekaSetupOpen] = useState(false)
  const [eurekaLoading, setEurekaLoading] = useState(false)
  const [eurekaResult, setEurekaResult] = useState(null)
  const [eurekaError, setEurekaError] = useState(null)
  const [isViewingSaved, setIsViewingSaved] = useState(false)
  const [eurekaSavedId, setEurekaSavedId] = useState(null) // 当前灵感已收藏时的记录 ID
  const [costs, setCosts] = useState(null) // 线上版各 AI 功能的积分价格

  const [confirmDialogState, setConfirmDialogState] = useState({
    isOpen: false,
    title: '',
    message: '',
    confirmLabel: '确定',
    isDangerous: false,
    type: 'confirm',
    onConfirm: null
  })

  const [selectedNode, setSelectedNode] = useState(null)
  const [selectedDocs, setSelectedDocs] = useState([])
  const [docsLoading, setDocsLoading] = useState(false)
  const [selectedTerms, setSelectedTerms] = useState([])
  const [selectionMode, setSelectionMode] = useState('AND') // 'AND' | 'OR'
  const [filterQuery, setFilterQuery] = useState('')       // 即时显示的输入值
  const [debouncedFilter, setDebouncedFilter] = useState('') // debounce 后用于过滤

  // 过滤输入 debounce：300ms 延迟，减少每次键入触发的过滤计算
  // 清空时立即生效，避免旧建议残留
  useEffect(() => {
    if (!filterQuery) {
      setDebouncedFilter('')
      return
    }
    const timer = setTimeout(() => setDebouncedFilter(filterQuery), 300)
    return () => clearTimeout(timer)
  }, [filterQuery])
  const [manageLoading, setManageLoading] = useState(false)
  const [manageInfo, setManageInfo] = useState('')
  const [manageOpen, setManageOpen] = useState(false)
  const [manageNodeId, setManageNodeId] = useState(null)
  const [manageLabel, setManageLabel] = useState('')
  const [manageDescription, setManageDescription] = useState('')
  const [manageSearchQuery, setManageSearchQuery] = useState('')
  const [mergeTargetId, setMergeTargetId] = useState('')
  const [mergeSearchQuery, setMergeSearchQuery] = useState('')

  const [savedEurekas, setSavedEurekas] = useState([])
  const [savedGapAnalyses, setSavedGapAnalyses] = useState([])
  const [sidebarTab, setSidebarTab] = useState('documents') // 'documents' | 'eureka' | 'gaps'
  const [nodeCloudExpanded, setNodeCloudExpanded] = useState(false)
  const [layoutPending, setLayoutPending] = useState(false)
  const [selectionVersion, setSelectionVersion] = useState(0) // 图重建完成后递增，让高亮重新应用

  const svgRef = useRef(null)
  const minimapRef = useRef(null)
  const graphContainerRef = useRef(null)
  const viewportRef = useRef({ width: 600, height: 400 })
  const zoomRef = useRef(null)
  const simulationRef = useRef(null)
  const graphSelectRef = useRef(null)       // 稳定引用，避免 D3 事件中的过时闭包
  const d3SelectionsRef = useRef(null)      // 缓存 D3 selections，供视觉更新使用
  const graphStructureKeyRef = useRef('')   // 上次构建的图结构（节点 + 边），结构不变时只原地更新属性
  const sceneCleanupRef = useRef(null)      // 停止当前力导图（重建或卸载时调用）
  const nodePositionsRef = useRef(new Map()) // 重建时沿用已有节点的位置，图不会整体重排
  const graphNodesRef = useRef([])          // D3 事件里读取最新的节点数据
  const docsRequestSeqRef = useRef(0)       // 关键词文档请求序号，避免并发切换导致旧结果覆盖
  const pollCancelRef = useRef({})          // 各 AI 任务各自的轮询取消函数：互不打断
  const lastEurekaParamsRef = useRef(null) // 上次灵感生成参数
  const mountedRef = useRef(true)           // 组件挂载状态
  const dialogOpenRef = useRef({})          // 结果到达时对应弹窗是否开着

  const graphNodes = useMemo(() => {
    const map = new Map()
    ;[...nodes, ...orphanNodes].forEach((node) => {
      if (node?.id != null && !map.has(node.id)) {
        map.set(node.id, node)
      }
    })
    return Array.from(map.values())
  }, [nodes, orphanNodes])

  graphNodesRef.current = graphNodes

  useEffect(() => {
    const polls = pollCancelRef.current
    return () => {
      mountedRef.current = false
      Object.values(polls).forEach((cancel) => cancel?.())
    }
  }, [])

  // 每种 AI 任务一个轮询：启动灵感生成不会打断进行中的知识间隙分析（反之亦然）
  const startPoll = (kind, taskId, handlers) => {
    pollCancelRef.current[kind]?.()
    pollCancelRef.current[kind] = pollCeleryTask(taskId, {
      onSuccess: (data) => { if (mountedRef.current) handlers.onSuccess(data) },
      onError: (msg) => { if (mountedRef.current) handlers.onError(msg) },
    })
  }

  useEffect(() => {
    const loadInitialData = async () => {
      const graphCacheKey = 'kg:nodes-edges'
      const eurekaCacheKey = 'kg:eureka:list'
      const gapsCacheKey = 'kg:gaps:list'
      
      const cachedGraph = dataCache.get(graphCacheKey)
      const cachedEurekas = dataCache.get(eurekaCacheKey)
      
      // 如果有缓存，先用缓存渲染，不显示 loading (SWR 模式)
      if (cachedGraph && cachedEurekas) {
        setError(null)
        setNodes(cachedGraph.nodes || [])
        setEdges(cachedGraph.edges || [])
        setOrphanNodes(cachedGraph.orphan_nodes || [])
        if (cachedEurekas.success) {
          setSavedEurekas(cachedEurekas.data || [])
        }
        const cachedGaps = dataCache.get(gapsCacheKey)
        if (cachedGaps && cachedGaps.success) {
          setSavedGapAnalyses(cachedGaps.items || [])
        }
        setLoading(false)
        
        // 后台静默刷新
        Promise.all([
          apiGet('/api/knowledge-graph/nodes-edges', {
            cacheKey: graphCacheKey,
            cacheTTL: CACHE_TTL.GRAPH,
            cacheMode: 'no-cache'
          }),
          apiGet('/api/knowledge-graph/eureka/list', {
            cacheKey: eurekaCacheKey,
            cacheTTL: CACHE_TTL.EUREKA_LIST,
            cacheMode: 'no-cache'
          }),
          apiGet('/api/knowledge-graph/knowledge-gaps/list', {
            cacheKey: gapsCacheKey,
            cacheTTL: CACHE_TTL.GAP_LIST,
            cacheMode: 'no-cache'
          })
        ]).then(([graphData, eurekasData, gapsData]) => {
          if (!mountedRef.current) return
          setNodes(graphData.nodes || [])
          setEdges(graphData.edges || [])
          setOrphanNodes(graphData.orphan_nodes || [])
          if (eurekasData.success) {
            setSavedEurekas(eurekasData.data || [])
          }
          if (gapsData.success) {
            setSavedGapAnalyses(gapsData.items || [])
          }
        }).catch(err => void err)
        
        return
      }

      setLoading(true)
      try {
        setError(null)
        const [graphData, eurekasData, gapsData] = await Promise.all([
          apiGet('/api/knowledge-graph/nodes-edges', {
            cacheKey: graphCacheKey,
            cacheTTL: CACHE_TTL.GRAPH
          }),
          apiGet('/api/knowledge-graph/eureka/list', {
            cacheKey: eurekaCacheKey,
            cacheTTL: CACHE_TTL.EUREKA_LIST
          }),
          apiGet('/api/knowledge-graph/knowledge-gaps/list', {
            cacheKey: gapsCacheKey,
            cacheTTL: CACHE_TTL.GAP_LIST
          })
        ])

        const gNodes = graphData.nodes || []
        const gEdges = graphData.edges || []
        setNodes(gNodes)
        setEdges(gEdges)
        setOrphanNodes(graphData.orphan_nodes || [])

        if (eurekasData.success) {
          setSavedEurekas(eurekasData.data || [])
        }
        if (gapsData.success) {
          setSavedGapAnalyses(gapsData.items || [])
        }
      } catch (err) {
        setError(err.message || '加载知识图谱失败')
      } finally {
        setLoading(false)
      }
    }

    loadInitialData()
  }, [])

  const refreshGraph = useCallback(async () => {
    dataCache.invalidate('kg:nodes-edges')
    setError(null)
    const data = await apiGet('/api/knowledge-graph/nodes-edges', {
      cacheKey: 'kg:nodes-edges',
      cacheTTL: CACHE_TTL.GRAPH,
      cacheMode: 'no-cache',
    })
    const nextNodes = data.nodes || []
    const nextEdges = data.edges || []
    const nextOrphans = data.orphan_nodes || []
    setNodes(nextNodes)
    setEdges(nextEdges)
    setOrphanNodes(nextOrphans)
    return { nodes: nextNodes, edges: nextEdges, allNodes: [...nextNodes, ...nextOrphans] }
  }, [])

  // 归一化规则修复后（如中文引号），旧数据里的重复节点在这里合并；没有需要处理的节点时不写库
  useEffect(() => {
    let ignore = false
    apiPost('/api/knowledge-graph/maintenance/renormalize-terms', {})
      .then((res) => {
        if (!ignore && (res?.merged_nodes || res?.renamed_nodes)) {
          refreshGraph().catch((err) => void err)
        }
      })
      .catch((err) => void err)
    return () => { ignore = true }
  }, [refreshGraph])

  // 线上版：在 AI 按钮上标出积分价格（本地版不计费）
  useEffect(() => {
    if (isLocalMode) {
      setCosts(null)
      return undefined
    }
    let ignore = false
    apiGet('/api/credits/cost-preview', { cacheKey: 'credits:cost-preview', cacheTTL: 10 * 60 * 1000 })
      .then((res) => {
        const table = res?.data?.costs || {}
        if (!ignore) setCosts({ eureka: table.ai_eureka?.cost, organize: table.ai_organize?.cost })
      })
      .catch((err) => void err)
    return () => { ignore = true }
  }, [isLocalMode])

  // 节点被删除 / 合并后，让选中状态只保留仍存在的节点
  const syncSelectionWith = useCallback((allNodes) => {
    const byId = new Map(allNodes.map((n) => [n.id, n]))
    setSelectedNode((prev) => (prev ? byId.get(prev.id) || null : null))
    setSelectedTerms((prev) => prev.map((t) => byId.get(t.id)).filter(Boolean))
    setPinnedNode((prev) => (prev ? byId.get(prev.id) || null : null))
  }, [])

  const fetchSavedEurekas = async (forceRefresh = false) => {
    const cacheKey = 'kg:eureka:list'
    if (forceRefresh) {
      dataCache.invalidate(cacheKey)
    }
    try {
      const res = await apiGet('/api/knowledge-graph/eureka/list', {
        cacheKey,
        cacheTTL: CACHE_TTL.EUREKA_LIST,
        cacheMode: forceRefresh ? 'no-cache' : 'default'
      })
      if (res.success) {
        setSavedEurekas(res.data || [])
      }
    } catch {
      setSavedEurekas([])
    }
  }

  const handleSaveEureka = async (ideaData) => {
    try {
      const payload = {
        idea: ideaData.idea,
        reasoning: ideaData.reasoning,
        source_nodes: ideaData.nodes
      }
      const res = await apiPost('/api/knowledge-graph/eureka/save', payload)
      if (res.success) {
        // 强制刷新灵感列表缓存
        await fetchSavedEurekas(true)
        setSidebarTab('eureka') // Switch to eureka tab
        return res.data
      }
      return null
    } catch (err) {
      setConfirmDialogState({
        isOpen: true,
        type: 'alert',
        title: '保存失败',
        message: err.message || '保存失败，请稍后重试',
        confirmLabel: '确定'
      })
      return null
    }
  }

  const deleteEureka = async (id) => {
    try {
      const res = await apiDelete(`/api/knowledge-graph/eureka/${id}`)
      if (!res?.success) throw new Error('删除失败')
      setSavedEurekas(prev => prev.filter(item => item.id !== id))
      dataCache.invalidate('kg:eureka:list')
      setEurekaSavedId(prev => (prev === id ? null : prev))
      return true
    } catch {
      showError('删除失败，请稍后重试')
      return false
    }
  }

  // 取消收藏会连同这条灵感的对话记录和笔记一起删除，所以先确认（结果通过 eurekaSavedId 反映到弹窗）
  const handleDeleteEureka = async (id) => {
    setConfirmDialogState({
      isOpen: true,
      type: 'confirm',
      title: '取消收藏',
      message: '取消收藏会删除这条灵感，以及它的对话记录和笔记，确定吗？',
      confirmLabel: '删除',
      isDangerous: true,
      onConfirm: () => deleteEureka(id),
    })
    return false
  }

  // 跳转到灵感对话页进行深入讨论
  const handleDeepDive = (eurekaId) => {
    setEurekaOpen(false)
    navigate(`/eureka-chat/${eurekaId}`)
  }

  const handleOpenSavedEureka = (item) => {
    setEurekaResult({
      id: item.id,
      idea: item.title,
      reasoning: item.content,
      nodes: item.source_nodes
    })
    setIsViewingSaved(true)
    setEurekaSavedId(item.id)
    setEurekaError(null)
    setEurekaLoading(false)
    setEurekaOpen(true)
  }

  // 保留上一次的分析结果（含进行中的分析）：关掉弹窗再打开不会丢失已付费的结果；
  // 想重新分析时，弹窗里有「重新分析」
  const handleOpenGapDialog = () => {
    setGapDialogOpen(true)
  }

  const handleOpenSavedGap = (item) => {
    setGapResult({ gaps: item.gaps || [], communities: item.communities || [] })
    setGapSavedId(item.id)
    setGapError(null)
    setGapLoading(false)
    setGapDialogOpen(true)
  }

  const handleStartGapAnalysis = async () => {
    setGapLoading(true)
    setGapError(null)
    setGapResult(null)
    setGapSavedId(null)

    try {
      const response = await apiPost('/api/knowledge-graph/knowledge-gaps', {})
      if (response.success && response.task_id) {
        startPoll('gaps', response.task_id, {
          onSuccess: (data) => {
            setGapResult(data)
            setGapLoading(false)
            if (!dialogOpenRef.current.gaps) {
              showInfo('知识间隙分析完成')
              setGapDialogOpen(true)
            }
          },
          onError: (msg) => {
            setGapError(msg)
            setGapLoading(false)
            if (!dialogOpenRef.current.gaps) showError(`知识间隙分析失败：${msg}`)
          },
        })
      } else if (response.success && response.gaps) {
        setGapResult(response)
        setGapLoading(false)
      } else {
        setGapError(response.message || '分析失败')
        setGapLoading(false)
      }
    } catch (err) {
      setGapError(err.message || '请求失败')
      setGapLoading(false)
    }
  }

  const refreshSavedGapAnalyses = async () => {
    const cacheKey = 'kg:gaps:list'
    dataCache.invalidate(cacheKey)
    try {
      const res = await apiGet('/api/knowledge-graph/knowledge-gaps/list', {
        cacheKey,
        cacheTTL: CACHE_TTL.GAP_LIST,
        cacheMode: 'no-cache'
      })
      if (res.success) {
        setSavedGapAnalyses(res.items || [])
      }
    } catch (err) {
      console.error('刷新间隙分析列表失败', err)
    }
  }

  const handleSaveGapAnalysis = async ({ gaps, communities }) => {
    try {
      const response = await apiPost('/api/knowledge-graph/knowledge-gaps/save', { gaps, communities })
      if (response.success) {
        await refreshSavedGapAnalyses()
        return { id: response.id }
      }
    } catch (err) {
      showError(err.message || '收藏失败，请稍后重试')
    }
    return null
  }

  const handleDeleteGapAnalysis = async (analysisId) => {
    try {
      await apiDelete(`/api/knowledge-graph/knowledge-gaps/${analysisId}`)
      setSavedGapAnalyses(prev => prev.filter(item => item.id !== analysisId))
      dataCache.invalidate('kg:gaps:list')
      setGapSavedId(prev => (prev === analysisId ? null : prev))
      return true
    } catch (err) {
      showError(err.message || '删除失败，请稍后重试')
      return false
    }
  }

  const confirmDeleteGapAnalysis = (analysisId) => {
    setConfirmDialogState({
      isOpen: true,
      type: 'confirm',
      title: '删除间隙分析',
      message: '确定删除这条已收藏的知识间隙分析吗？',
      confirmLabel: '删除',
      isDangerous: true,
      onConfirm: () => handleDeleteGapAnalysis(analysisId),
    })
  }

  const handleAnalyzeMerge = async () => {
    setManageOpen(false)
    setAiDialogOpen(true)
    setAiLoading(true)
    setAiError(null)
    setAiMessage('')
    setMergeSuggestions([])

    try {
      const response = await apiPost('/api/knowledge-graph/analyze-merge-candidates', {})

      if (response.success && response.task_id) {
        startPoll('merge', response.task_id, {
          onSuccess: (result) => {
            setMergeSuggestions(result?.suggestions || [])
            setAiLoading(false)
            // 分析期间关掉了弹窗：结果到了再打开，否则这次付费的建议就看不到了
            if (!dialogOpenRef.current.merge) {
              showInfo('智能整理建议已生成')
              setAiDialogOpen(true)
            }
          },
          onError: (msg) => {
            setAiError(msg)
            setAiLoading(false)
            if (!dialogOpenRef.current.merge) showError(`智能整理失败：${msg}`)
          },
        })
      } else if (response.success && response.suggestions) {
        setMergeSuggestions(response.suggestions || [])
        setAiMessage(response.message || '')
        setAiLoading(false)
      } else {
        setAiError(response.message || '分析失败')
        setAiLoading(false)
      }
    } catch (err) {
      setAiError(err.message || '请求失败')
      setAiLoading(false)
    }
  }

  const handleEureka = async (sizeOrIds, innovationType = 'auto') => {
    lastEurekaParamsRef.current = { sizeOrIds, innovationType }
    setEurekaOpen(true)
    setEurekaSetupOpen(false)
    setEurekaLoading(true)
    setEurekaError(null)
    setIsViewingSaved(false)
    setEurekaSavedId(null)
    setEurekaResult(null)

    const acceptResult = (data) => {
      // 结果缺标题或内容时按失败处理，不能当作一条灵感展示
      if (data?.idea && data?.reasoning) {
        setEurekaResult(data)
      } else {
        setEurekaError('AI 返回的灵感不完整，请重试')
      }
      setEurekaLoading(false)
    }

    try {
      let payload = { innovation_type: innovationType }
      if (Array.isArray(sizeOrIds)) {
        payload.node_ids = sizeOrIds
      } else {
        payload.size = typeof sizeOrIds === 'number' ? sizeOrIds : 3
      }

      const response = await apiPost('/api/knowledge-graph/eureka', payload)

      if (response.success && response.task_id) {
        startPoll('eureka', response.task_id, {
          onSuccess: (data) => {
            acceptResult(data)
            if (!dialogOpenRef.current.eureka) {
              showInfo('灵感已生成')
              setEurekaOpen(true)
            }
          },
          onError: (msg) => {
            setEurekaError(msg)
            setEurekaLoading(false)
            if (!dialogOpenRef.current.eureka) showError(`灵感生成失败：${msg}`)
          },
        })
      } else if (response.success && response.data) {
        // 兼容旧版同步响应
        acceptResult(response.data)
      } else {
        setEurekaError(response.message || '获取灵感失败')
        setEurekaLoading(false)
      }
    } catch (err) {
      setEurekaError(err.message || '请求失败')
      setEurekaLoading(false)
    }
  }

  const handleApplyMerge = async (suggestion) => {
    if (aiMerging) return
    setAiMerging(true)
    try {
      await apiPost('/api/knowledge-graph/term/merge', {
        source_id: suggestion.source_id,
        target_id: suggestion.target_id
      })
    } catch (err) {
      showError(err.message || '合并失败，请稍后重试')
      setAiMerging(false)
      return
    }

    setMergeSuggestions(prev => retargetSuggestions(
      prev.filter(s => s !== suggestion), suggestion.source_id, suggestion.target_id, suggestion.target_label,
    ))
    dataCache.invalidate(`kg:term:${suggestion.source_id}:docs`)
    dataCache.invalidate(`kg:term:${suggestion.target_id}:docs`)
    try {
      const { allNodes } = await refreshGraph()
      if (selectedNode?.id === suggestion.source_id) {
        const target = allNodes.find(n => n.id === suggestion.target_id)
        setSelectedNode(target || null)
        if (target) setManageNodeId(target.id)
      }
      syncSelectionWith(allNodes)
    } catch (err) {
      showError(err.message || '刷新知识网络失败')
    } finally {
      setAiMerging(false)
    }
  }

  const handleApplyMergeAll = async (suggestions) => {
    if (aiMerging) return
    setAiMerging(true)
    try {
      const res = await apiPost('/api/knowledge-graph/term/merge-batch', {
        merges: suggestions.map(s => ({
          source_id: s.source_id,
          target_id: s.target_id,
        })),
      })
      const merged = res?.merged ?? 0
      const skipped = (res?.results || []).filter((r) => r.skipped).length
      showInfo(skipped ? `已合并 ${merged} 组，${skipped} 组因节点已被合并或不存在而跳过` : `已合并 ${merged} 组节点`)
      setMergeSuggestions([])
      setAiDialogOpen(false)
      suggestions.forEach((s) => {
        dataCache.invalidate(`kg:term:${s.source_id}:docs`)
        dataCache.invalidate(`kg:term:${s.target_id}:docs`)
      })
      const { allNodes } = await refreshGraph()
      syncSelectionWith(allNodes)
    } catch (err) {
      // 失败时保留弹窗和建议，用户可以重试或逐条合并
      showError(err.message || '批量合并失败，请稍后重试')
    } finally {
      setAiMerging(false)
    }
  }

  const handleManageKnowledge = () => {
    setManageOpen((open) => {
      const nextOpen = !open
      if (nextOpen) {
        let initialId = null
        if (selectedNode?.id != null) {
          initialId = selectedNode.id
        } else if (graphNodes.length) {
          const sorted = [...graphNodes].sort(sortByDocCountDesc)
          initialId = sorted[0]?.id ?? null
        }

        if (initialId != null) {
          setManageNodeId(initialId)
          const node = graphNodes.find((n) => n.id === initialId)
          const label = formatDisplayLabel(node?.label)
          setManageLabel(label)
          setManageDescription(node?.description || '')
        } else {
          setManageNodeId(null)
          setManageLabel('')
          setManageDescription('')
        }
        setMergeTargetId('')
        setMergeSearchQuery('')
        setManageSearchQuery('')
        setManageInfo('')
      }
      return nextOpen
    })
  }

  const handleSaveManagedNode = async () => {
    if (!manageNodeId) return

    const payload = {
      display_term: manageLabel.trim(),
      description: manageDescription,
    }

    try {
      setManageLoading(true)
      setManageInfo('正在保存节点信息...')

      const endpoint = `/api/knowledge-graph/term/${manageNodeId}`
      await apiPut(endpoint, payload)

      dataCache.invalidate(`kg:term:${manageNodeId}:docs`)
      const { allNodes } = await refreshGraph()
      syncSelectionWith(allNodes)

      setManageInfo('节点信息已更新')
    } catch (err) {
      // 改名后与已有节点同名：询问是否合并到那个节点
      if (err?.status === 409 && err?.details?.existing_id) {
        const sourceId = manageNodeId
        const targetId = err.details.existing_id
        setManageInfo('')
        setManageOpen(false)
        setConfirmDialogState({
          isOpen: true,
          type: 'confirm',
          title: '已存在同名节点',
          message: `${err.message}。要把当前节点合并到它吗？两者关联的文档会合在一起。`,
          confirmLabel: '合并',
          onConfirm: () => performManagedMerge(sourceId, targetId),
          onCancel: () => setManageOpen(true),
        })
        return
      }
      const msg = err?.message || '保存失败，请稍后重试'
      setManageInfo(msg)
    } finally {
      setManageLoading(false)
    }
  }

  const performManagedMerge = async (sourceId, targetId) => {
    try {
      setManageLoading(true)
      setManageInfo('正在合并节点...')

      await apiPost('/api/knowledge-graph/term/merge', {
        source_id: sourceId,
        target_id: targetId,
      })

      dataCache.invalidate(`kg:term:${sourceId}:docs`)
      dataCache.invalidate(`kg:term:${targetId}:docs`)
      const { allNodes } = await refreshGraph()

      const targetNode = allNodes.find((n) => n.id === targetId) || null
      setSelectedNode((prev) => {
        if (prev && prev.id === sourceId) {
          return targetNode
        }
        return prev
      })
      setSelectedTerms((prev) => {
        const filtered = prev.filter((t) => t.id !== sourceId)
        if (targetNode && !filtered.some((t) => t.id === targetNode.id)) {
          const next = [...filtered, targetNode]
          void updateDocsForTerms(next)
          return next
        }
        void updateDocsForTerms(filtered)
        return filtered
      })
      setSelectedDocs([])
      setManageNodeId(targetId)
      if (targetNode) {
        setManageLabel(formatDisplayLabel(targetNode.label))
        setManageDescription(targetNode.description || '')
      }
      setManageInfo('节点已合并')
    } catch (err) {
      const msg = err?.message || '合并失败，请稍后重试'
      setManageInfo(msg)
    } finally {
      setManageLoading(false)
    }
  }

  const handleDeleteManagedNode = async () => {
    if (!manageNodeId) return

    // 弹出确认框前，先关闭管理弹窗，避免遮挡
    setManageOpen(false)

    setConfirmDialogState({
      isOpen: true,
      type: 'confirm',
      title: '确认删除',
      message: '确定要删除该知识节点吗？这不会删除任何文档，只会移除该节点及其在图谱中的关联。',
      confirmLabel: '删除',
      isDangerous: true,
      onConfirm: async () => {
        try {
          setManageLoading(true)
          setManageInfo('正在删除节点...')

          const endpoint = `/api/knowledge-graph/term/${manageNodeId}`
          await apiDelete(endpoint)

          dataCache.invalidate(`kg:term:${manageNodeId}:docs`)
          await refreshGraph()

          const deletedId = manageNodeId
          setSelectedNode((prev) => (prev?.id === deletedId ? null : prev))
          setSelectedTerms((prev) => prev.filter((t) => t.id !== deletedId))
          setSelectedDocs([])
          setManageNodeId(null)
          setManageInfo('节点已删除')
        } catch (err) {
          const msg = err?.message || '删除失败，请稍后重试'
          setManageInfo(msg)
        } finally {
          setManageLoading(false)
        }
      },
      onCancel: () => {
        // 如果用户取消删除，重新打开管理弹窗
        setManageOpen(true)
      }
    })
  }

  const handleMergeManagedNode = async () => {
    if (!manageNodeId) return

    const sourceId = manageNodeId
    const targetId = Number(mergeTargetId)
    if (!targetId || Number.isNaN(targetId)) {
      setConfirmDialogState({
        isOpen: true,
        type: 'alert',
        title: '提示',
        message: '请选择要合并到的目标节点',
        confirmLabel: '确定'
      })
      return
    }
    if (targetId === sourceId) {
      setConfirmDialogState({
        isOpen: true,
        type: 'alert',
        title: '提示',
        message: '不能合并到自身',
        confirmLabel: '确定'
      })
      return
    }

    // 弹出确认框前，先关闭管理弹窗，避免遮挡
    setManageOpen(false)

    setConfirmDialogState({
      isOpen: true,
      type: 'confirm',
      title: '确认合并',
      message: '确定要将当前节点合并到所选节点吗？这不会删除任何文档，只会合并图谱中的节点关系。',
      confirmLabel: '合并',
      isDangerous: true,
      onConfirm: () => performManagedMerge(sourceId, targetId),
      onCancel: () => {
        // 如果用户取消合并，重新打开管理弹窗
        setManageOpen(true)
      }
    })
  }

  // --- Helper Functions & Event Handlers ---

  const sortByDocCountDesc = (a, b) => (b.document_count || 0) - (a.document_count || 0)

  const updateDocsForTerms = useCallback(async (terms) => {
    const requestSeq = ++docsRequestSeqRef.current

    // 仅保留当前图谱中仍存在的术语，避免删除/合并后继续请求失效 term_id
    const validTerms = terms.filter((t) => graphNodes.some((n) => n.id === t.id))

    if (!validTerms.length) {
      setSelectedDocs([])
      setDocsLoading(false)
      return
    }

    // results 与 validTerms 一一对应；加载失败的位置为 null（不能直接过滤掉，否则后面的关键词会错位）
    const processResults = (results) => {
      const loaded = results.filter(Array.isArray).length
      const allDocsMap = new Map()
      results.forEach((docs, termIdx) => {
        if (!Array.isArray(docs)) return
        const label = formatDisplayLabel(validTerms[termIdx]?.label)
        docs.forEach((d) => {
          if (!allDocsMap.has(d.document_id)) {
            // 复制一份：缓存里的对象不能被改写
            allDocsMap.set(d.document_id, { ...d, matchCount: 0, matchedTerms: [] })
          }
          const entry = allDocsMap.get(d.document_id)
          entry.matchCount++
          entry.matchedTerms.push(label)
        })
      })

      let finalDocs = Array.from(allDocsMap.values())

      if (selectionMode === 'AND' && loaded > 1) {
        finalDocs = finalDocs.filter(d => d.matchCount === loaded)
      }

      // Sort by creation date
      finalDocs.sort((a, b) => new Date(b.created_at) - new Date(a.created_at))

      // 若请求已过期（用户已继续切换关键词），忽略本次结果
      if (requestSeq !== docsRequestSeqRef.current) {
        return
      }

      setSelectedDocs(finalDocs)
    }

    const fetchAll = (cacheMode) => Promise.allSettled(validTerms.map(t =>
      apiGet(`/api/knowledge-graph/term/${t.id}/documents`, {
        cacheKey: `kg:term:${t.id}:docs`,
        cacheTTL: CACHE_TTL.TERM_DOCS,
        ...(cacheMode ? { cacheMode } : {}),
      })
    )).then((settled) => settled.map((item) => (
      item.status === 'fulfilled' && Array.isArray(item.value) ? item.value : null
    )))

    // 检查是否所有需要的缓存都存在
    const cachedResults = validTerms.map(t => dataCache.get(`kg:term:${t.id}:docs`))
    if (cachedResults.every(res => res !== null)) {
      // 使用缓存立即渲染 (SWR 模式)
      processResults(cachedResults)

      // 后台静默刷新：有请求失败时保留缓存结果，不用不完整的数据覆盖
      fetchAll('no-cache').then((refreshed) => {
        if (!mountedRef.current || refreshed.some((r) => r === null)) return
        processResults(refreshed)
      }).catch(err => void err)

      return
    }

    setDocsLoading(true)
    try {
      const results = await fetchAll()
      processResults(results)
      const failed = results.filter((r) => r === null).length
      if (failed && requestSeq === docsRequestSeqRef.current) {
        showError(`有 ${failed} 个关键词的相关文档加载失败，结果可能不完整`)
      }
    } catch {
      setSelectedDocs([])
    } finally {
      // 仅最新请求负责关闭 loading，防止并发切换时闪烁或状态错乱
      if (requestSeq === docsRequestSeqRef.current) {
        setDocsLoading(false)
      }
    }
  }, [selectionMode, graphNodes])

  const handleGraphSelectNode = useCallback((node) => {
    // Single selection from graph click
    if (!node) {
      setSelectedNode(null)
      setSelectedTerms([])
      setSelectedDocs([])
      setManageNodeId(null)
      return
    }
    setSelectedNode(node)
    setIsPanelOpen(true)
    setSelectedTerms([node])
    void updateDocsForTerms([node])
    setManageNodeId(null)
    setSidebarTab('documents') // Switch to documents tab
  }, [updateDocsForTerms])
  // 保持 ref 指向最新的 handler，D3 事件通过 ref 调用，避免过时闭包
  graphSelectRef.current = handleGraphSelectNode

  const handleKeywordSelectNode = useCallback((node) => {
    setSelectedTerms(prev => {
      const exists = prev.some(t => t.id === node.id)
      const nextTerms = exists ? prev.filter(t => t.id !== node.id) : [...prev, node]

      if (nextTerms.length === 1) {
        setSelectedNode(nextTerms[0])
      } else {
        setSelectedNode(null)
      }

      if (!exists) {
        setCenteredNodeId(node.id)
      }

      updateDocsForTerms(nextTerms)
      return nextTerms
    })
  }, [updateDocsForTerms])

  const handleRemoveSelectedTerm = (id) => {
    const nextTerms = selectedTerms.filter(t => t.id !== id)
    setSelectedTerms(nextTerms)
    if (nextTerms.length === 1) {
      setSelectedNode(nextTerms[0])
    } else if (nextTerms.length === 0) {
      setSelectedNode(null)
    }
    updateDocsForTerms(nextTerms)
  }

  // --- Effects ---

  useEffect(() => {
    if (selectedTerms.length) {
      updateDocsForTerms(selectedTerms)
    }
  }, [selectionMode])

  // === D3 力导图：初始化 / 结构重建 ===
  // 节点和边的集合不变时（改名、改描述、文档数变化）只原地更新文字和大小，不重新布局；
  // 集合变化时重建，但已有节点沿用原来的位置、并保持当前缩放，图不会整体重排。
  useEffect(() => {
    if (!svgRef.current || !graphContainerRef.current) {
      return
    }

    if (!graphNodes.length) {
      // 节点全部被删除：清掉旧图，否则画布上还留着上一次的图
      sceneCleanupRef.current?.()
      sceneCleanupRef.current = null
      simulationRef.current = null
      d3SelectionsRef.current = null
      graphStructureKeyRef.current = ''
      d3.select(svgRef.current).selectAll('*').remove()
      if (minimapRef.current) d3.select(minimapRef.current).selectAll('*').remove()
      setLayoutPending(false)
      return
    }

    const structureKey = `${graphNodes.map(n => n.id).join(',')}|${edges.map(e => `${e.source}-${e.target}`).join(',')}`
    if (structureKey === graphStructureKeyRef.current && d3SelectionsRef.current) {
      d3SelectionsRef.current.updateData(graphNodes, edges)
      return
    }
    graphStructureKeyRef.current = structureKey

    // 停止旧的 simulation，并记下现有节点的位置与缩放
    sceneCleanupRef.current?.()
    sceneCleanupRef.current = null
    const previousPositions = nodePositionsRef.current
    const previousTransform = d3SelectionsRef.current ? d3.zoomTransform(svgRef.current) : null

    const container = graphContainerRef.current
    const rect = container.getBoundingClientRect()
    const width = rect.width || 600
    const height = rect.height || 400
    viewportRef.current = { width, height }

    const svg = d3.select(svgRef.current)
    svg.selectAll('*').remove()
    svg.attr('viewBox', `0 0 ${width} ${height}`)

    const g = svg.append('g')

    let radiusScale = null
    let colorScale = null
    const rebuildScales = (data) => {
      const maxCount = d3.max(data, (d) => d.document_count || 1) || 1
      radiusScale = d3.scaleSqrt().domain([1, maxCount]).range([6, 18])
      colorScale = d3.scaleLinear()
        .domain([1, Math.max(2, maxCount * 0.5), maxCount])
        .range(['#14b8a6', '#0f766e', '#134e4a'])
        .clamp(true)
    }

    const simNodes = graphNodes.map((d) => {
      const known = previousPositions.get(d.id)
      return known ? { ...d, x: known.x, y: known.y } : { ...d }
    })
    const simLinks = edges.map((l) => ({ ...l }))
    rebuildScales(simNodes)
    const reusedRatio = simNodes.filter((d) => previousPositions.has(d.id)).length / simNodes.length

    const simulation = d3.forceSimulation(simNodes)
      .force("link", d3.forceLink(simLinks).id(d => d.id).distance(80))
      .force("charge", d3.forceManyBody().strength(-200))
      .force("center", d3.forceCenter(width / 2, height / 2))
    simulation.stop()

    // 大部分节点已有位置时只需微调：降低初始能量，避免整体重排
    if (reusedRatio > 0.5) simulation.alpha(0.3)

    // 自适应预热：小图少跑、大图多跑
    const warmupTicks = Math.min(120, Math.max(40, graphNodes.length * 3))
    const collide = () => d3.forceCollide().radius(d => {
      const r = radiusScale(d.document_count || 1) + 8
      const { en, zh } = splitTitleLabel(d.label)
      const enW = (en || '').length * 6
      const zhW = (zh || '').length * 12
      const maxW = Math.max(enW, zhW)
      return Math.max(r, maxW * 0.55)
    }).strength(0.8)

    let tooltip = d3.select(container).select('.kg-edge-tooltip')
    if (tooltip.empty()) {
      tooltip = d3.select(container)
        .append('div')
        .attr('class', 'kg-edge-tooltip')
        .style('position', 'absolute')
        .style('pointer-events', 'none')
        .style('opacity', 0)
    }

    let nodeTooltip = d3.select(container).select('.kg-node-tooltip')
    if (nodeTooltip.empty()) {
      nodeTooltip = d3.select(container)
        .append('div')
        .attr('class', 'kg-node-tooltip')
    }

    const nodeMap = new Map(simNodes.map(n => [n.id, n]))

    const connectionCountMap = new Map()
    simLinks.forEach(l => {
      const sId = typeof l.source === 'object' ? l.source.id : l.source
      const tId = typeof l.target === 'object' ? l.target.id : l.target
      connectionCountMap.set(sId, (connectionCountMap.get(sId) || 0) + 1)
      connectionCountMap.set(tId, (connectionCountMap.get(tId) || 0) + 1)
    })

    const renderLabel = (selection) => selection.each(function (d) {
      const { en, zh } = splitTitleLabel(d.label)
      const textSel = d3.select(this)
      textSel.selectAll('tspan').remove()
      if (en) {
        textSel
          .append('tspan')
          .text(en)
          .attr('x', 0)
          .attr('dy', 0)
      }
      if (zh) {
        textSel
          .append('tspan')
          .text(zh)
          .attr('x', 0)
          .attr('dy', en ? 14 : 0)
      }
    })

    let cancelled = false
    let warmupTimer = null

    // 预热完成后再创建 DOM 并开始动画：预热期间不做任何 DOM 更新
    const startScene = () => {
      if (cancelled) return
      simulation.force("collide", collide())

      const link = g
        .append('g')
        .attr('stroke', '#cbd5e1')
        .attr('stroke-opacity', 0.6)
        .selectAll('line')
        .data(simLinks)
        .enter()
        .append('line')
        .attr('class', 'kg-graph-link')
        .attr('stroke-width', (d) => 0.6 + Math.log1p(d.weight || 1) * 0.8)
        .on('mouseenter', (event, d) => {
          const sId = typeof d.source === 'object' ? d.source.id : d.source
          const tId = typeof d.target === 'object' ? d.target.id : d.target
          const sNode = nodeMap.get(sId)
          const tNode = nodeMap.get(tId)
          const sLabel = formatDisplayLabel(sNode?.label)
          const tLabel = formatDisplayLabel(tNode?.label)
          const w = d.weight || 1
          tooltip
            // 知识点名称来自文档 / LLM 抽取，拼进 HTML 前必须转义
            .html(`${escapeHtml(sLabel)} ↔ ${escapeHtml(tLabel)}<br/><span style="opacity:0.7">${escapeHtml(w)} 篇文档共现</span>`)
            .style('opacity', 1)
          d3.select(event.target).attr('stroke', '#0f766e').attr('stroke-opacity', 1)
        })
        .on('mousemove', (event) => {
          const box = container.getBoundingClientRect()
          tooltip
            .style('left', (event.clientX - box.left + 12) + 'px')
            .style('top', (event.clientY - box.top - 8) + 'px')
        })
        .on('mouseleave', (event) => {
          tooltip.style('opacity', 0)
          d3.select(event.target).attr('stroke', null).attr('stroke-opacity', null)
        })

      const node = g
        .append('g')
        .selectAll('circle')
        .data(simNodes)
        .enter()
        .append('circle')
        .attr('class', 'kg-graph-node')
        .attr('r', (d) => radiusScale(d.document_count || 1))
        .attr('fill', (d) => colorScale(d.document_count || 1))
        .call(d3.drag()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart()
            d.fx = d.x
            d.fy = d.y
          })
          .on("drag", (event, d) => {
            d.fx = event.x
            d.fy = event.y
          })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0)
            d.fx = null
            d.fy = null
          })
        )
        .on('click', (_, d) => {
          // 读最新的节点数据：节点改名后不重建图，闭包里的旧数组会过期
          const original = graphNodesRef.current.find(n => n.id === d.id)
          if (original) graphSelectRef.current?.(original)
        })
        .on('mouseenter', (event, d) => {
          // 名称与描述来自文档 / LLM 抽取，拼进 HTML 前必须转义
          const labelText = escapeHtml(formatDisplayLabel(d.label))
          const rawDesc = d.description || ''
          const desc = escapeHtml(rawDesc.length > 80 ? rawDesc.slice(0, 80) + '...' : rawDesc)
          const docCount = d.document_count || 0
          const connCount = connectionCountMap.get(d.id) || 0
          nodeTooltip.html(
            `<div class="kg-node-tooltip-title">${labelText}</div>` +
            (desc ? `<div class="kg-node-tooltip-desc">${desc}</div>` : '') +
            `<div class="kg-node-tooltip-stats">` +
            `<span class="kg-node-tooltip-stat">📄 ${docCount} 篇文档</span>` +
            `<span class="kg-node-tooltip-stat">🔗 ${connCount} 个连接</span>` +
            `</div>`
          ).style('opacity', 1)
        })
        .on('mousemove', (event) => {
          const box = container.getBoundingClientRect()
          nodeTooltip
            .style('left', (event.clientX - box.left + 16) + 'px')
            .style('top', (event.clientY - box.top - 12) + 'px')
        })
        .on('mouseleave', () => {
          nodeTooltip.style('opacity', 0)
        })

      const label = g
        .append('g')
        .selectAll('text')
        .data(simNodes)
        .enter()
        .append('text')
        .attr('class', 'kg-graph-label')
        .attr('font-size', 11)
        .attr('fill', '#0f172a')
        .attr('text-anchor', 'middle')
        .call(renderLabel)
        // pointer-events: none on .kg-graph-label — click handled by circle nodes

      const ticked = () => {
        link
          .attr("x1", d => d.source.x)
          .attr("y1", d => d.source.y)
          .attr("x2", d => d.target.x)
          .attr("y2", d => d.target.y)

        node
          .attr("cx", d => d.x)
          .attr("cy", d => d.y)

        label
          .attr("transform", d => `translate(${d.x},${d.y + radiusScale(d.document_count || 1) + 14})`)

        simNodes.forEach((d) => nodePositionsRef.current.set(d.id, { x: d.x, y: d.y }))
      }
      simulation.on("tick", ticked)

      // 语义缩放：根据缩放等级智能控制标签显示
      // scale >= 0.8 → 显示所有标签
      // 0.4 <= scale < 0.8 → 仅显示 document_count 前 30% 的节点标签
      // scale < 0.4 → 仅显示 document_count 前 10% 的节点标签
      let top30Threshold = 1
      let top10Threshold = 1
      const rebuildLabelThresholds = () => {
        const docCounts = simNodes.map(d => d.document_count || 1).sort((a, b) => b - a)
        top30Threshold = docCounts[Math.floor(docCounts.length * 0.3)] || 1
        top10Threshold = docCounts[Math.floor(docCounts.length * 0.1)] || 1
      }
      rebuildLabelThresholds()
      let currentScale = previousTransform?.k ?? 1

      const updateLabelVisibility = (scale) => {
        currentScale = scale
        label.style('display', (d) => {
          if (scale >= 0.8) return null
          const count = d.document_count || 1
          if (scale >= 0.4) return count >= top30Threshold ? null : 'none'
          return count >= top10Threshold ? null : 'none'
        })
      }

      // Minimap
      const mmEl = minimapRef.current
      if (mmEl) {
        const mmW = 160, mmH = 110
        d3.select(mmEl).selectAll('*').remove()
        const mmSvg = d3.select(mmEl).append('svg').attr('viewBox', `0 0 ${mmW} ${mmH}`)
        const allX = simNodes.map(n => n.x || 0)
        const allY = simNodes.map(n => n.y || 0)
        const pad = 20
        const minX = Math.min(...allX) - pad, maxX = Math.max(...allX) + pad
        const minY = Math.min(...allY) - pad, maxY = Math.max(...allY) + pad
        const dataW = maxX - minX || 1, dataH = maxY - minY || 1
        const mmScale = Math.min(mmW / dataW, mmH / dataH)
        const oX = (mmW - dataW * mmScale) / 2, oY = (mmH - dataH * mmScale) / 2
        const mmG = mmSvg.append('g').attr('transform', `translate(${oX},${oY}) scale(${mmScale})`)
        mmG.selectAll('line').data(simLinks).enter().append('line')
          .attr('x1', d => (typeof d.source === 'object' ? d.source.x : 0) - minX)
          .attr('y1', d => (typeof d.source === 'object' ? d.source.y : 0) - minY)
          .attr('x2', d => (typeof d.target === 'object' ? d.target.x : 0) - minX)
          .attr('y2', d => (typeof d.target === 'object' ? d.target.y : 0) - minY)
          .attr('stroke', '#cbd5e1').attr('stroke-width', 0.5 / mmScale)
        mmG.selectAll('circle').data(simNodes).enter().append('circle')
          .attr('cx', d => (d.x || 0) - minX)
          .attr('cy', d => (d.y || 0) - minY)
          .attr('r', 2 / mmScale)
          .attr('fill', '#0f766e')
        const mmViewRect = mmSvg.append('rect').attr('class', 'kg-minimap-viewport')

        mmEl._updateMinimap = (transform) => {
          const invK = 1 / transform.k
          const vx = (-transform.x * invK - minX) * mmScale + oX
          const vy = (-transform.y * invK - minY) * mmScale + oY
          const vw = width * invK * mmScale
          const vh = height * invK * mmScale
          mmViewRect.attr('x', vx).attr('y', vy).attr('width', vw).attr('height', vh)
        }
        mmEl._updateMinimap(previousTransform || d3.zoomIdentity)
      }

      // Zoom
      const zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on("zoom", (event) => {
          g.attr("transform", event.transform)
          updateLabelVisibility(event.transform.k)
          mmEl?._updateMinimap?.(event.transform)
        })

      zoomRef.current = zoom
      svg.call(zoom)
      if (previousTransform) {
        svg.call(zoom.transform, previousTransform)
      }
      updateLabelVisibility(currentScale)
      ticked()

      // 结构不变时的原地更新：名称、描述、文档数（节点大小 / 颜色）、边的权重
      const updateData = (latestNodes, latestEdges) => {
        const latestById = new Map(latestNodes.map((n) => [n.id, n]))
        simNodes.forEach((d) => {
          const latest = latestById.get(d.id)
          if (!latest) return
          d.label = latest.label
          d.description = latest.description
          d.document_count = latest.document_count
        })
        const weights = new Map(latestEdges.map((e) => [`${e.source}-${e.target}`, e.weight]))
        simLinks.forEach((l) => {
          const key = `${l.source.id ?? l.source}-${l.target.id ?? l.target}`
          if (weights.has(key)) l.weight = weights.get(key)
        })
        rebuildScales(simNodes)
        rebuildLabelThresholds()
        if (d3SelectionsRef.current) d3SelectionsRef.current.colorScale = colorScale
        node
          .attr('r', (d) => radiusScale(d.document_count || 1))
          .attr('fill', (d) => colorScale(d.document_count || 1))
        link.attr('stroke-width', (d) => 0.6 + Math.log1p(d.weight || 1) * 0.8)
        label.call(renderLabel)
        updateLabelVisibility(currentScale)
        ticked()
      }

      simulationRef.current = simulation
      d3SelectionsRef.current = { node, label, link, edges: simLinks, colorScale, updateData }
      setSelectionVersion((v) => v + 1)

      // 大图减少动画帧数：每帧都要更新成千上万个 DOM 属性
      if (graphNodes.length > LARGE_GRAPH_NODES) simulation.alphaDecay(0.06)
      simulation.restart()
    }

    if (graphNodes.length <= LARGE_GRAPH_NODES) {
      simulation.tick(warmupTicks)
      startScene()
    } else {
      // 大图分片预热：每片约 16ms，期间页面仍可操作
      setLayoutPending(true)
      let remaining = warmupTicks
      const step = () => {
        if (cancelled) return
        const started = performance.now()
        while (remaining > 0 && performance.now() - started < 16) {
          simulation.tick()
          remaining -= 1
        }
        if (remaining > 0) {
          warmupTimer = setTimeout(step, 0)
          return
        }
        setLayoutPending(false)
        startScene()
      }
      warmupTimer = setTimeout(step, 0)
    }

    sceneCleanupRef.current = () => {
      cancelled = true
      clearTimeout(warmupTimer)
      simulation.stop()
    }
    // 不在这里返回清理函数：依赖变化时 React 会先执行清理，原地更新就无从谈起；
    // 重建时由上面的 sceneCleanupRef 停止旧图，卸载时由下面的 effect 停止
  }, [graphNodes, edges])

  useEffect(() => () => {
    sceneCleanupRef.current?.()
    simulationRef.current = null
  }, [])

  // === D3 视觉更新：选中高亮（轻量级，不重建 simulation） ===
  useEffect(() => {
    const sels = d3SelectionsRef.current
    if (!sels) return

    const isSelected = (d) => selectedTerms.some(t => t.id === d.id)

    // 计算选中节点的邻居
    const neighborIds = new Set()
    if (selectedTerms.length > 0) {
      const selectedIds = new Set(selectedTerms.map(t => t.id))
      const edgesData = sels.edges || edges
      edgesData.forEach(edge => {
        const sourceId = typeof edge.source === 'object' ? edge.source.id : edge.source
        const targetId = typeof edge.target === 'object' ? edge.target.id : edge.target

        if (selectedIds.has(sourceId)) {
          neighborIds.add(targetId)
        } else if (selectedIds.has(targetId)) {
          neighborIds.add(sourceId)
        }
      })
    }

    const cs = sels.colorScale
    sels.node
      .classed('active', d => isSelected(d))
      .classed('active-neighbor', d => neighborIds.has(d.id) && !isSelected(d))
      .attr('fill', d => {
        if (isSelected(d)) return null
        if (neighborIds.has(d.id)) return null
        return cs ? cs(d.document_count || 1) : null
      })

    sels.label
      .classed('active', d => isSelected(d))
      .classed('active-neighbor', d => neighborIds.has(d.id) && !isSelected(d))

    // 边脉冲动画：选中节点的连接线添加 active-edge class
    const selectedIds = new Set(selectedTerms.map(t => t.id))
    sels.link
      .classed('active-edge', d => {
        if (selectedIds.size === 0) return false
        const sId = typeof d.source === 'object' ? d.source.id : d.source
        const tId = typeof d.target === 'object' ? d.target.id : d.target
        return selectedIds.has(sId) || selectedIds.has(tId)
      })
  }, [selectedTerms, selectionVersion])

  // === D3 视觉更新：搜索高亮（搜索时在图谱中闪烁匹配节点） ===
  useEffect(() => {
    const sels = d3SelectionsRef.current
    if (!sels) return

    if (!debouncedFilter) {
      sels.node.classed('search-match', false)
      sels.label.classed('search-match', false)
      return
    }

    const matchedIds = new Set(
      filterNodesByQuery(graphNodes, debouncedFilter).map(n => n.id)
    )

    sels.node.classed('search-match', d => matchedIds.has(d.id))
    sels.label.classed('search-match', d => matchedIds.has(d.id))
  }, [debouncedFilter, graphNodes, selectionVersion])

  // Effect: Auto-select largest node on load
  useEffect(() => {
    // Only auto-select if we have nodes but no selection
    if (graphNodes.length > 0 && !selectedNode && selectedTerms.length === 0) {
      // Sort by document count desc
      const sorted = [...graphNodes].sort((a, b) => (b.document_count || 0) - (a.document_count || 0))
      if (sorted.length > 0) {
        const topNode = sorted[0]
        // Reuse the existing selection logic
        handleGraphSelectNode(topNode)
      }
    }
  }, [graphNodes])

  const [centeredNodeId, setCenteredNodeId] = useState(null)
  const [pinnedNode, setPinnedNode] = useState(null)
  const [isPanelOpen, setIsPanelOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [batchSelectedIds, setBatchSelectedIds] = useState(new Set())

  // Knowledge Gap
  const [gapDialogOpen, setGapDialogOpen] = useState(false)
  const [gapLoading, setGapLoading] = useState(false)
  const [gapError, setGapError] = useState(null)
  const [gapResult, setGapResult] = useState(null)
  const [gapSavedId, setGapSavedId] = useState(null) // 当前展示的间隙分析已收藏时的记录 ID

  dialogOpenRef.current = { eureka: eurekaOpen, gaps: gapDialogOpen, merge: aiDialogOpen }


  // Panel resizing logic
  const [panelWidth, setPanelWidth] = useState(260)
  const isResizingRef = useRef(false)

  // Handle panel resize
  useEffect(() => {
    const handleMouseMove = (e) => {
      if (!isResizingRef.current) return
      // Limit width between 200px and 600px
      const newWidth = Math.max(200, Math.min(600, e.clientX - 36)) // Subtract margin/padding offset approx
      setPanelWidth(newWidth)
    }

    const handleMouseUp = () => {
      if (isResizingRef.current) {
        isResizingRef.current = false
        document.body.style.cursor = 'default'
        document.body.style.userSelect = 'auto'
      }
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  const handleClosePanel = () => {
    setPinnedNode(null)
    setIsPanelOpen(false)
  }

  // Effect: Center view on selected node, term, or manually centered node
  useEffect(() => {
    if (!zoomRef.current || !svgRef.current || !viewportRef.current) return

    // Determine target node
    let targetId = centeredNodeId
    if (!targetId) {
      if (selectedNode) {
        targetId = selectedNode.id
      } else if (selectedTerms.length > 0) {
        targetId = selectedTerms[selectedTerms.length - 1].id
      }
    }

    if (!targetId) return

    const svg = d3.select(svgRef.current)
    const allNodesData = svg.selectAll('.kg-graph-node').data()
    const targetDatum = allNodesData.find(d => d.id === targetId)

    // Reset centeredNodeId after finding target to allow subsequent selections to work
    if (centeredNodeId) {
      setCenteredNodeId(null)
    }

    if (targetDatum) {
      const { width, height } = viewportRef.current
      const currentTransform = d3.zoomTransform(svg.node())
      // Keep current scale if just centering, or use default if needed. 
      // User might want to zoom in? Let's keep it consistent with selection zoom.
      // If we want to keep context, maybe don't zoom in too much?
      // Re-using existing logic:
      const targetScale = currentTransform.k

      const x = width / 2 - targetDatum.x * targetScale
      const y = height / 2 - targetDatum.y * targetScale

      const newTransform = d3.zoomIdentity
        .translate(x, y)
        .scale(targetScale)

      svg.transition()
        .duration(750)
        .call(zoomRef.current.transform, newTransform)
    }
  }, [selectedNode, selectedTerms, centeredNodeId])

  const suggestionList = useMemo(() => {
    return filterNodesByQuery(graphNodes, debouncedFilter).slice(0, 10)
  }, [graphNodes, debouncedFilter])

  const mergeSuggestionList = useMemo(() => {
    // 直接使用 mergeSearchQuery (无 debounce)，响应更直接
    const results = filterNodesByQuery(graphNodes, mergeSearchQuery)
    // 排除自身 (使用 != 允许字符串/数字 ID 兼容)
    return results.filter(n => n.id != manageNodeId).slice(0, 10)
  }, [graphNodes, mergeSearchQuery, manageNodeId])

  const nodeSuggestionList = useMemo(() => {
    // 知识管理里「搜索要管理的节点」的候选
    return filterNodesByQuery(graphNodes, manageSearchQuery).slice(0, 10)
  }, [graphNodes, manageSearchQuery])


  // Determine which node to display connections for
  const displayNode = pinnedNode || selectedNode

  // Compute connected nodes for the selected node
  const connectedNodes = useMemo(() => {
    if (!displayNode || !graphNodes.length || !edges.length) return []

    // Count connections between displayNode and other nodes
    const connectionCounts = new Map()

    edges.forEach(edge => {
      const sourceId = typeof edge.source === 'object' ? edge.source.id : edge.source
      const targetId = typeof edge.target === 'object' ? edge.target.id : edge.target

      let otherId = null
      if (sourceId === displayNode.id) {
        otherId = targetId
      } else if (targetId === displayNode.id) {
        otherId = sourceId
      }

      if (otherId) {
        // Sum weights if multiple edges or aggregated edges exist
        const weight = typeof edge.weight === 'number' ? edge.weight : 1
        connectionCounts.set(otherId, (connectionCounts.get(otherId) || 0) + weight)
      }
    })

    // Map to nodes and sort by count
    return graphNodes
      .filter(n => connectionCounts.has(n.id))
      .map(n => ({
        node: n,
        count: connectionCounts.get(n.id)
      }))
      .sort((a, b) => b.count - a.count)
  }, [displayNode, graphNodes, edges])

  // 导出整张图，而不只是当前缩放下可见的区域
  const exportGraph = (format) => {
    const svgEl = svgRef.current
    const content = svgEl?.querySelector(':scope > g')
    if (!svgEl || !content) return
    const box = content.getBBox()
    const pad = 40
    const width = Math.max(1, Math.ceil(box.width + pad * 2))
    const height = Math.max(1, Math.ceil(box.height + pad * 2))

    const clone = svgEl.cloneNode(true)
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    clone.setAttribute('viewBox', `${box.x - pad} ${box.y - pad} ${width} ${height}`)
    clone.setAttribute('width', String(width))
    clone.setAttribute('height', String(height))
    clone.querySelector(':scope > g')?.removeAttribute('transform')
    // 缩小时隐藏的标签在导出图里全部显示
    clone.querySelectorAll('.kg-graph-label').forEach((el) => { el.style.display = '' })
    const style = document.createElementNS('http://www.w3.org/2000/svg', 'style')
    style.textContent = EXPORT_STYLES
    clone.insertBefore(style, clone.firstChild)

    const svgBlob = new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml;charset=utf-8' })
    if (format === 'svg') {
      downloadUrl(URL.createObjectURL(svgBlob), 'knowledge-graph.svg')
      return
    }

    const url = URL.createObjectURL(svgBlob)
    const img = new Image()
    img.onload = () => {
      // 2 倍分辨率，但不超过浏览器画布尺寸上限
      const scale = Math.min(2, 8192 / Math.max(width, height))
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(width * scale)
      canvas.height = Math.round(height * scale)
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      URL.revokeObjectURL(url)
      canvas.toBlob((blob) => {
        if (blob) downloadUrl(URL.createObjectURL(blob), 'knowledge-graph.png')
        else showError('导出 PNG 失败')
      }, 'image/png')
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      showError('导出 PNG 失败')
    }
    img.src = url
  }

  const handleBatchDelete = () => {
    const ids = Array.from(batchSelectedIds)
    setConfirmDialogState({
      isOpen: true,
      type: 'confirm',
      title: '批量删除',
      message: `确定要删除选中的 ${ids.length} 个节点吗？这不会删除任何文档。`,
      confirmLabel: '删除',
      isDangerous: true,
      onConfirm: async () => {
        try {
          const res = await apiPost('/api/knowledge-graph/term/delete-batch', { term_ids: ids })
          showInfo(`已删除 ${res?.deleted_ids?.length ?? 0} 个节点`)
        } catch (err) {
          showError(err.message || '批量删除失败，请稍后重试')
          return
        }
        ids.forEach((id) => dataCache.invalidate(`kg:term:${id}:docs`))
        setBatchSelectedIds(new Set())
        try {
          await refreshGraph()
        } catch (err) {
          showError(err.message || '刷新知识网络失败')
        }
        setSelectedNode(null)
        setSelectedTerms([])
        setSelectedDocs([])
      }
    })
  }

  return (
    <div className={`kg-page ${isFullscreen ? 'kg-fullscreen' : ''}`}>
      <Motion.section
        className="kg-top-section"
        initial={animateIn ? { opacity: 0, y: -12 } : false}
        animate={{ opacity: 1, y: 0 }}
      >
        <div
          className="kg-graph-container"
          ref={graphContainerRef}
        >
          <svg ref={svgRef} className="kg-graph-svg" />

          {layoutPending && (
            <div className="kg-graph-layout-pending">正在布局知识网络…</div>
          )}

          {!loading && error && (
            <div className="kg-graph-empty-overlay">
              <div className="kg-graph-empty-title">知识网络加载失败</div>
              <div className="kg-graph-empty-text">{error}</div>
            </div>
          )}

          {!loading && !error && graphNodes.length === 0 && (
            <div className="kg-graph-empty-overlay">
              <div className="kg-graph-empty-title">还没有知识节点</div>
              <div className="kg-graph-empty-text">上传并解析文档后，关键概念会在这里形成知识网络。</div>
            </div>
          )}

          <button
            className="kg-fullscreen-btn"
            onClick={() => setIsFullscreen(prev => !prev)}
            title={isFullscreen ? '退出全屏' : '全屏模式'}
          >
            {isFullscreen ? '✕' : '⛶'}
          </button>

          {displayNode && connectedNodes.length > 0 && isPanelOpen && (
            <div
              className="kg-connected-nodes-panel"
              style={{ width: panelWidth }}
            >
              <div className="kg-connected-header kg-connected-header-layout">
                <span className="kg-connected-title kg-connected-title-wrap" title={formatDisplayLabel(displayNode.label)}>
                  {formatDisplayLabel(displayNode.label)} 的连接 ({connectedNodes.length})
                </span>
                <div className="kg-connected-header-actions">
                  <button
                    className={`kg-pin-btn-icon ${pinnedNode ? 'active' : ''}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      // Toggle pin state
                      if (pinnedNode) {
                        setPinnedNode(null)
                      } else {
                        setPinnedNode(selectedNode)
                      }
                    }}
                    title={pinnedNode ? "取消固定" : "固定弹窗"}
                  >
                    📌
                  </button>
                  <button
                    className="kg-close-btn kg-close-btn-icon"
                    onClick={(e) => {
                      e.stopPropagation()
                      handleClosePanel()
                    }}
                    title="关闭面板"
                  >
                    ×
                  </button>
                </div>
              </div>
              <div className="kg-connected-list">
                {connectedNodes.map(({ node, count }) => {
                  const isSelected = selectedTerms.some(t => t.id === node.id)
                  return (
                    <button
                      key={node.id}
                      className={`kg-connected-item ${isSelected ? 'active' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation()
                        if (pinnedNode) {
                          handleKeywordSelectNode(node)
                        } else {
                          handleGraphSelectNode(node)
                        }
                      }}
                      title={formatDisplayLabel(node.label)}
                    >
                      {/* Removed blue dot as requested */}
                      <span className="kg-connected-label">
                        {formatDisplayLabel(node.label)}
                        <span className="kg-connected-count">×{count}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
              <div
                className="kg-resize-handle"
                onMouseDown={(e) => {
                  e.stopPropagation()
                  e.preventDefault() // Prevent selection
                  isResizingRef.current = true
                  document.body.style.cursor = 'col-resize'
                  document.body.style.userSelect = 'none'
                }}
              />
            </div>
          )}

          <div className="kg-graph-actions-bottom-right">
            <div className="kg-graph-actions-row">
              <button
                type="button"
                className="kg-btn-base kg-btn-white"
                onClick={() => exportGraph('svg')}
                disabled={!graphNodes.length || layoutPending}
                title="导出整张图谱为 SVG"
              >
                SVG
              </button>
              <button
                type="button"
                className="kg-btn-base kg-btn-white"
                onClick={() => exportGraph('png')}
                disabled={!graphNodes.length || layoutPending}
                title="导出整张图谱为 PNG（2x 分辨率）"
              >
                PNG
              </button>
              <button
                type="button"
                className="kg-btn-base kg-btn-teal"
                onClick={handleManageKnowledge}
                disabled={manageLoading}
                title={manageOpen ? '关闭管理' : '管理节点'}
              >
                {manageOpen ? '完成管理' : '知识管理'}
              </button>
              <button
                type="button"
                className="kg-btn-base kg-btn-eureka"
                onClick={() => setEurekaSetupOpen(true)}
                title="寻找未连接的创新点"
              >
                💡 灵感生成
              </button>
              <button
                type="button"
                className="kg-btn-base kg-btn-white"
                onClick={handleOpenGapDialog}
                title="分析知识盲区"
              >
                🔬 知识间隙
              </button>
            </div>
          </div>

          <div className="kg-minimap" ref={minimapRef} />

          {manageInfo && <div className="kg-manage-info">{manageInfo}</div>}
        </div>
      </Motion.section >

      <Motion.section
        className="kg-bottom-section"
        initial={animateIn ? { opacity: 0, y: 12 } : false}
        animate={{ opacity: 1, y: 0 }}
      >
        <div className="kg-bottom-left">
          <div className="kg-filter-bar">
            <div className="kg-filter-input-wrapper">
              <input
                type="text"
                className="kg-filter-input"
                placeholder="筛选关键词..."
                value={filterQuery}
                onChange={(e) => setFilterQuery(e.target.value)}
                onKeyDown={(e) => {
                  e.stopPropagation()
                  if (e.key === 'Enter') {
                    e.preventDefault()
                  }
                }}
              />
              {suggestionList.length > 0 && (
                <div className="kg-filter-suggestions">
                  {suggestionList.map((term) => (
                    <button
                      key={term.id}
                      type="button"
                      className="kg-filter-suggestion-item"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => {
                        void handleKeywordSelectNode(term)
                        setFilterQuery('')
                      }}
                    >
                      {formatDisplayLabel(term.label)}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="kg-selected-bar">
            <div className="kg-mode-toggle">
              <button
                type="button"
                className={`kg-mode-toggle-btn ${selectionMode === 'AND' ? 'active' : ''}`}
                onClick={() => setSelectionMode('AND')}
                title="只显示同时包含所有选中关键词的文档"
              >
                同时包含
              </button>
              <button
                type="button"
                className={`kg-mode-toggle-btn ${selectionMode === 'OR' ? 'active' : ''}`}
                onClick={() => setSelectionMode('OR')}
                title="显示包含任意一个选中关键词的文档"
              >
                任意包含
              </button>
            </div>

            {selectedTerms.length > 0 && (
              <>
                {selectedTerms.map((term) => (
                  <button
                    key={term.id}
                    className="kg-selected-term-chip"
                    onClick={() => handleRemoveSelectedTerm(term.id)}
                    title="点击移除筛选"
                  >
                    <span className="kg-selected-term-chip-text">
                      {formatDisplayLabel(term.label)}
                    </span>
                  </button>
                ))}
              </>
            )}
          </div>

          {batchSelectedIds.size > 0 && (
            <div className="kg-batch-bar">
              <span className="kg-batch-bar-text">已选中 {batchSelectedIds.size} 个节点</span>
              <button
                className="kg-batch-bar-btn danger"
                onClick={handleBatchDelete}
              >
                批量删除
              </button>
              <button className="kg-batch-bar-btn cancel" onClick={() => setBatchSelectedIds(new Set())}>
                取消
              </button>
            </div>
          )}

          {!loading && nodes.length > 1 && batchSelectedIds.size === 0 && (
            <div className="kg-node-cloud-hint">按住 Shift 点击关键词可多选，然后批量删除</div>
          )}

          <div className="kg-node-cloud">
            {loading ? (
              SKELETON_WIDTHS.map((w, i) => (
                <div
                  key={i}
                  className="kg-node-chip skeleton kg-node-chip-skeleton"
                  style={{ width: `${w}px` }}
                />
              ))
            ) : (
              (() => {
                const sorted = [...nodes].sort(sortByDocCountDesc)
                const visible = nodeCloudExpanded ? sorted : sorted.slice(0, NODE_CLOUD_LIMIT)
                const hasMore = sorted.length > NODE_CLOUD_LIMIT
                const cloudMax = sorted[0]?.document_count || 1
                const chipFontSize = (count) => {
                  const ratio = Math.min((count || 1) / Math.max(cloudMax, 2), 1)
                  return 11.5 + ratio * 3.5
                }
                return (
                  <>
                    {visible.map((node) => {
                      const isSelected = selectedTerms.some((t) => t.id === node.id)
                      const isBatchSelected = batchSelectedIds.has(node.id)
                      return (
                        <button
                          key={node.id}
                          type="button"
                          className={`kg-node-chip ${isSelected ? 'active' : ''} ${isBatchSelected ? 'batch-selected' : ''}`}
                          style={{ fontSize: `${chipFontSize(node.document_count)}px` }}
                          onClick={(e) => {
                            if (e.shiftKey) {
                              setBatchSelectedIds(prev => {
                                const next = new Set(prev)
                                if (next.has(node.id)) next.delete(node.id)
                                else next.add(node.id)
                                return next
                              })
                            } else {
                              handleKeywordSelectNode(node)
                            }
                          }}
                        >
                          <span className="kg-node-label">{formatDisplayLabel(node.label)}</span>
                          {typeof node.document_count === 'number' && (
                            <span className="kg-node-count">{node.document_count}</span>
                          )}
                        </button>
                      )
                    })}
                    {hasMore && (
                      <button
                        type="button"
                        className="kg-node-chip kg-node-chip-toggle"
                        onClick={() => setNodeCloudExpanded(prev => !prev)}
                      >
                        {nodeCloudExpanded ? '收起' : `查看全部 ${sorted.length} 个`}
                        <span className="kg-node-cloud-toggle-arrow">{nodeCloudExpanded ? '↑' : '↓'}</span>
                      </button>
                    )}
                  </>
                )
              })()
            )}
          </div>

          {orphanNodes.length > 0 && !loading && (
            <div className="kg-orphan-section">
              <div className="kg-orphan-header">
                未连接的概念 ({orphanNodes.length})
              </div>
              <div className="kg-node-cloud">
                {orphanNodes.slice(0, 30).map((node) => {
                  const isSelected = selectedTerms.some((t) => t.id === node.id)
                  return (
                    <button
                      key={node.id}
                      type="button"
                      className={`kg-node-chip kg-node-chip-orphan ${isSelected ? 'active' : ''}`}
                      onClick={() => handleKeywordSelectNode(node)}
                    >
                      <span className="kg-node-label">{formatDisplayLabel(node.label)}</span>
                      {typeof node.document_count === 'number' && (
                        <span className="kg-node-count">{node.document_count}</span>
                      )}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        <div className="kg-bottom-right">
          {/* Tab Header */}
          <div className="kg-sidebar-tabs">
            <button
              className={`kg-sidebar-tab ${sidebarTab === 'documents' ? 'active' : ''}`}
              onClick={() => setSidebarTab('documents')}
            >
              相关文档
              {selectedDocs.length > 0 && <span className="kg-tab-badge">{selectedDocs.length}</span>}
            </button>
            <button
              className={`kg-sidebar-tab ${sidebarTab === 'eureka' ? 'active' : ''}`}
              onClick={() => setSidebarTab('eureka')}
            >
              灵感
              {savedEurekas.length > 0 && <span className="kg-tab-badge">{savedEurekas.length}</span>}
            </button>
            <button
              className={`kg-sidebar-tab ${sidebarTab === 'gaps' ? 'active' : ''}`}
              onClick={() => setSidebarTab('gaps')}
            >
              间隙
              {savedGapAnalyses.length > 0 && <span className="kg-tab-badge">{savedGapAnalyses.length}</span>}
            </button>
          </div>

          <div className="kg-sidebar-content">
            {sidebarTab === 'documents' ? (
              <div className="kg-right-panel-scroll">
                {selectedNode || selectedTerms.length > 0 ? (
                  <>
                    {docsLoading ? (
                      <div className="kg-docs-loading-wrap">
                        <SkeletonListItem lines={2} />
                        <SkeletonListItem lines={2} />
                        <SkeletonListItem lines={2} />
                        <SkeletonListItem lines={2} />
                        <SkeletonListItem lines={2} />
                      </div>
                    ) : (
                      <div className="kg-sidebar-list">
                        {selectedDocs.length ? (
                          selectedDocs.map((doc) => (
                            <div
                              key={doc.document_id}
                              className="kg-sidebar-card kg-card-doc"
                              onClick={(e) => {
                                e.preventDefault()
                                if (!doc?.document_id) return
                                const url = new URL(window.location.origin)
                                url.searchParams.set('document_id', String(doc.document_id))
                                const newWindow = window.open(url.toString(), '_blank', 'noopener')
                                if (!newWindow && typeof onOpenDocument === 'function') {
                                  onOpenDocument(doc)
                                }
                              }}
                            >
                              <div className="kg-card-content">
                                <h3 className="kg-card-title">
                                  {doc.display_name || doc.extracted_title || doc.filename}
                                </h3>
                                {doc.summary && (
                                  <p className="kg-card-summary">
                                    {stripSummaryPrefix(doc.summary).slice(0, 120)}...
                                  </p>
                                )}
                                {doc.matchedTerms && doc.matchedTerms.length > 0 && (
                                  <div className="kg-card-tags">
                                    {doc.matchedTerms.map((t, i) => (
                                      <span key={i} className="kg-card-tag">{t}</span>
                                    ))}
                                  </div>
                                )}
                              </div>
                            </div>
                          ))
                        ) : (
                          <div className="kg-empty-placeholder large kg-empty-placeholder-muted kg-empty-placeholder-docs">
                            <p className="kg-empty-placeholder-text">暂无关联文档</p>
                          </div>
                        )}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="kg-empty-placeholder large kg-empty-placeholder-muted kg-empty-placeholder-default">
                    <p className="kg-empty-placeholder-text">点击图谱节点查看详情</p>
                  </div>
                )}
              </div>
            ) : sidebarTab === 'eureka' ? (
              <div className="kg-right-panel-scroll">
                {savedEurekas.length > 0 ? (
                  <div className="kg-sidebar-list">
                    {savedEurekas.map((item) => (
                      <div
                        key={item.id}
                        className="kg-sidebar-card kg-card-eureka"
                        onClick={() => handleOpenSavedEureka(item)}
                      >
                        <div className="kg-card-content">
                          <h3 className="kg-card-title">{item.title}</h3>
                          <div className="kg-card-summary">
                            {item.content.length > 80 ? item.content.slice(0, 80) + '...' : item.content}
                          </div>
                          {item.source_nodes && item.source_nodes.length > 0 && (
                            <div className="kg-card-tags">
                              {item.source_nodes.slice(0, 4).map((n, i) => (
                                <span key={i} className="kg-card-tag">{formatDisplayLabel(n.label || n.term)}</span>
                              ))}
                              {item.source_nodes.length > 4 && <span className="kg-card-tag">+{item.source_nodes.length - 4}</span>}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="kg-empty-placeholder large kg-empty-placeholder-muted kg-empty-placeholder-default">
                    <div className="kg-placeholder-icon kg-placeholder-icon-muted">💡</div>
                    <p className="kg-empty-placeholder-text">收集你的 Eureka 时刻...</p>
                  </div>
                )}
              </div>
            ) : (
              <div className="kg-right-panel-scroll">
                {savedGapAnalyses.length > 0 ? (
                  <div className="kg-sidebar-list">
                    {savedGapAnalyses.map((item) => (
                      <div
                        key={item.id}
                        className="kg-sidebar-card kg-card-eureka"
                        onClick={() => handleOpenSavedGap(item)}
                      >
                        <button
                          type="button"
                          className="kg-card-delete-btn"
                          title="删除这条分析"
                          aria-label="删除这条分析"
                          onClick={(e) => {
                            e.stopPropagation()
                            confirmDeleteGapAnalysis(item.id)
                          }}
                        >
                          ×
                        </button>
                        <div className="kg-card-content">
                          <h3 className="kg-card-title">
                            {item.gap_count} 个间隙 · {item.community_count} 个社区
                          </h3>
                          <div className="kg-card-summary">
                            {item.gaps?.slice(0, 2).map(g => g.theme_a && g.theme_b ? `${g.theme_a} ⇄ ${g.theme_b}` : '').filter(Boolean).join('；') || '查看详情'}
                          </div>
                          <div className="kg-card-tags">
                            <span className="kg-card-tag">
                              {item.created_at ? new Date(item.created_at).toLocaleDateString('zh-CN') : ''}
                            </span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="kg-empty-placeholder large kg-empty-placeholder-muted kg-empty-placeholder-default">
                    <div className="kg-placeholder-icon kg-placeholder-icon-muted">🔬</div>
                    <p className="kg-empty-placeholder-text">分析并收藏你的知识间隙...</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </Motion.section >

      <MergeSuggestionsDialog
        isOpen={aiDialogOpen}
        onClose={() => setAiDialogOpen(false)}
        suggestions={mergeSuggestions}
        onMerge={handleApplyMerge}
        onMergeAll={handleApplyMergeAll}
        loading={aiLoading}
        merging={aiMerging}
        error={aiError}
        message={aiMessage}
      />

      <ManageKnowledgeDialog
        isOpen={manageOpen}
        onClose={() => setManageOpen(false)}
        loading={manageLoading}
        manageNodeId={manageNodeId}
        setManageNodeId={setManageNodeId} // Pass setter
        manageLabel={manageLabel}
        setManageLabel={setManageLabel}
        manageDescription={manageDescription}
        setManageDescription={setManageDescription}
        manageSearchQuery={manageSearchQuery}
        setManageSearchQuery={setManageSearchQuery}
        nodeSuggestionList={nodeSuggestionList}
        mergeSearchQuery={mergeSearchQuery}
        setMergeSearchQuery={setMergeSearchQuery}
        mergeTargetId={mergeTargetId}
        setMergeTargetId={setMergeTargetId}

        mergeSuggestionList={mergeSuggestionList}
        onSave={handleSaveManagedNode}
        onDelete={handleDeleteManagedNode}
        onMerge={handleMergeManagedNode}
        onAiOrganize={handleAnalyzeMerge}
        organizeCostLabel={costLabelOf(costs?.organize)}
      />

      <EurekaSetupDialog
        isOpen={eurekaSetupOpen}
        onClose={() => setEurekaSetupOpen(false)}
        onGenerate={handleEureka}
        allNodes={graphNodes}
        edges={edges}
        costLabel={costLabelOf(costs?.eureka)}
      />

      <EurekaDialog
        isOpen={eurekaOpen}
        onClose={() => {
          if (eurekaResult && !isViewingSaved && !eurekaSavedId && !eurekaLoading) {
            setConfirmDialogState({
              isOpen: true,
              type: 'confirm',
              title: '未收藏的灵感',
              message: '当前灵感尚未收藏，关闭后将丢失。要先收藏吗？',
              confirmLabel: '收藏并关闭',
              cancelLabel: '不收藏',
              onConfirm: async () => {
                const saved = await handleSaveEureka(eurekaResult)
                if (saved?.id) {
                  setEurekaSavedId(saved.id)
                  setEurekaOpen(false)
                }
              },
              onCancel: () => setEurekaOpen(false),
              // 点空白处：回到灵感弹窗，不要误丢结果
              onDismiss: () => {},
            })
          } else {
            setEurekaOpen(false)
          }
        }}
        result={eurekaResult}
        loading={eurekaLoading}
        error={eurekaError}
        onRetry={() => {
          const p = lastEurekaParamsRef.current
          handleEureka(p?.sizeOrIds ?? 3, p?.innovationType ?? 'auto')
        }}
        onSave={handleSaveEureka}
        onDelete={handleDeleteEureka}
        savedId={eurekaSavedId}
        onSavedIdChange={setEurekaSavedId}
        onDeepDive={handleDeepDive}
      />

      <KnowledgeGapDialog
        isOpen={gapDialogOpen}
        onClose={() => setGapDialogOpen(false)}
        onStartAnalysis={handleStartGapAnalysis}
        onSave={handleSaveGapAnalysis}
        onDelete={handleDeleteGapAnalysis}
        gaps={gapResult?.gaps || []}
        communities={gapResult?.communities || []}
        loading={gapLoading}
        error={gapError}
        message={gapResult?.message}
        savedId={gapSavedId}
        onSavedIdChange={setGapSavedId}
        costLabel={costLabelOf(costs?.organize)}
      />

      <ConfirmDialog
        isOpen={confirmDialogState.isOpen}
        title={confirmDialogState.title}
        message={confirmDialogState.message}
        confirmLabel={confirmDialogState.confirmLabel}
        cancelLabel={confirmDialogState.cancelLabel}
        isDangerous={confirmDialogState.isDangerous}
        type={confirmDialogState.type}
        onConfirm={(dontRemind) => {
          if (confirmDialogState.onConfirm) {
            confirmDialogState.onConfirm(dontRemind)
          }
          setConfirmDialogState(prev => ({ ...prev, isOpen: false }))
        }}
        onCancel={() => {
          confirmDialogState.onCancel?.()
          setConfirmDialogState(prev => ({ ...prev, isOpen: false }))
        }}
        onDismiss={() => {
          const { onDismiss, onCancel } = confirmDialogState
          ;(onDismiss || onCancel)?.()
          setConfirmDialogState(prev => ({ ...prev, isOpen: false }))
        }}
      />
    </div>
  )
}

export default KnowledgeGraphPage
