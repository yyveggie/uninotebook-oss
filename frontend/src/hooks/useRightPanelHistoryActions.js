import { getApiErrorMessage } from '../utils/api'
import { useState } from 'react'

export default function useRightPanelHistoryActions({
  state,
  actions,
  loaders,
}) {
  const {
    documentId,
    eurekaId,
    notes,
  } = state

  const {
    apiCall,
    setNotes,
    invalidateHistoryCache,
  } = actions

  const {
    loadNotes,
    loadFlowchartHistory,
    loadFlashcardsHistory,
    loadQuizHistory,
    loadMindmapHistory,
    loadHighlightsHistory,
    loadReportHistory,
    loadPptHistory,
    loadPosterHistory,
    loadPodcastHistory,
    loadInterviewHistory,
  } = loaders

  const [openMenuId, setOpenMenuId] = useState(null)
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 })
  const [renamingItem, setRenamingItem] = useState(null)
  const [newName, setNewName] = useState('')
  const [isDeletingItem, setIsDeletingItem] = useState(null)
  const [deleteConfirmState, setDeleteConfirmState] = useState({
    isOpen: false,
    type: null,
    id: null,
    title: ''
  })

  const executeDeleteHistory = async (type, id) => {
    const itemKey = `${type}-${id}`
    setIsDeletingItem(itemKey)

    try {
      if (type === 'note') {
        const isEurekaMode = !documentId && eurekaId
        const noteIndex = notes.findIndex(note => note.id === id)
        if (noteIndex === -1) return

        const updatedNotes = notes.filter(note => note.id !== id)
        setNotes(updatedNotes)

        try {
          const endpoint = isEurekaMode
            ? `/api/eureka/notes/${id}`
            : `/api/notes/${id}`

          const response = await apiCall(endpoint, {
            method: 'DELETE'
          })

          if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: '未知错误' }))
            throw new Error(getApiErrorMessage(errorData, '删除失败'))
          }

          if (isEurekaMode) {
            loadNotes({ forceRefresh: true })
          } else {
            invalidateHistoryCache()
            Promise.all([
              loadNotes({ forceRefresh: true }),
              loadFlowchartHistory({ withLoading: false, forceRefresh: true }),
              loadFlashcardsHistory({ withLoading: false, forceRefresh: true }),
              loadQuizHistory({ withLoading: false, forceRefresh: true }),
              loadMindmapHistory({ withLoading: false, forceRefresh: true }),
              loadHighlightsHistory({ withLoading: false, forceRefresh: true }),
              loadReportHistory({ withLoading: false, forceRefresh: true }),
              loadPptHistory({ withLoading: false, forceRefresh: true })
            ])
          }
        } catch (error) {
          setNotes(notes)
          alert(error.message || '删除失败，请重试')
        }
      } else if (type === 'ppt') {
        const response = await apiCall(`/api/ppt/task/${id}`, {
          method: 'DELETE'
        })

        if (response.ok) {
          invalidateHistoryCache('ppt')
          await loadPptHistory({ forceRefresh: true })
          setIsDeletingItem(null)
        } else {
          const errorData = await response.json().catch(() => ({ detail: '未知错误' }))
          setIsDeletingItem(null)
          alert(`删除 PPT 失败: ${getApiErrorMessage(errorData, '未知错误')}`)
        }
      } else if (type === 'interview') {
        const response = await apiCall(`/api/interview/${id}`, {
          method: 'DELETE'
        })

        if (response.ok) {
          invalidateHistoryCache('interview')
          await loadInterviewHistory({ forceRefresh: true })
          setIsDeletingItem(null)
        } else {
          const errorData = await response.json().catch(() => ({ detail: '未知错误' }))
          setIsDeletingItem(null)
          alert(`删除面试记录失败: ${getApiErrorMessage(errorData, '未知错误')}`)
          throw new Error('删除面试记录失败')
        }
      } else if (type === 'podcast') {
        const response = await apiCall(`/api/podcast/${id}`, {
          method: 'DELETE'
        })

        if (response.ok) {
          await loadPodcastHistory()
          setIsDeletingItem(null)
        } else {
          const errorData = await response.json().catch(() => ({ detail: '未知错误' }))
          setIsDeletingItem(null)
          alert(`删除播客失败: ${getApiErrorMessage(errorData, '未知错误')}`)
        }
      } else {
        const endpoint = type === 'flowchart' ? 'flowchart' :
          type === 'flashcards' ? 'flashcards' :
            type === 'quiz' ? 'quiz' :
              type === 'mindmap' ? 'mindmap' :
                type === 'highlights' ? 'highlights' : 'report'

        const response = await apiCall(`/api/document/${documentId}/${endpoint}/${id}`, {
          method: 'DELETE'
        })

        if (response.ok) {
          invalidateHistoryCache(type)
          if (type === 'flowchart') await loadFlowchartHistory({ forceRefresh: true })
          else if (type === 'flashcards') await loadFlashcardsHistory({ forceRefresh: true })
          else if (type === 'quiz') await loadQuizHistory({ forceRefresh: true })
          else if (type === 'mindmap') await loadMindmapHistory({ forceRefresh: true })
          else if (type === 'highlights') await loadHighlightsHistory({ forceRefresh: true })
          else if (type === 'report') await loadReportHistory({ forceRefresh: true })

          setIsDeletingItem(null)
        } else {
          setIsDeletingItem(null)
        }
      }
    } catch {
      setIsDeletingItem(null)
      alert('删除失败')
    }
  }

  const handleDeleteHistory = (type, id) => {
    const typeNames = {
      note: '笔记',
      flowchart: '学习路径',
      flashcards: '闪卡',
      quiz: '测验',
      mindmap: '思维导图',
      highlights: '重点提炼',
              report: '长报告',
      interview: '面试记录',
      ppt: 'PPT演示'
    }
    const typeName = typeNames[type] || 'content'

    setOpenMenuId(null)

    const dontShowAgain = localStorage.getItem('dontShowDeleteConfirm') === 'true'

    if (dontShowAgain) {
      executeDeleteHistory(type, id)
    } else {
      setDeleteConfirmState({
        isOpen: true,
        type,
        id,
        title: `确定要删除这条${typeName}吗？`
      })
    }
  }

  const startRename = (type, id, currentTitle) => {
    setRenamingItem({ type, id, originalTitle: currentTitle })
    setNewName(currentTitle)
    setOpenMenuId(null)
  }

  const confirmRename = async () => {
    if (!renamingItem) {
      return
    }

    const trimmedName = newName.trim()

    if (!trimmedName || trimmedName === renamingItem.originalTitle) {
      setRenamingItem(null)
      setNewName('')
      return
    }

    try {
      const { type, id } = renamingItem

      if (type === 'note') {
        const response = await apiCall(`/api/notes/${id}`, {
          method: 'PUT',
          body: JSON.stringify({ title: trimmedName })
        })

        if (response.ok) {
          await response.json()
          await loadNotes({ forceRefresh: true })
          setRenamingItem(null)
          setNewName('')
        } else {
          const errorData = await response.json().catch(() => ({}))
          throw new Error(getApiErrorMessage(errorData, '重命名笔记失败'))
        }
      } else if (type === 'interview') {
        const response = await apiCall(`/api/interview/${id}/rename`, {
          method: 'PUT',
          body: JSON.stringify({ title: trimmedName })
        })

        if (response.ok) {
          await response.json()
          invalidateHistoryCache('interview')
          await loadInterviewHistory({ forceRefresh: true })
          setRenamingItem(null)
          setNewName('')
        } else {
          const errorData = await response.json().catch(() => ({}))
          throw new Error(getApiErrorMessage(errorData, `重命名失败 (${response.status})`))
        }
      } else if (type === 'ppt') {
        const response = await apiCall(`/api/ppt/rename/${id}`, {
          method: 'PUT',
          body: JSON.stringify({ title: trimmedName })
        })

        if (response.ok) {
          await response.json()
          invalidateHistoryCache('ppt')
          await loadPptHistory({ forceRefresh: true })
          setRenamingItem(null)
          setNewName('')
        } else {
          const errorData = await response.json().catch(() => ({}))
          throw new Error(getApiErrorMessage(errorData, '重命名失败'))
        }
      } else if (type === 'poster') {
        const formData = new FormData()
        formData.append('title', trimmedName)

        const posterResponse = await apiCall(`/api/poster/${id}/rename`, {
          method: 'PUT',
          body: formData,
          headers: {}
        })

        if (posterResponse.ok) {
          await posterResponse.json()
          invalidateHistoryCache('poster')
          await loadPosterHistory({ forceRefresh: true })
          setRenamingItem(null)
          setNewName('')
        } else {
          const errorData = await posterResponse.json().catch(() => ({}))
          throw new Error(getApiErrorMessage(errorData, '重命名失败'))
        }
      } else if (type === 'podcast') {
        const response = await apiCall(`/api/podcast/${id}/rename`, {
          method: 'PUT',
          body: JSON.stringify({ title: trimmedName })
        })

        if (response.ok) {
          await loadPodcastHistory()
          setRenamingItem(null)
          setNewName('')
        } else {
          const errorData = await response.json().catch(() => ({}))
          throw new Error(getApiErrorMessage(errorData, '重命名失败'))
        }
      } else {
        const endpointMap = {
          flowchart: 'flowchart',
          flashcards: 'flashcards',
          quiz: 'quiz',
          mindmap: 'mindmap',
          highlights: 'highlights',
          report: 'report'
        }

        const endpoint = endpointMap[type]

        if (!endpoint) {
          alert(`未知的笔记类型: ${type}`)
          setRenamingItem(null)
          setNewName('')
          return
        }

        const otherResponse = await apiCall(`/api/document/${documentId}/${endpoint}/${id}/rename`, {
          method: 'PUT',
          body: JSON.stringify({ title: trimmedName })
        })

        if (otherResponse.ok) {
          await otherResponse.json()
          invalidateHistoryCache(type)
          if (type === 'flowchart') {
            await loadFlowchartHistory({ forceRefresh: true })
          }
          else if (type === 'flashcards') {
            await loadFlashcardsHistory({ forceRefresh: true })
          }
          else if (type === 'quiz') {
            await loadQuizHistory({ forceRefresh: true })
          }
          else if (type === 'mindmap') {
            await loadMindmapHistory({ forceRefresh: true })
          }
          else if (type === 'highlights') {
            await loadHighlightsHistory({ forceRefresh: true })
          }
          else if (type === 'report') {
            await loadReportHistory({ forceRefresh: true })
          }

          setRenamingItem(null)
          setNewName('')
        } else {
          const errorData = await otherResponse.json().catch(() => ({}))
          throw new Error(getApiErrorMessage(errorData, `重命名失败 (${otherResponse.status})`))
        }
      }
    } catch (error) {
      alert(`重命名失败: ${error.message}`)
      setRenamingItem(null)
      setNewName('')
    }
  }

  const cancelRename = () => {
    setRenamingItem(null)
    setNewName('')
  }

  return {
    openMenuId,
    setOpenMenuId,
    menuPosition,
    setMenuPosition,
    renamingItem,
    setRenamingItem,
    newName,
    setNewName,
    isDeletingItem,
    setIsDeletingItem,
    deleteConfirmState,
    setDeleteConfirmState,
    executeDeleteHistory,
    handleDeleteHistory,
    startRename,
    confirmRename,
    cancelRename,
  }
}
