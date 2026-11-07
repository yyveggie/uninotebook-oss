import { useState, useEffect, useRef } from 'react'
import { motion as Motion } from 'framer-motion'
import { apiGet } from '../utils/api'
import { dataCache } from '../utils/dataCache'
import { SkeletonSessionList } from './Skeleton'
import './DocumentInfo.css' // 复用 DocumentInfo 的样式

/**
 * 灵感详情面板组件
 * 显示连接点、灵感内容和历史对话列表
 */
function EurekaInfo({
  eureka,
  onLoadSession,
  currentSessionId,
  refreshKey,
  isMaximized,
  onToggleMaximize
}) {
  const [sessions, setSessions] = useState([])
  const [isLoadingSessions, setIsLoadingSessions] = useState(false)
  const hasLoadedSessionsRef = useRef(false)
  const eurekaId = eureka?.id

  const loadSessions = async (forceRefresh = false) => {
    if (!eurekaId) return

    const cacheKey = `eureka:${eurekaId}:sessions`

    // 强制刷新时清除缓存
    if (forceRefresh) {
      dataCache.invalidate(cacheKey)
    }

    try {
      setIsLoadingSessions(true)
      const data = await apiGet(`/api/knowledge-graph/eureka/${eurekaId}/sessions`, {
        cacheKey,
        cacheTTL: 30 * 1000,  // 30秒缓存
        cacheMode: forceRefresh ? 'no-cache' : 'default'
      })

      if (data.success && data.data) {
        setSessions(data.data)
        if (!hasLoadedSessionsRef.current) {
          hasLoadedSessionsRef.current = true
        }
      }
    } catch (err) {
      void err
    } finally {
      setIsLoadingSessions(false)
    }
  }

  // 加载会话列表
  useEffect(() => {
    // refreshKey 变化时强制刷新缓存
    loadSessions(refreshKey > 0)
  }, [eurekaId, refreshKey])

  if (!eureka) return null

  return (
    <Motion.div
      className="document-info-panel"
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className="info-header">
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

      {/* 历史对话列表 */}
      <div className="sessions-container" style={{ position: 'absolute', top: '24px', left: '24px', right: '50px', marginTop: 0 }}>
        <h4 className="sessions-title" style={{ marginBottom: '16px' }}>历史对话</h4>
        {isLoadingSessions ? (
          <div className="sessions-loading"><SkeletonSessionList count={3} /></div>
        ) : sessions.length > 0 ? (
          <div className="sessions-list">
            {sessions.map((session) => (
              <div
                key={session.session_id}
                className={`session-item ${currentSessionId === session.session_id ? 'active' : ''}`}
                onClick={() => onLoadSession && onLoadSession(session.session_id)}
              >
                <div className="session-question">
                  {session.first_question || '未命名对话'}
                </div>
                <div className="session-meta">
                  <span className="session-count">{session.message_count} 条消息</span>
                  <span className="session-time">
                    {new Date(session.last_activity_time).toLocaleDateString('zh-CN', {
                      month: 'numeric',
                      day: 'numeric'
                    })}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="sessions-empty">
            暂无历史对话
            <br />
            <span className="sessions-empty-hint">开始提问后会自动保存</span>
          </div>
        )}
      </div>
    </Motion.div>
  )
}

export default EurekaInfo

