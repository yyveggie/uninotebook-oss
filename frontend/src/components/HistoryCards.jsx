import { useState, useEffect, useCallback } from 'react'
import { motion as Motion } from 'framer-motion'
import { apiGet, apiRequest } from '../utils/api'
import { dataCache } from '../utils/dataCache'
import { invalidateDocumentStatusCache } from '../utils/documentStatusCache'
import ConfirmDialog from './ConfirmDialog'
import './HistoryCards.css'

const HISTORY_LIMIT = 1000

const stripSummaryPrefix = (raw) => {
  if (!raw) return ''
  let s = String(raw).trimStart()
  // 去掉类似 "## 📄 核心内容" 的 Markdown 标题前缀，但保留后面的正文
  s = s.replace(/^##\s*.*?核心内容\s*/u, '')
  return s.trimStart()
}

import './Skeleton.css'
import useFirstEntrance from '../hooks/useFirstEntrance'

function SkeletonCard() {
  return (
    <div className="history-card document-card" style={{ pointerEvents: 'none' }}>
      <div className="card-header">
        <div className="skeleton" style={{ width: '120px', height: '14px' }}></div>
        <div className="skeleton" style={{ width: '40px', height: '14px' }}></div>
      </div>

      <div className="card-title-row">
        <div className="skeleton skeleton-circular" style={{ width: '20px', height: '20px', marginRight: '8px', flexShrink: 0 }}></div>
        <div className="skeleton" style={{ width: '60%', height: '18px' }}></div>
      </div>

      <div className="card-content">
        <div className="skeleton" style={{ width: '100%', height: '14px', marginBottom: '6px' }}></div>
        <div className="skeleton" style={{ width: '80%', height: '14px', marginBottom: '16px' }}></div>
        <div className="skeleton" style={{ width: '90px', height: '32px', borderRadius: '16px' }}></div>
      </div>
    </div>
  )
}

function HistoryCards({ user, onDocumentSelect, refreshKey }) {
  const animateIn = useFirstEntrance()
  const [documentHistory, setDocumentHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeMenu, setActiveMenu] = useState(null) // 当前打开的菜单ID
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 })

  // 删除确认对话框状态
  const [deleteConfirmState, setDeleteConfirmState] = useState({
    isOpen: false,
    doc: null
  })

  // 定义 fetchHistory（放在 useEffect 之前）
  const fetchHistory = useCallback(async (forceRefresh = false) => {
    if (!user) {
      setLoading(false)
      return
    }

    try {
      const cacheKey = user ? `history:documents:${user.id}:limit:${HISTORY_LIMIT}` : null

      // 检查缓存，如果有缓存就不显示 loading
      if (!forceRefresh && cacheKey && dataCache.get(cacheKey)) {
        const cached = dataCache.get(cacheKey)
        if (cached?.success) {
          setDocumentHistory(cached.data || [])
          // 即使命中缓存，也发起后台请求更新数据 (SWR 模式)
          // 使用 cacheMode: 'no-cache' 绕过 dataCache 读取和浏览器 HTTP 缓存，确保真正请求网络
          apiGet(`/api/history/documents?limit=${HISTORY_LIMIT}`, {
            cacheKey,
            cacheTTL: 5 * 60 * 1000,
            cacheMode: 'no-cache',
          }).then(data => {
            if (data?.success) {
              setDocumentHistory(data.data || [])
            }
          }).catch(err => void err)
          
          setLoading(false)
          return
        }
      }

      // 缓存未命中，显示 loading
      setLoading(true)

      const data = await apiGet(`/api/history/documents?limit=${HISTORY_LIMIT}`, {
        cacheKey,
        cacheTTL: 5 * 60 * 1000,  // 缓存 5 分钟
        cacheMode: 'no-cache',    // 绕过浏览器 HTTP 缓存，确保从服务器获取最新数据
      })

      if (data?.success) {
        setDocumentHistory(data.data || [])
      }
    } catch (err) {
      void err;
    } finally {
      setLoading(false)
    }
  }, [user])

  // useEffect - 用户变化时加载历史
  useEffect(() => {
    if (user) {
      fetchHistory()
    } else {
      setLoading(false)
    }
  }, [user, fetchHistory])

  // useEffect - 监听 refreshKey 变化
  useEffect(() => {
    if (user && refreshKey > 0) {
      // 仅在后台静默刷新，不强制清除缓存，避免出现骨架屏
      fetchHistory(false)
    }
  }, [refreshKey, user, fetchHistory])

  // useEffect - 点击外部关闭菜单
  useEffect(() => {
    if (!activeMenu) return

    const handleClickOutside = (e) => {
      if (!e.target.closest('.card-menu-dropdown') && !e.target.closest('.card-menu-btn')) {
        setActiveMenu(null)
      }
    }

    setTimeout(() => {
      document.addEventListener('click', handleClickOutside)
    }, 0)

    return () => {
      document.removeEventListener('click', handleClickOutside)
    }
  }, [activeMenu])

  // 执行删除文档
  const executeDelete = async (doc) => {
    try {
      const response = await apiRequest(`/api/document/${doc.document_id}`, {
        method: 'DELETE'
      })

      if (response.ok || response.status === 404) {
        // 200: 正常删除；404: 文档已从 DB 中删除（幽灵文档），同样视为成功
        setDocumentHistory(prev => prev.filter(d => d.document_id !== doc.document_id))
        setActiveMenu(null)
        // 清理所有可能的缓存键，包括 dataCache 和 SWR 缓存
        if (user?.id) {
          dataCache.invalidateByPrefix(`history:documents:${user.id}`)
        }
        dataCache.invalidateByPrefix(`doc:${doc.document_id}:history:`)
        dataCache.invalidateByPrefix('SWR:/api/history/documents')
        dataCache.invalidateByPrefix('GET:/api/history/documents')
        invalidateDocumentStatusCache(doc.document_id)
      } else {
        alert('删除失败')
      }
    } catch (err) {
      void err;
      alert('删除失败')
    }
  }

  // 删除文档
  const handleDelete = (doc) => {
    // 检查是否不再提示
    const dontShowAgain = localStorage.getItem('dontShowDeleteConfirm') === 'true'

    if (dontShowAgain) {
      executeDelete(doc)
    } else {
      setDeleteConfirmState({
        isOpen: true,
        doc
      })
    }
  }

  const touchDocumentOnOpen = useCallback((doc) => {
    if (!doc?.document_id) return

    const nowIso = new Date().toISOString()

    // 1) 先乐观更新当前列表中的“最后更新时间”
    setDocumentHistory(prev => prev.map(item => (
      item.document_id === doc.document_id
        ? { ...item, last_accessed_at: nowIso }
        : item
    )))

    // 2) 失效本地历史缓存，避免返回学习空间时读到旧时间
    if (user?.id) {
      dataCache.invalidateByPrefix(`history:documents:${user.id}`)
      dataCache.invalidateByPrefix(`SWR:/api/history/documents`)
    }

    // 3) 异步通知后端更新时间（不阻塞跳转）
    apiRequest(`/api/document/${doc.document_id}/touch`, { method: 'POST' })
      .catch(() => {})
  }, [user?.id])

  // Loading State with Skeletons
  if (loading && user) {
    return (
      <div className="history-section">
        <div className="history-group">
          <div className="history-grid">
            {Array.from({ length: 4 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        </div>
      </div>
    )
  }

  const hasHistory = documentHistory.length > 0

  return (
    <div className="history-section">
      {!user ? (
        <div className="history-empty-state">
          <div className="history-empty-icon">🔐</div>
          <h4>登录后查看学习笔记</h4>
          <p>登录之后即可同步你的学习历程，随时回顾历史记录。</p>
        </div>
      ) : !hasHistory ? (
        <div className="history-empty-state">
          <div className="history-empty-icon">📝</div>
          <p>你还没有学习笔记，上传文档或粘贴文本，立即生成你的学习笔记。</p>
        </div>
      ) : null}

      {/* 文档学习历史 */}
      {documentHistory.length > 0 && (
        <div className="history-group">
          <div className="history-grid">
            {documentHistory.map((item, index) => (
              <Motion.div
                key={item.document_id}
                className="history-card document-card"
                initial={animateIn ? { opacity: 0, y: 20 } : false}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.05 }}
                onClick={() => {
                  // 如果点击的是菜单相关元素，不触发卡片点击
                  if (activeMenu === item.document_id) return
                  touchDocumentOnOpen(item)
                  onDocumentSelect(item)
                }}
              >
                <div className="card-header">
                  <span className="card-date">
                    最后更新：{new Date(item.last_accessed_at).toLocaleDateString('zh-CN')} {new Date(item.last_accessed_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                  </span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span className="card-size">{(item.file_size / 1024).toFixed(1)} KB</span>
                    <div style={{ position: 'relative' }}>
                      <button
                        className="card-menu-btn"
                        onClick={(e) => {
                          e.stopPropagation()
                          const rect = e.currentTarget.getBoundingClientRect()
                          setMenuPosition({
                            top: rect.bottom + 4,
                            left: rect.right - 100
                          })
                          setActiveMenu(activeMenu === item.document_id ? null : item.document_id)
                        }}
                      >
                        ⋮
                      </button>
                    </div>
                  </div>
                </div>

                <div className="card-title-row">
                  <span className="card-icon">
                    {item.file_type === 'url' ? '🔗' : item.file_type === 'pdf' ? '📕' : '📄'}
                  </span>
                  <h5 className="card-title">{item.display_name || item.filename}</h5>
                </div>

                <div className="card-content">
                  {item.summary && (
                    <p className="card-summary">{stripSummaryPrefix(item.summary).slice(0, 60)}...</p>
                  )}
                  <button className="card-button" onClick={(e) => {
                    e.stopPropagation();
                    touchDocumentOnOpen(item)
                    onDocumentSelect(item);
                  }}>
                    继续学习 →
                  </button>
                </div>
              </Motion.div>
            ))}
          </div>
        </div>
      )}

      {/* 固定定位的菜单 */}
      {activeMenu && (
        <div
          className="card-menu-dropdown"
          style={{
            top: `${menuPosition.top}px`,
            left: `${menuPosition.left}px`
          }}
        >
          <button
            className="card-menu-item delete"
            onClick={(e) => {
              e.stopPropagation()
              const item = documentHistory.find(doc => doc.document_id === activeMenu)
              if (item) handleDelete(item)
            }}
          >
            🗑️ 删除
          </button>
        </div>
      )}

      {/* 删除确认对话框 */}
      <ConfirmDialog
        isOpen={deleteConfirmState.isOpen}
        title="确认删除"
        message={deleteConfirmState.doc ? `确定要删除文档"${deleteConfirmState.doc.display_name || deleteConfirmState.doc.filename}"吗？此操作不可恢复。` : ''}
        confirmLabel="删除"
        isDangerous={true}
        onConfirm={(dontRemind) => {
          if (dontRemind) {
            localStorage.setItem('dontShowDeleteConfirm', 'true')
          }
          if (deleteConfirmState.doc) {
            executeDelete(deleteConfirmState.doc)
          }
          setDeleteConfirmState(prev => ({ ...prev, isOpen: false }))
        }}
        onCancel={() => setDeleteConfirmState(prev => ({ ...prev, isOpen: false }))}
      />
    </div>
  )
}

export default HistoryCards

