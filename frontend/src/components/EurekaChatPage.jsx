import { useState, useEffect, useRef, useCallback } from 'react'
import { apiGet, apiRequest } from '../utils/api'
import ResizableLayout from './ResizableLayout'
import EurekaInfo from './EurekaInfo'
import DocumentChat from './DocumentChat'
import RightPanel from './RightPanel'
import Navbar from './Navbar'
import ConfirmDialog from './ConfirmDialog'
import useUIStore from '../stores/uiStore'
import './DocumentChat.css'
import './KnowledgeGraphPage.css'

/**
 * 灵感对话页
 * 复用文档解析页的三栏布局结构
 */
const EurekaChatPage = ({ eurekaId, onBack, onLogout, onSelectRecharge }) => {
  const [eureka, setEureka] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // 右侧面板状态
  const [rightPanelView, setRightPanelView] = useState('studio')

  // 面板最大化状态
  const [maximizedPanel, setMaximizedPanel] = useState('none') // 'none', 'left', 'center', 'right'

  const handleMaximize = (panel) => {
    setMaximizedPanel(current => current === panel ? 'none' : panel)
  }


  // 对话会话状态
  const [currentSessionId, setCurrentSessionId] = useState(null)
  const [sessionsRefreshKey, setSessionsRefreshKey] = useState(0)
  const [chatSessionKey, setChatSessionKey] = useState(0)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)

  // Refs
  const rightPanelRef = useRef(null)
  const documentChatRef = useRef(null)

  // 加载灵感详情
  useEffect(() => {
    const fetchEurekaDetails = async () => {
      if (!eurekaId) return
      try {
        setLoading(true)
        const res = await apiGet(`/api/knowledge-graph/eureka/${eurekaId}`, {
          cacheKey: `eureka:${eurekaId}:detail`,
          cacheTTL: 5 * 60 * 1000  // 5分钟缓存
        })
        if (res.success) {
          setEureka(res.data)

          // 初始化会话ID
          const storageKey = `eureka_${eurekaId}_sessionId`
          let storedSessionId = localStorage.getItem(storageKey)

          if (!storedSessionId) {
            storedSessionId = `eureka-session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
            localStorage.setItem(storageKey, storedSessionId)
          }

          setCurrentSessionId(storedSessionId)
        } else {
          setError(res.message || '加载灵感失败')
        }
      } catch (err) {
        setError(err.message || '加载灵感失败')
      } finally {
        setLoading(false)
      }
    }

    fetchEurekaDetails()
  }, [eurekaId])

  // 新建对话
  const handleNewChat = useCallback(async () => {
    const newSessionId = `eureka-session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
    setCurrentSessionId(newSessionId)

    if (eurekaId) {
      localStorage.setItem(`eureka_${eurekaId}_sessionId`, newSessionId)
    }

    setChatSessionKey(prev => prev + 1)
    setSessionsRefreshKey(prev => prev + 1)
  }, [eurekaId])

  // 删除当前对话：先确认
  const handleDeleteChat = useCallback(() => {
    if (!eurekaId || !currentSessionId) return
    setConfirmDeleteOpen(true)
  }, [eurekaId, currentSessionId])

  const deleteCurrentChat = useCallback(async () => {
    setConfirmDeleteOpen(false)
    const showError = (message) => useUIStore.getState().showToast(message, { type: 'error' })
    try {
      const response = await apiRequest(
        `/api/knowledge-graph/eureka/${eurekaId}/history?session_id=${encodeURIComponent(currentSessionId)}`,
        { method: 'DELETE' }
      )

      if (response.ok) {
        await handleNewChat()
      } else {
        showError('删除对话失败，请重试')
      }
    } catch {
      showError('删除对话失败，请重试')
    }
  }, [eurekaId, currentSessionId, handleNewChat])

  // 加载历史会话
  const handleLoadSession = useCallback((sessionId) => {
    setCurrentSessionId(sessionId)
    if (eurekaId) {
      localStorage.setItem(`eureka_${eurekaId}_sessionId`, sessionId)
    }
    setChatSessionKey(prev => prev + 1)
  }, [eurekaId])

  // 发送问题到对话
  const handleSendQuestionToChat = useCallback((question) => {
    if (documentChatRef.current?.sendQuestion) {
      documentChatRef.current.sendQuestion(question)
    }
  }, [])

  // 设置输入框内容
  const handleSetChatInput = useCallback((data, mode = 'edit') => {
    if (documentChatRef.current) {
      if (mode === 'reference' && documentChatRef.current.setReferenceContent) {
        documentChatRef.current.setReferenceContent(data)
      } else if (documentChatRef.current.setInputText) {
        documentChatRef.current.setInputText(typeof data === 'string' ? data : (data.content || ''))
      }
    }
  }, [])

  // 评估灵感质量
  const handleEvaluateEureka = useCallback(() => {
    if (!documentChatRef.current?.sendQuestion) return

    const prompt = `请不要考虑任何人的感受和情绪，包括用户在内，作为一个绝对理性的智能体，请从以下五个维度，对这个灵感进行全面客观的评估：

## 📊 灵感评估报告

### 1. 🌟 创新性（Innovation）
**评分：？/10**
- 这个想法有多新颖？是否打破了现有思维框架？
- 与现有解决方案相比，差异化在哪里？
- 是渐进式改进还是突破性创新？

### 2. 🧩 逻辑合理性（Logic）
**评分：？/10**
- 从这些节点到灵感的推理链条是否清晰？
- 各部分之间的因果关系是否成立？
- 有没有逻辑漏洞或跳跃？

### 3. ⚙️ 技术可行性（Feasibility）
**评分：？/10**
- 以当前技术水平，这个想法能实现吗？
- 需要什么关键技术或资源？
- 主要技术障碍是什么？

### 4. 💎 实用价值（Practicality）
**评分：？/10**
- 这个灵感能解决什么实际问题？
- 目标用户/受益者是谁？
- 市场需求或应用场景有多大？

### 5. 🌱 当前成熟度（Maturity）
**评分：？/10**
- 这是初步概念还是可执行方案？
- 还需要补充哪些细节？
- 距离落地还有多远？

---

### 📈 综合评分：？/10

### 💡 改进建议
1. ...
2. ...
3. ...

### 🚀 下一步行动
建议优先做什么来推进这个灵感？`

    documentChatRef.current.sendQuestion(prompt)
  }, [])

  // 加载中状态
  if (loading) {
    return (
      <div className="eureka-chat-page">
        <div className="eureka-chat-loading">
          <div className="kg-spinner"></div>
          <p>正在回顾这个绝妙的想法...</p>
        </div>
      </div>
    )
  }

  // 错误状态
  if (error) {
    return (
      <div className="eureka-chat-page">
        <div className="eureka-chat-error">
          <div className="error-icon">⚠️</div>
          <p>出错了: {error}</p>
          <button className="kg-primary-btn" onClick={onBack}>返回知识图谱</button>
        </div>
      </div>
    )
  }

  return (
    <div className="eureka-chat-page">
      {/* 自定义 Navbar - 显示灵感标题 */}
      <Navbar
        onLogout={onLogout}
        onSelectRecharge={onSelectRecharge}
        title={eureka?.title || '深入讨论'}
        onLogoClick={onBack}
      />

      {/* 使用与文档解析页相同的三栏布局 */}
      <div className="document-layout">
        <ResizableLayout
          maximizedPanel={maximizedPanel}
          onMaximize={handleMaximize}
          leftPanel={
            <EurekaInfo
              eureka={eureka}
              onLoadSession={handleLoadSession}
              currentSessionId={currentSessionId}
              refreshKey={sessionsRefreshKey}
              isMaximized={maximizedPanel === 'left'}
              onToggleMaximize={() => handleMaximize('left')}
            />
          }
          centerPanel={
            <div className="chat-layout-wrapper">
              <div className="chat-header">
                <div className="chat-title-wrapper">
                  <h3 className="chat-title">深入讨论</h3>
                </div>

                {/* 新建和删除按钮 */}
                <div className="chat-control-buttons">
                  <button
                    className="chat-control-btn"
                    onClick={handleNewChat}
                    title="新建对话"
                  >
                    新建
                  </button>
                  <button
                    className="chat-control-btn delete"
                    onClick={handleDeleteChat}
                    title="删除当前对话"
                  >
                    删除
                  </button>
                </div>

                {/* 快捷提示词按钮 */}
                <div className="quick-action-buttons">
                  <button
                    className="quick-action-btn"
                    onClick={handleEvaluateEureka}
                    title="从创新性、逻辑性、可行性、实用性、成熟度五个维度评估灵感"
                  >
                    📊 灵感评估
                  </button>
                </div>

                <button
                  className="panel-embedded-maximize-btn"
                  onClick={() => handleMaximize('center')}
                  title={maximizedPanel === 'center' ? '还原' : '最大化'}
                >
                  {maximizedPanel === 'center' ? (
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3" />
                    </svg>
                  ) : (
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                    </svg>
                  )}
                </button>
              </div>

              <main className="document-chat-main">
                <DocumentChat
                  ref={documentChatRef}
                  key={chatSessionKey}
                  eurekaId={eurekaId}
                  eurekaData={eureka}
                  sessionId={currentSessionId}
                  onNewChat={handleNewChat}
                  onDeleteChat={handleDeleteChat}
                  onNoteSaved={() => {
                    setRightPanelView('studio')
                    if (rightPanelRef.current) {
                      rightPanelRef.current.refreshNotes?.({ forceRefresh: true })
                    }
                  }}
                  onHistoryUpdated={() => setSessionsRefreshKey(prev => prev + 1)}
                />
              </main>
            </div>
          }
          rightPanel={
            <RightPanel
              ref={rightPanelRef}
              view={rightPanelView}
              onViewChange={setRightPanelView}
              documentId={null}  // 灵感对话不关联文档
              eurekaId={eurekaId}  // 传递灵感ID
              disabled={true}  // 禁用工作室功能卡片
              onSendQuestionToChat={handleSendQuestionToChat}
              onSetChatInput={handleSetChatInput}
              isMaximized={maximizedPanel === 'right'}
              onToggleMaximize={() => handleMaximize('right')}
            />
          }
        />
      </div>

      <ConfirmDialog
        isOpen={confirmDeleteOpen}
        title="删除对话"
        message="确定要删除当前对话吗？此操作不可恢复。"
        confirmLabel="删除"
        onConfirm={deleteCurrentChat}
        onCancel={() => setConfirmDeleteOpen(false)}
      />
    </div>
  )
}

export default EurekaChatPage

