import React, { useState } from 'react'
import { motion as Motion } from 'framer-motion'
import { SkeletonNotesList } from '../Skeleton'
import PodcastPlayer from '../PodcastPlayer'
import { apiRequest } from '../../utils/api'
import { parseTaskErrorResponse, normalizeTaskErrorMessage } from '../../utils/taskError'
import GenerationConfigModal from './GenerationConfigModal'
import { useCapability } from '../../stores/runtimeStore'

const FLASHCARD_COUNT_PRESETS = {
  less: 6,
  standard: 10,
  more: 15,
}

const QUIZ_COUNT_PRESETS = {
  less: 6,
  standard: 10,
  more: 15,
}

const HIGHLIGHTS_COUNT_PRESETS = {
  less: 6,
  standard: 10,
  more: 15,
}

const DENSITY_OPTIONS = [
  { value: 'medium', label: '标准（默认）' },
  { value: 'sparse', label: '疏朗' },
  { value: 'dense', label: '紧凑' },
]

const STYLE_OPTIONS = [
  { value: 'academic', label: '学术海报' },
  { value: 'flat', label: '扁平简约' },
  { value: 'business', label: '商务科技' },
  { value: 'creative', label: '创意设计' },
]

const PODCAST_STYLE_OPTIONS = [
  { value: 'fun', label: '轻松有趣（默认）' },
  { value: 'serious', label: '专业深度' },
  { value: 'summary', label: '简明摘要' },
]

const LANGUAGE_OPTIONS = [
  { value: 'zh', label: '中文（默认）' },
  { value: 'en', label: 'English' },
]

const PPT_MODE_OPTIONS = [
  { value: 'paper', label: '学术模式（默认）' },
  { value: 'general', label: '通用模式' },
]

const PPT_STYLE_OPTIONS = [
  { value: 'academic', label: '学术风格' },
  { value: 'business', label: '商务风格' },
  { value: 'doraemon', label: '哆啦A梦' },
  { value: 'minimal', label: '极简白' },
]

const REPORT_TYPE_OPTIONS = [
  { value: 'deep_report', label: '深度报告（默认）', description: '围绕问题、方法、证据与结论展开深入分析' },
  { value: 'briefing', label: '简报文档', description: '先给结论，再提炼关键事实、影响与行动建议' },
  { value: 'study_guide', label: '学习指南', description: '按阶段拆解学习路径，并配套练习、自测与复盘' },
  { value: 'blog_post', label: '博文', description: '面向读者叙事表达，强调可读性与观点传达' },
]

const LoadingIcon = () => (
  <svg
    className="loading-icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M21 12a9 9 0 1 1-6.2-8.56" />
  </svg>
)

const StudioView = ({
  disabled,
  eurekaId,
  isMaximized,
  onToggleMaximize,
  isChatGenerating,
  isGeneratingFlowchart,
  isGeneratingFlashcards,
  isGeneratingQuiz,
  isGeneratingMindmap,
  isGeneratingHighlights,
  isGeneratingReport,
  isPreparingInterview,
  isResumingInterview,
  isGeneratingPpt,
  isGeneratingPoster,
  isPodcastGenerating,
  hasFlashcards,
  hasQuiz,
  hasMindmap,
  hasHighlights,
  hasReport,
  flowchartHistory,
  flashcardsHistory,
  quizHistory,
  mindmapHistory,
  highlightsHistory,
  reportHistory,
  interviewHistory,
  pptHistory,
  posterHistory,
  podcastHistory,
  notes,
  openMenuId,
  setOpenMenuId,
  menuPosition,
  setMenuPosition,
  renamingItem,
  newName,
  setNewName,
  isDeletingItem,
  loadingNotes,
  loadingHistory,
  formatDateTime,
  startRename,
  confirmRename,
  cancelRename,
  handleDeleteHistory,
  loadNoteVersion,
  loadFlowchartVersion,
  loadFlashcardsVersion,
  loadQuizVersion,
  loadMindmapHistory,
  loadHighlightsHistory,
  loadReportVersion,
  loadInterviewDetail,
  loadPptVersion,
  loadPosterVersion,
  loadPodcastHistory,
  onViewChange,
  onGenerateFlashcards,
  onGenerateQuiz,
  onGenerateMindmap,
  onGenerateHighlights,
  onGenerateReport,
  handleStartInterview,
  onSetChatInput,
  setSelectedNoteId,
  setTempNote,
  setSelectedMindmapId,
  setTempMindmap,
  setSelectedHighlightsId,
  setTempHighlights,
  setTempPoster,
  setPosterId,
  setActivePodcastTaskId,
  setEditingNoteId,
  setNoteTitle,
  setNoteContent,
  setShowNoteDialog,
  pptModalOpen,
  setPptModalOpen,
  setPptTaskId,
  documentId,
  posterModalOpen,
  setPosterModalOpen,
  setPosterTaskId,
  setErrorDialogState,
  showPodcastModal,
  setShowPodcastModal,
  activePodcastTaskId,
  setIsPodcastGenerating
}) => {
  const [showFlashcardCustomModal, setShowFlashcardCustomModal] = useState(false)
  const [flashcardCountPreset, setFlashcardCountPreset] = useState('standard')
  const [flashcardDifficulty, setFlashcardDifficulty] = useState('medium')
  const [flashcardRequirements, setFlashcardRequirements] = useState('')
  const [showQuizCustomModal, setShowQuizCustomModal] = useState(false)
  const [quizCountPreset, setQuizCountPreset] = useState('standard')
  const [quizDifficulty, setQuizDifficulty] = useState('medium')
  const [quizRequirements, setQuizRequirements] = useState('')
  const [showHighlightsCustomModal, setShowHighlightsCustomModal] = useState(false)
  const [highlightsCountPreset, setHighlightsCountPreset] = useState('standard')
  const [highlightsDifficulty, setHighlightsDifficulty] = useState('medium')
  const [highlightsRequirements, setHighlightsRequirements] = useState('')
  const [showReportCustomModal, setShowReportCustomModal] = useState(false)
  const [reportType, setReportType] = useState('deep_report')
  const [reportCustomStyleDesc, setReportCustomStyleDesc] = useState('')
  const [reportRequirements, setReportRequirements] = useState('')
  const [posterDensity, setPosterDensity] = useState('medium')
  const [posterStyle, setPosterStyle] = useState('academic')
  const [posterCustomStyleDesc, setPosterCustomStyleDesc] = useState('')
  const [podcastStyle, setPodcastStyle] = useState('fun')
  const [podcastLanguage, setPodcastLanguage] = useState('zh')
  const [pptMode, setPptMode] = useState('paper')
  const [pptStyle, setPptStyle] = useState('academic')
  const [pptCustomStyleDesc, setPptCustomStyleDesc] = useState('')
  const [isPosterSubmitting, setIsPosterSubmitting] = useState(false)
  const [isPodcastSubmitting, setIsPodcastSubmitting] = useState(false)
  const [isPptSubmitting, setIsPptSubmitting] = useState(false)

  // 缺 Key / 缺可选依赖的功能：入口置灰并在 title 中说明原因（订阅能力清单，加载后自动更新）
  const podcastCapability = useCapability('podcast')
  const posterCapability = useCapability('poster')
  const pptCapability = useCapability('ppt')

  const isAnyGenerating = isGeneratingFlowchart || isGeneratingFlashcards || isGeneratingQuiz || isGeneratingMindmap || isGeneratingHighlights || isGeneratingReport || isPreparingInterview || isResumingInterview || isGeneratingPpt || isGeneratingPoster || isChatGenerating || isPodcastGenerating
  const isCardsDisabled = disabled || isAnyGenerating

  const showTaskErrorDialog = (message, fallback = '任务提交失败，请重试') => {
    setErrorDialogState && setErrorDialogState({
      isOpen: true,
      message: normalizeTaskErrorMessage(message, fallback),
    })
  }

  const handleGenerateFlashcardsDefault = () => {
    onGenerateFlashcards && onGenerateFlashcards()
  }

  const handleGenerateFlashcardsCustom = () => {
    const payload = {
      card_count: FLASHCARD_COUNT_PRESETS[flashcardCountPreset] || FLASHCARD_COUNT_PRESETS.standard,
      difficulty: flashcardDifficulty,
      requirements: flashcardRequirements.trim() || null,
    }
    onGenerateFlashcards && onGenerateFlashcards(payload)
    setShowFlashcardCustomModal(false)
  }

  const handleGenerateQuizDefault = () => {
    onGenerateQuiz && onGenerateQuiz()
  }

  const handleGenerateQuizCustom = () => {
    const payload = {
      question_count: QUIZ_COUNT_PRESETS[quizCountPreset] || QUIZ_COUNT_PRESETS.standard,
      difficulty: quizDifficulty,
      requirements: quizRequirements.trim() || null,
    }
    onGenerateQuiz && onGenerateQuiz(payload)
    setShowQuizCustomModal(false)
  }

  const handleGenerateHighlightsDefault = () => {
    onGenerateHighlights && onGenerateHighlights()
  }

  const handleGenerateHighlightsCustom = () => {
    const payload = {
      point_count: HIGHLIGHTS_COUNT_PRESETS[highlightsCountPreset] || HIGHLIGHTS_COUNT_PRESETS.standard,
      difficulty: highlightsDifficulty,
      requirements: highlightsRequirements.trim() || null,
    }
    onGenerateHighlights && onGenerateHighlights(payload)
    setShowHighlightsCustomModal(false)
  }

  const handleGenerateReportDefault = () => {
    onGenerateReport && onGenerateReport()
  }

  const handleGenerateReportCustom = () => {
    const customStyle = reportCustomStyleDesc.trim()
    const focusPoints = reportRequirements.trim()

    onGenerateReport && onGenerateReport({
      report_type: reportType,
      custom_style: customStyle || null,
      focus_points: focusPoints || null,
    })
    setShowReportCustomModal(false)
  }

  const handleGeneratePosterDefault = async () => {
    if (isCardsDisabled || isGeneratingPoster || !documentId || !posterCapability.available) {
      return
    }

    try {
      const formData = new FormData()
      formData.append('document_id', String(documentId))
      formData.append('title', '海报')
      formData.append('style', 'academic')
      formData.append('density', 'medium')

      const response = await apiRequest('/api/poster/generate', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '海报任务提交失败'))
      }
      const data = await response.json()

      const taskId = data.id || data.task_id
      if (!taskId) {
        throw new Error('海报任务创建失败')
      }

      setPosterTaskId(taskId)
    } catch (error) {
      showTaskErrorDialog(error.message, '海报任务提交失败，请重试')
    }
  }

  const handleGeneratePosterCustom = async () => {
    if (!documentId || isPosterSubmitting) {
      return
    }

    try {
      setIsPosterSubmitting(true)
      const formData = new FormData()
      formData.append('document_id', String(documentId))
      formData.append('title', '海报')
      formData.append('density', posterDensity)
      formData.append('style', posterCustomStyleDesc.trim() || posterStyle)

      const response = await apiRequest('/api/poster/generate', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '海报任务提交失败'))
      }
      const data = await response.json()

      const taskId = data.id || data.task_id
      if (!taskId) {
        throw new Error('海报任务创建失败')
      }

      setPosterTaskId(taskId)
      setPosterModalOpen(false)
    } catch (error) {
      showTaskErrorDialog(error.message, '海报任务提交失败，请重试')
    } finally {
      setIsPosterSubmitting(false)
    }
  }

  const handleGeneratePodcastDefault = async () => {
    if (isCardsDisabled || isPodcastGenerating || !documentId || !podcastCapability.available) {
      return
    }

    try {
      const response = await apiRequest('/api/podcast/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          document_id: documentId,
          title: '播客',
          style: 'fun',
          language: 'zh',
        }),
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '播客任务提交失败'))
      }
      const data = await response.json()

      const taskId = data.task_id || data.id
      if (!taskId) {
        throw new Error('播客任务创建失败')
      }

      setActivePodcastTaskId(taskId)
      setIsPodcastGenerating(true)
    } catch (error) {
      showTaskErrorDialog(error.message, '播客任务提交失败，请重试')
    }
  }

  const handleGeneratePodcastCustom = async () => {
    if (!documentId || isPodcastSubmitting) {
      return
    }

    try {
      setIsPodcastSubmitting(true)
      const response = await apiRequest('/api/podcast/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          document_id: documentId,
          title: '播客',
          style: podcastStyle,
          language: podcastLanguage,
        }),
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, '播客任务提交失败'))
      }
      const data = await response.json()

      const taskId = data.task_id || data.id
      if (!taskId) {
        throw new Error('播客任务创建失败')
      }

      setActivePodcastTaskId(taskId)
      setIsPodcastGenerating(true)
      setShowPodcastModal(false)
    } catch (error) {
      showTaskErrorDialog(error.message, '播客任务提交失败，请重试')
    } finally {
      setIsPodcastSubmitting(false)
    }
  }

  const handleGeneratePptDefault = async () => {
    if (isCardsDisabled || isGeneratingPpt || !documentId || !import.meta.env.DEV || !pptCapability.available) {
      return
    }

    try {
      const formData = new FormData()
      formData.append('document_id', String(documentId))
      formData.append('title', '演示文稿')
      formData.append('style', 'academic')
      formData.append('language', 'zh')
      formData.append('mode', 'paper')
      formData.append('output_type', 'slides')

      const response = await apiRequest('/api/ppt/generate', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, 'PPT 任务提交失败'))
      }
      const data = await response.json()

      const taskId = data.task_id || data.id
      if (!taskId) {
        throw new Error('PPT 任务创建失败')
      }

      setPptTaskId(taskId)
    } catch (error) {
      showTaskErrorDialog(error.message, 'PPT 任务提交失败，请重试')
    }
  }

  const handleGeneratePptCustom = async () => {
    if (!documentId || isPptSubmitting || !import.meta.env.DEV) {
      return
    }

    try {
      setIsPptSubmitting(true)
      const formData = new FormData()
      formData.append('document_id', String(documentId))
      formData.append('title', '演示文稿')
      formData.append('style', pptCustomStyleDesc.trim() || pptStyle)
      formData.append('language', 'zh')
      formData.append('mode', pptMode)
      formData.append('output_type', 'slides')

      const response = await apiRequest('/api/ppt/generate', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        throw new Error(await parseTaskErrorResponse(response, 'PPT 任务提交失败'))
      }
      const data = await response.json()

      const taskId = data.task_id || data.id
      if (!taskId) {
        throw new Error('PPT 任务创建失败')
      }

      setPptTaskId(taskId)
      setPptModalOpen(false)
    } catch (error) {
      showTaskErrorDialog(error.message, 'PPT 任务提交失败，请重试')
    } finally {
      setIsPptSubmitting(false)
    }
  }

  const allHistory = [
    ...flowchartHistory.map(item => ({
      ...item,
      type: 'flowchart',
      icon: '🗺️',
      title: '学习路径',
      typeLabel: '学习路径',
      subtitle: `${item.node_count} 个节点`,
    })),
    ...flashcardsHistory.map(item => ({
      ...item,
      type: 'flashcards',
      icon: '📇',
      title: item.title || '闪卡',
      typeLabel: '闪卡',
      subtitle: `${item.card_count} 张卡片`,
    })),
    ...quizHistory.map(item => ({
      ...item,
      type: 'quiz',
      icon: '📝',
      title: item.title || '测验',
      typeLabel: '测验',
      subtitle: `${item.question_count} 道题目`,
    })),
    ...mindmapHistory.map(item => ({
      ...item,
      type: 'mindmap',
      icon: '🗺️',
      title: item.title || '思维导图',
      typeLabel: '思维导图',
      subtitle: `${item.node_count} 个节点`,
    })),
    ...highlightsHistory.map(item => ({
      ...item,
      type: 'highlights',
      icon: '💡',
      title: item.title || '重点提炼',
      typeLabel: '重点提炼',
      subtitle: `${item.point_count} 个要点`,
    })),
    ...reportHistory.map(item => ({
      ...item,
      type: 'report',
      icon: '📄',
      title: item.title || '长报告',
      typeLabel: '长报告',
      subtitle: `${item.word_count || 0} 字`,
    })),
    ...interviewHistory.map(item => ({
      ...item,
      type: 'interview',
      icon: '🎤',
      title: '面试记录',
      typeLabel: '面试记录',
      subtitle: `${item.total_rounds} 轮 · 平均 ${item.average_score.toFixed(0)} 分`,
    })),
    ...pptHistory
      .filter(item => item.status === 'completed')
      .map(item => {
        let count = 0
        try {
          count = item.preview_images ? (Array.isArray(item.preview_images) ? item.preview_images.length : JSON.parse(item.preview_images).length) : 0
        } catch (e) { void e }
        return {
          ...item,
          type: 'ppt',
          icon: '📊',
          title: item.title || '演示文稿',
          typeLabel: '演示文稿',
          subtitle: `${count} 张幻灯片`,
        }
      }),
    ...posterHistory
      .filter(item => item.status === 'completed')
      .map(item => {
        let displayTitle = item.title || '海报'
        if (displayTitle.startsWith('Poster - ') || displayTitle.startsWith('Poster -')) {
          displayTitle = '海报'
        }
        return {
          ...item,
          type: 'poster',
          icon: '🪧',
          title: displayTitle,
          typeLabel: '海报',
          subtitle: '单页海报',
        }
      }),
    ...podcastHistory
      .filter(item => item.status === 'completed')
      .map(item => ({
        ...item,
        type: 'podcast',
        icon: '🎙️',
        title: item.title || 'AI Podcast',
        typeLabel: '播客',
        subtitle: `${item.style === 'fun' ? '轻松有趣' : (item.style === 'serious' ? '专业深度' : '简明摘要')} · ${item.language === 'zh' ? '中文' : 'English'}`,
      })),
    ...notes
      .filter(item => !['learning_path', 'flashcards', 'quiz', 'mindmap', 'highlights', 'interview'].includes(item.type))
      .map(item => {
        const typeConfig = {
          ai_response: { icon: '💬', typeLabel: 'AI 回复' },
          manual: { icon: '✍️', typeLabel: '手动笔记' },
          general: { icon: '📝', typeLabel: '笔记' },
          text_polish: { icon: '🪄', typeLabel: 'AI 回复' }
        }

        const config = typeConfig[item.type] || { icon: '📝', typeLabel: '笔记' }
        const previewText = item.content ? item.content.replace(/\s+/g, ' ').slice(0, 60) : ''
        const isShared = item.is_shared === true || item.is_shared === 1 || item.is_shared === 'true' || item.is_shared === '1'

        return {
          ...item,
          type: 'note',
          originalType: item.type,
          icon: config.icon,
          typeLabel: config.typeLabel,
          title: item.title,
          subtitle: previewText,
          _isShared: isShared
        }
      })
  ].sort((a, b) => {
    const timeA = a.created_at || a.updated_at
    const timeB = b.created_at || b.updated_at
    const valueA = timeA ? new Date(timeA).getTime() : 0
    const valueB = timeB ? new Date(timeB).getTime() : 0
    return valueB - valueA
  })

  const historyEntries = allHistory

  const handleHistoryClick = (item) => {
    const itemKey = `${item.type}-${item.id}`
    if (isDeletingItem === itemKey) {
      return
    }
    if (renamingItem && renamingItem.type === item.type && renamingItem.id === item.id) {
      return
    }
    setOpenMenuId(null)
    switch (item.type) {
      case 'note':
        setSelectedNoteId(item.id)
        setTempNote({ ...item })
        loadNoteVersion(item.id)
        onViewChange('note')
        break
      case 'flowchart':
        loadFlowchartVersion(item.id)
        onViewChange('flowchart')
        break
      case 'flashcards':
        loadFlashcardsVersion(item.id)
        onViewChange('flashcards')
        break
      case 'quiz':
        loadQuizVersion(item.id)
        onViewChange('quiz')
        break
      case 'mindmap':
        setSelectedMindmapId(item.id)
        if (item.mindmap_data) {
          setTempMindmap(item.mindmap_data)
        } else {
          setTempMindmap(null)
          loadMindmapHistory({ withLoading: false })
        }
        onViewChange('mindmap')
        break
      case 'highlights':
        setSelectedHighlightsId(item.id)
        if (item.highlights_data) {
          setTempHighlights(item.highlights_data)
        } else {
          setTempHighlights(null)
          loadHighlightsHistory({ withLoading: false })
        }
        onViewChange('highlights')
        break
      case 'interview':
        loadInterviewDetail(item.id)
        loadInterviewDetail(item.id)
        onViewChange('interview-detail')
        break
      case 'report':
        loadReportVersion(item.id)
        onViewChange('report')
        break
      case 'ppt':
        loadPptVersion(item.id)
        onViewChange('ppt')
        break
      case 'poster':
        if (item.poster_url) {
          setTempPoster({
            id: item.id,
            title: item.title || '海报',
            poster_url: item.poster_url,
            preview_url: item.preview_url || item.poster_url
          })
          setPosterId(item.id)
          onViewChange('poster')
        } else {
          loadPosterVersion(item.id)
          onViewChange('poster')
        }
        break
      case 'podcast':
        setActivePodcastTaskId(item.id)
        break
      default:
        break
    }
  }

  const handleMenuOpen = (event, item) => {
    event.stopPropagation()
    const rect = event.currentTarget.getBoundingClientRect()
    const dropdownWidth = 180
    const left = Math.min(rect.left, window.innerWidth - dropdownWidth - 12)
    setMenuPosition({
      top: rect.bottom + 6,
      left
    })
    const menuId = `${item.type}-${item.id}`
    setOpenMenuId(prev => (prev === menuId ? null : menuId))
  }

  const renderHistoryMenu = () => {
    if (!openMenuId) {
      return null
    }

    const targetEntry = historyEntries.find(entry => `${entry.type}-${entry.id}` === openMenuId)
    if (!targetEntry) {
      return null
    }

    const defaultTitles = {
      note: '未命名笔记',
      flowchart: '学习路径',
      flashcards: '闪卡',
      quiz: '测验',
      mindmap: '思维导图',
      highlights: '重点提炼',
      report: '长报告',
      interview: '面试记录'
    }
    const targetTitle = targetEntry.title || defaultTitles[targetEntry.type] || '学习笔记'

    return (
      <div
        className="history-menu-dropdown"
        style={{ top: `${menuPosition.top}px`, left: `${menuPosition.left}px` }}
      >
        <button
          className="menu-item"
          onClick={(e) => {
            e.stopPropagation()
            startRename(targetEntry.type, targetEntry.id, targetTitle)
          }}
        >
          ✏️ 重命名
        </button>

        {(targetEntry.type !== 'ppt' && targetEntry.type !== 'poster') && (
          <button
            className="menu-item delete"
            onClick={(e) => {
              e.stopPropagation()
              handleDeleteHistory(targetEntry.type, targetEntry.id)
              setOpenMenuId(null)
            }}
          >
            🗑️ 删除
          </button>
        )}
      </div>
    )
  }

  const renderMyNotes = () => {
    if ((loadingNotes || loadingHistory) && historyEntries.length === 0) {
      return (
        <div className="studio-empty-state">
          <SkeletonNotesList count={2} />
        </div>
      )
    }

    if (!loadingNotes && !loadingHistory && historyEntries.length === 0) {
      return (
        <div className="studio-empty-state">
          <p>{eurekaId
            ? '暂无记录，你可以直接将左侧对话中的内容保存到笔记，或在工作室中手动创建。'
            : '暂无记录，点击上方"工作室"的功能卡片创建一条吧，你也可以直接将左侧对话中的内容保存到笔记。'
          }</p>
        </div>
      )
    }

    return (
      <>
        <div className="studio-history-list">
          {historyEntries.map((item) => {
            const itemKey = `${item.type}-${item.id}`
            const isMenuOpen = openMenuId === itemKey
            const isRenaming = renamingItem && renamingItem.type === item.type && renamingItem.id === item.id
            const isDeleting = isDeletingItem === itemKey
            const defaultTitles = {
              note: '未命名笔记',
              flowchart: '学习路径',
              flashcards: '闪卡',
              quiz: '测验',
              mindmap: '思维导图',
              highlights: '重点提炼',
              report: '长报告',
              interview: '面试记录',
              ppt: '演示文稿',
              poster: '海报'
            }
            const displayTitle = isRenaming ? newName : (item.title || defaultTitles[item.type] || '学习笔记')
            const timeText = formatDateTime(item.updated_at || item.created_at)

            const handleSendToInput = (e) => {
              e.stopPropagation()
              if (onSetChatInput) {
                let contentToFill = ''
                if (item.type === 'note') {
                  // 笔记内容可能含 HTML：用 DOMParser 在惰性文档中解析（不会加载图片、执行事件处理器），只取纯文本
                  const parsed = new DOMParser().parseFromString(item.content || '', 'text/html')
                  contentToFill = parsed.body.textContent || ''
                } else {
                  return
                }

                onSetChatInput({
                  title: item.title || '学习笔记',
                  content: contentToFill
                }, 'reference')
              }
            }

            return (
              <div
                key={itemKey}
                className={`studio-history-item ${item.type === 'note' && item._isShared ? 'shared-note' : ''} ${isDeleting ? 'deleting' : ''}`}
                onClick={() => handleHistoryClick(item)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    handleHistoryClick(item)
                  }
                }}
              >
                <div className="history-item-icon">{item.icon}</div>
                <div className="history-item-content">
                  <div className="history-item-title-row">
                    {isRenaming ? (
                      <div className="history-rename-row" onClick={(e) => e.stopPropagation()}>
                        <input
                          className="rename-input"
                          value={newName}
                          onChange={(e) => setNewName(e.target.value)}
                          onBlur={confirmRename}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault()
                              confirmRename()
                            } else if (e.key === 'Escape') {
                              e.preventDefault()
                              cancelRename()
                            }
                          }}
                          autoFocus
                        />
                      </div>
                    ) : (
                      <span className="history-item-title">{displayTitle}</span>
                    )}
                  </div>
                  <div className="history-item-meta-row">
                    {timeText && <span className="history-item-time">{timeText}</span>}
                  </div>
                </div>
                <div className="history-item-menu-container">
                  {onSetChatInput && item.type === 'note' && (
                    <button
                      className="history-item-action-btn"
                      onClick={handleSendToInput}
                      title="引用到对话"
                      style={{
                        background: 'transparent',
                        border: 'none',
                        cursor: 'pointer',
                        color: '#94a3b8',
                        padding: '4px',
                        marginRight: '4px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        borderRadius: '4px',
                      }}
                      onMouseOver={(e) => e.currentTarget.style.color = '#3b82f6'}
                      onMouseOut={(e) => e.currentTarget.style.color = '#94a3b8'}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M9 15L3 9m0 0l6-6M3 9h12a6 6 0 0 1 0 12h-3" />
                      </svg>
                    </button>
                  )}
                  <button
                    className="history-item-menu-btn"
                    onClick={(event) => handleMenuOpen(event, item)}
                    aria-expanded={isMenuOpen}
                    aria-haspopup="true"
                  >                    ⋮
                  </button>
                </div>
              </div>
            )
          })}
        </div>
        {renderHistoryMenu()}
      </>
    )
  }

  return (
    <div className="studio-view">
      {!eurekaId && (
        <>
          <div className="actions-header">
            <h3>工作室</h3>
            {onToggleMaximize && (
              <button
                className="panel-embedded-maximize-btn"
                onClick={onToggleMaximize}
                title={isMaximized ? '还原' : '最大化'}
              >
                {isMaximized ? (
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3" />
                  </svg>
                ) : (
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                  </svg>
                )}
              </button>
            )}
          </div>
          <div className="actions-content">

            <Motion.button
              className={`action-card note-action ${disabled ? 'disabled-card' : ''}`}
              onClick={() => {
                setEditingNoteId(null)
                setNoteTitle('')
                setNoteContent('')
                setShowNoteDialog(true)
              }}
              disabled={isCardsDisabled}
              title={disabled ? '灵感模式不可用' : '手动创建学习笔记'}
            >
              <div className="action-icon">✍️</div>
              <div className="action-text">
                <h4>做笔记</h4>
              </div>
            </Motion.button>

            <div className="action-card-with-edit">
              <Motion.button
                className={`action-card flashcards-action has-edit ${isGeneratingFlashcards ? 'generating' : ''} ${hasFlashcards ? 'generated' : ''} ${disabled ? 'disabled-card' : ''}`}
                onClick={handleGenerateFlashcardsDefault}
                disabled={isCardsDisabled}
                title={disabled ? '灵感对话模式下不可用' : ''}
              >
                <div className="action-icon">
                  {isGeneratingFlashcards ? <LoadingIcon /> : '📇'}
                </div>
                <div className="action-text">
                  <h4>闪卡</h4>
                </div>
              </Motion.button>
              <button
                type="button"
                className="flashcards-edit-trigger"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (!isCardsDisabled) {
                    setShowFlashcardCustomModal(true)
                  }
                }}
                disabled={isCardsDisabled}
                title={disabled ? '灵感模式不可用' : '自定义生成闪卡'}
              >
                ✎
              </button>
            </div>

            <div className="action-card-with-edit">
              <Motion.button
                className={`action-card quiz-action has-edit ${isGeneratingQuiz ? 'generating' : ''} ${hasQuiz ? 'generated' : ''} ${disabled ? 'disabled-card' : ''}`}
                onClick={handleGenerateQuizDefault}
                disabled={isCardsDisabled}
                title={disabled ? '灵感对话模式下不可用' : ''}
              >
                <div className="action-icon">
                  {isGeneratingQuiz ? <LoadingIcon /> : '📝'}
                </div>
                <div className="action-text">
                  <h4>测验</h4>
                </div>
              </Motion.button>
              <button
                type="button"
                className="flashcards-edit-trigger"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (!isCardsDisabled) {
                    setShowQuizCustomModal(true)
                  }
                }}
                disabled={isCardsDisabled}
                title={disabled ? '灵感模式不可用' : '自定义生成测验'}
              >
                ✎
              </button>
            </div>

            <Motion.button
              className={`action-card mindmap-action ${isGeneratingMindmap ? 'generating' : ''} ${hasMindmap ? 'generated' : ''} ${disabled ? 'disabled-card' : ''}`}
              onClick={() => {
                onGenerateMindmap && onGenerateMindmap()
              }}
              disabled={isCardsDisabled}
              title={disabled ? '灵感对话模式下不可用' : ''}
            >
              <div className="action-icon">
                {isGeneratingMindmap ? <LoadingIcon /> : '🗺️'}
              </div>
              <div className="action-text">
                <h4>思维导图</h4>
              </div>
            </Motion.button>

            <div className="action-card-with-edit">
              <Motion.button
                className={`action-card highlights-action has-edit ${isGeneratingHighlights ? 'generating' : ''} ${hasHighlights ? 'generated' : ''} ${disabled ? 'disabled-card' : ''}`}
                onClick={handleGenerateHighlightsDefault}
                disabled={isCardsDisabled}
                title={disabled ? '灵感对话模式下不可用' : ''}
              >
                <div className="action-icon">
                  {isGeneratingHighlights ? <LoadingIcon /> : '💡'}
                </div>
                <div className="action-text">
                  <h4>重点提炼</h4>
                </div>
              </Motion.button>
              <button
                type="button"
                className="flashcards-edit-trigger"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (!isCardsDisabled) {
                    setShowHighlightsCustomModal(true)
                  }
                }}
                disabled={isCardsDisabled}
                title={disabled ? '灵感模式不可用' : '自定义重点提炼'}
              >
                ✎
              </button>
            </div>

            <div className="action-card-with-edit">
              <Motion.button
                className={`action-card report-action has-edit ${isGeneratingReport ? 'generating' : ''} ${hasReport ? 'generated' : ''} ${disabled ? 'disabled-card' : ''}`}
                onClick={handleGenerateReportDefault}
                disabled={isCardsDisabled}
                title={disabled ? '灵感对话模式下不可用' : ''}
              >
                <div className="action-icon">
                  {isGeneratingReport ? <LoadingIcon /> : '📄'}
                </div>
                <div className="action-text">
                  <h4>报告</h4>
                </div>
              </Motion.button>
              <button
                type="button"
                className="flashcards-edit-trigger"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (!isCardsDisabled) {
                    setShowReportCustomModal(true)
                  }
                }}
                disabled={isCardsDisabled}
                title={disabled ? '灵感模式不可用' : '自定义报告生成'}
              >
                ✎
              </button>
            </div>

            <Motion.button
              className={`action-card interview-action ${isPreparingInterview ? 'generating' : ''} ${disabled ? 'disabled-card' : ''}`}
              onClick={handleStartInterview}
              disabled={isCardsDisabled}
              title={disabled ? '灵感对话模式下不可用' : ''}
            >
              <div className="action-icon">🎤</div>
              <div className="action-text">
                <h4>{isPreparingInterview ? '准备中...' : '面试'}</h4>
              </div>
            </Motion.button>

            <div className="action-card-with-edit">
              <Motion.button
                className={`action-card podcast-action has-edit ${isPodcastGenerating ? 'generating' : ''} ${(disabled || !podcastCapability.available) ? 'disabled-card' : ''}`}
                onClick={handleGeneratePodcastDefault}
                disabled={isCardsDisabled || !podcastCapability.available}
                title={!podcastCapability.available ? podcastCapability.reason?.text : (disabled ? '灵感对话模式下不可用' : '默认配置生成播客')}
              >
                <div className="action-icon">
                  {isPodcastGenerating ? <LoadingIcon /> : '🎙️'}
                </div>
                <div className="action-text">
                  <h4>播客</h4>
                </div>
              </Motion.button>
              <button
                type="button"
                className="flashcards-edit-trigger"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (!isCardsDisabled && podcastCapability.available) {
                    setShowPodcastModal(true)
                  }
                }}
                disabled={isCardsDisabled || !podcastCapability.available}
                title={!podcastCapability.available ? podcastCapability.reason?.text : (disabled ? '灵感模式不可用' : '自定义播客生成')}
              >
                ✎
              </button>
            </div>

            <div className="action-card-with-edit">
              <Motion.button
                className={`action-card poster-action has-edit ${isGeneratingPoster ? 'generating' : ''} ${(disabled || !posterCapability.available) ? 'disabled-card' : ''}`}
                onClick={handleGeneratePosterDefault}
                disabled={isCardsDisabled || !posterCapability.available}
                title={!posterCapability.available ? posterCapability.reason?.text : (disabled ? '灵感模式不可用' : '默认配置生成海报')}
              >
                <div className="action-icon">
                  {isGeneratingPoster ? <LoadingIcon /> : '🪧'}
                </div>
                <div className="action-text">
                  <h4>海报</h4>
                </div>
              </Motion.button>
              <button
                type="button"
                className="flashcards-edit-trigger"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (!isCardsDisabled && posterCapability.available) {
                    setPosterModalOpen(true)
                  }
                }}
                disabled={isCardsDisabled || !posterCapability.available}
                title={!posterCapability.available ? posterCapability.reason?.text : (disabled ? '灵感模式不可用' : '自定义海报生成')}
              >
                ✎
              </button>
            </div>

            <div className="action-card-with-edit">
              <Motion.button
                className={`action-card ppt-action has-edit ${isGeneratingPpt ? 'generating' : ''} ${(disabled || !import.meta.env.DEV || !pptCapability.available) ? 'disabled-card' : ''}`}
                onClick={handleGeneratePptDefault}
                disabled={isCardsDisabled || !import.meta.env.DEV || !pptCapability.available}
                title={!import.meta.env.DEV ? '即将推出' : (!pptCapability.available ? pptCapability.reason?.text : (disabled ? '灵感模式不可用' : '默认配置生成PPT'))}
              >
                <div className="action-icon">
                  {isGeneratingPpt ? <LoadingIcon /> : '📊'}
                </div>
                <div className="action-text">
                  <h4>演示文稿</h4>
                </div>
              </Motion.button>
              <button
                type="button"
                className="flashcards-edit-trigger"
                onClick={(e) => {
                  e.preventDefault()
                  e.stopPropagation()
                  if (!isCardsDisabled && import.meta.env.DEV && pptCapability.available) {
                    setPptModalOpen(true)
                  }
                }}
                disabled={isCardsDisabled || !import.meta.env.DEV || !pptCapability.available}
                title={!import.meta.env.DEV ? '即将推出' : (!pptCapability.available ? pptCapability.reason?.text : (disabled ? '灵感模式不可用' : '自定义PPT生成'))}
              >
                ✎
              </button>
            </div>

          </div>
        </>
      )}

      <div className="studio-history" style={eurekaId ? { position: 'absolute', top: '24px', left: '24px', right: '50px', padding: 0, margin: 0 } : {}}>
        <div className="studio-history-header" style={eurekaId ? { justifyContent: 'flex-start', gap: '12px', marginBottom: '12px', marginTop: 0 } : {}}>
          <h4 style={eurekaId ? { flex: 'none', margin: 0 } : {}}>我的记录</h4>
          {onToggleMaximize && eurekaId && (
            <button
              className="panel-embedded-maximize-btn"
              onClick={onToggleMaximize}
              title={isMaximized ? '还原' : '最大化'}
              style={{ marginLeft: 'auto' }}
            >
              {isMaximized ? (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3" />
                </svg>
              ) : (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                </svg>
              )}
            </button>
          )}
          {eurekaId && (
            <button
              className="create-note-btn"
              onClick={() => {
                setEditingNoteId(null)
                setNoteTitle('')
                setNoteContent('')
                setShowNoteDialog(true)
              }}
              title="手动创建学习笔记"
            >
              做笔记
            </button>
          )}

        </div>

        {renderMyNotes()}
      </div>
      <PodcastPlayer
        taskId={activePodcastTaskId}
        onClose={() => {
          setActivePodcastTaskId(null)
          setIsPodcastGenerating(false)
        }}
        onStatusChange={(status) => {
          if (status === 'completed') {
            if (isPodcastGenerating) {
              setIsPodcastGenerating(false)
              setActivePodcastTaskId(null)
              loadPodcastHistory()
            }
          }
          if (status === 'failed') {
            setIsPodcastGenerating(false)
          }
        }}
      />

      {showFlashcardCustomModal && (
        <GenerationConfigModal
          open={showFlashcardCustomModal}
          title="自定义闪卡"
          onClose={() => setShowFlashcardCustomModal(false)}
          onSubmit={handleGenerateFlashcardsCustom}
          groups={[
            {
              key: 'flashcards-count',
              label: '卡片数量',
              value: flashcardCountPreset,
              onChange: setFlashcardCountPreset,
              options: [
                { value: 'less', label: '更少' },
                { value: 'standard', label: '标准（默认）' },
                { value: 'more', label: '更多' },
              ],
            },
            {
              key: 'flashcards-difficulty',
              label: '难度等级',
              value: flashcardDifficulty,
              onChange: setFlashcardDifficulty,
              options: [
                { value: 'easy', label: '简单' },
                { value: 'medium', label: '中等（默认）' },
                { value: 'hard', label: '困难' },
              ],
            },
          ]}
          textFields={[
            {
              key: 'flashcards-requirements',
              label: '你希望闪卡重点覆盖什么？（可选）',
              placeholder: '例如：只关注牛顿第二定律；优先定义+公式+典型例题；正面尽量短语化（1-5词）',
              value: flashcardRequirements,
              onChange: setFlashcardRequirements,
              multiline: true,
            },
          ]}
        />
      )}

      {showQuizCustomModal && (
        <GenerationConfigModal
          open={showQuizCustomModal}
          title="自定义测验"
          onClose={() => setShowQuizCustomModal(false)}
          onSubmit={handleGenerateQuizCustom}
          groups={[
            {
              key: 'quiz-count',
              label: '问题数量',
              value: quizCountPreset,
              onChange: setQuizCountPreset,
              options: [
                { value: 'less', label: '更少' },
                { value: 'standard', label: '标准（默认）' },
                { value: 'more', label: '更多' },
              ],
            },
            {
              key: 'quiz-difficulty',
              label: '难度等级',
              value: quizDifficulty,
              onChange: setQuizDifficulty,
              options: [
                { value: 'easy', label: '简单' },
                { value: 'medium', label: '中等（默认）' },
                { value: 'hard', label: '困难' },
              ],
            },
          ]}
          textFields={[
            {
              key: 'quiz-requirements',
              label: '你希望测验重点覆盖什么？（可选）',
              placeholder: '例如：只考核心概念；每题都要结合文中事实；避免常识题和纯记忆题',
              value: quizRequirements,
              onChange: setQuizRequirements,
              multiline: true,
            },
          ]}
        />
      )}

      {showHighlightsCustomModal && (
        <GenerationConfigModal
          open={showHighlightsCustomModal}
          title="自定义重点提炼"
          onClose={() => setShowHighlightsCustomModal(false)}
          onSubmit={handleGenerateHighlightsCustom}
          groups={[
            {
              key: 'highlights-count',
              label: '要点数量',
              value: highlightsCountPreset,
              onChange: setHighlightsCountPreset,
              options: [
                { value: 'less', label: '更少' },
                { value: 'standard', label: '标准（默认）' },
                { value: 'more', label: '更多' },
              ],
            },
            {
              key: 'highlights-difficulty',
              label: '提炼深度',
              value: highlightsDifficulty,
              onChange: setHighlightsDifficulty,
              options: [
                { value: 'easy', label: '基础' },
                { value: 'medium', label: '标准（默认）' },
                { value: 'hard', label: '深入' },
              ],
            },
          ]}
          textFields={[
            {
              key: 'highlights-requirements',
              label: '你希望重点提炼关注什么？（可选）',
              placeholder: '例如：优先提炼方法与结论；保留关键数据与对比结果；只关注可执行建议',
              value: highlightsRequirements,
              onChange: setHighlightsRequirements,
              multiline: true,
            },
          ]}
        />
      )}

      {showReportCustomModal && (
        <GenerationConfigModal
          open={showReportCustomModal}
          title="自定义报告"
          onClose={() => setShowReportCustomModal(false)}
          onSubmit={handleGenerateReportCustom}
          groups={[
            {
              key: 'report-type',
              label: '报告类型',
              value: reportType,
              onChange: setReportType,
              fullWidth: true,
              optionsClassName: 'report-type-options',
              options: REPORT_TYPE_OPTIONS,
            },
          ]}
          textFields={[
            {
              key: 'report-custom-style',
              label: '自定义风格（可选）',
              placeholder: '例如：学术严谨、新闻解读、第一人称叙事、面向产品经理',
              value: reportCustomStyleDesc,
              onChange: setReportCustomStyleDesc,
            },
            {
              key: 'report-requirements',
              label: '你希望报告重点覆盖什么？（可选）',
              placeholder: '例如：重点分析方法与结论；保留关键数据；最后给出可执行建议',
              value: reportRequirements,
              onChange: setReportRequirements,
              multiline: true,
            },
          ]}
        />
      )}

      {posterModalOpen && (
        <GenerationConfigModal
          open={posterModalOpen}
          title="自定义海报"
          onClose={() => setPosterModalOpen(false)}
          onSubmit={handleGeneratePosterCustom}
          isSubmitting={isPosterSubmitting}
          groups={[
            {
              key: 'poster-density',
              label: '内容密度',
              value: posterDensity,
              onChange: setPosterDensity,
              options: DENSITY_OPTIONS,
            },
            {
              key: 'poster-style',
              label: '视觉风格',
              value: posterStyle,
              onChange: setPosterStyle,
              options: STYLE_OPTIONS,
            },
          ]}
          textFields={[
            {
              key: 'poster-custom-style',
              label: '或者输入自定义风格（可选）',
              placeholder: '请输入您想要的风格，如：清新文艺、赛博朋克、复古风...',
              value: posterCustomStyleDesc,
              onChange: setPosterCustomStyleDesc,
            },
          ]}
        />
      )}

      {showPodcastModal && (
        <GenerationConfigModal
          open={showPodcastModal}
          title="自定义播客"
          onClose={() => setShowPodcastModal(false)}
          onSubmit={handleGeneratePodcastCustom}
          isSubmitting={isPodcastSubmitting}
          groups={[
            {
              key: 'podcast-style',
              label: '对话风格',
              value: podcastStyle,
              onChange: setPodcastStyle,
              options: PODCAST_STYLE_OPTIONS,
            },
            {
              key: 'podcast-language',
              label: '语言选择',
              value: podcastLanguage,
              onChange: setPodcastLanguage,
              options: LANGUAGE_OPTIONS,
            },
          ]}
        />
      )}

      {pptModalOpen && (
        <GenerationConfigModal
          open={pptModalOpen}
          title="自定义演示文稿"
          onClose={() => setPptModalOpen(false)}
          onSubmit={handleGeneratePptCustom}
          isSubmitting={isPptSubmitting}
          groups={[
            {
              key: 'ppt-mode',
              label: '内容模式',
              value: pptMode,
              onChange: setPptMode,
              options: PPT_MODE_OPTIONS,
            },
            {
              key: 'ppt-style',
              label: '视觉风格',
              value: pptStyle,
              onChange: setPptStyle,
              options: PPT_STYLE_OPTIONS,
            },
          ]}
          textFields={[
            {
              key: 'ppt-custom-style',
              label: '或者输入自定义风格（可选）',
              placeholder: '请输入您想要的风格，如：科技感、赛博朋克、水彩手绘...',
              value: pptCustomStyleDesc,
              onChange: setPptCustomStyleDesc,
            },
          ]}
        />
      )}
    </div>
  )
}

export default StudioView
