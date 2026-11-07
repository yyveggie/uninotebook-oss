import { useState } from 'react'
import AnimatedModal from '../ui/AnimatedModal'

const asList = (value) => (Array.isArray(value) ? value : [])

/**
 * 知识间隙弹窗。是否已收藏（savedId）由页面持有：从侧栏打开已收藏的分析时传入它的 id，
 * 弹窗里显示「已收藏」且可以取消收藏，而不是再存一份。
 */
const KnowledgeGapDialog = ({ isOpen, onClose, onStartAnalysis, onSave, onDelete, gaps, communities, loading, error, message, savedId, onSavedIdChange, costLabel }) => {
  const [saving, setSaving] = useState(false)

  const handleToggleSave = async () => {
    if (!gaps || gaps.length === 0 || saving) return
    setSaving(true)
    try {
      if (savedId) {
        if (await onDelete(savedId)) onSavedIdChange(null)
      } else {
        const result = await onSave({ gaps, communities })
        if (result?.id) onSavedIdChange(result.id)
      }
    } finally {
      setSaving(false)
    }
  }

  const hasResult = loading || error || (gaps && gaps.length > 0) || message
  const showIntro = !hasResult

  const dialogHeight = showIntro
    ? { height: '480px', maxHeight: '70vh' }
    : loading
      ? { height: '300px', maxHeight: '50vh' }
      : { height: '700px', maxHeight: '90vh' }

  const dialogSize = showIntro ? 'md' : loading ? 'sm' : 'lg'
  const dialogWidth = showIntro ? 560 : loading ? undefined : 1000

  return (
    <AnimatedModal
      isOpen={isOpen}
      onClose={onClose}
      size={dialogSize}
      width={dialogWidth}
      className="kg-dialog kg-eureka-dialog"
      style={dialogHeight}
    >
      <div className="kg-dialog-body kg-eureka-dialog-body" style={{ justifyContent: (showIntro || loading) ? 'center' : 'flex-start' }}>
        {showIntro ? (
          <div className="kg-eureka-content" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', gap: 16 }}>
            <div className="kg-eureka-icon-wrapper kg-eureka-icon-wrapper-medium">
              <div className="kg-eureka-icon">🔬</div>
            </div>
            <h2 className="kg-eureka-title kg-eureka-title-medium">知识间隙分析</h2>
            <p className="kg-loading-subtext" style={{ maxWidth: 420, lineHeight: 1.7 }}>
              分析你的知识图谱结构，找出彼此孤立、尚未建立联系的<strong>知识社区</strong>，
              并推荐<strong>桥梁概念</strong>和<strong>阅读方向</strong>帮助你弥合盲区。
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
              <div className="kg-loading-subtext" style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>🧩</span><span>识别互不连接的知识群落</span>
              </div>
              <div className="kg-loading-subtext" style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>🌉</span><span>建议可以打通孤岛的桥梁概念</span>
              </div>
              <div className="kg-loading-subtext" style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>📖</span><span>推荐填补空白的阅读主题</span>
              </div>
            </div>
            <button
              className="kg-primary-btn"
              onClick={onStartAnalysis}
              style={{ marginTop: 12 }}
            >
              开始分析
              {costLabel && <span className="kg-cost-badge">{costLabel}</span>}
            </button>
          </div>
        ) : loading ? (
          <div className="kg-loading-state">
            <div className="kg-spinner eureka-spinner"></div>
            <p className="kg-loading-text">正在分析知识图谱结构...</p>
            <p className="kg-loading-subtext">寻找知识盲区与跨域连接机会</p>
          </div>
        ) : error ? (
          <div className="kg-error-state">
            <p>分析失败: {error}</p>
            <div className="kg-dialog-inline-actions">
              <button className="kg-retry-btn" onClick={onStartAnalysis}>重新分析</button>
              <button className="kg-retry-btn" onClick={onClose}>关闭</button>
            </div>
          </div>
        ) : (!gaps || gaps.length === 0) ? (
          <div className="kg-empty-state">
            <div className="kg-eureka-icon-wrapper kg-eureka-icon-wrapper-large">
              <div className="kg-eureka-icon">🎉</div>
            </div>
            <p className="kg-loading-text">
              {message || '你的知识网络高度连通！'}
            </p>
            <p className="kg-loading-subtext">
              {communities && communities.length > 0
                ? `已识别 ${communities.length} 个知识社区，它们之间没有明显的间隙。`
                : '没有发现需要弥合的知识间隙。'
              }
            </p>
            <button className="kg-retry-btn" onClick={onStartAnalysis}>重新分析</button>
          </div>
        ) : (
          <div className="kg-eureka-content">
            <div className="kg-eureka-hero kg-eureka-hero-spacious">
              <div className="kg-eureka-icon-wrapper">
                <div className="kg-eureka-icon">🔬</div>
              </div>
              <h2 className="kg-eureka-title">知识间隙分析</h2>
              <p className="kg-loading-subtext kg-loading-subtext-tight-top">
                发现了 {gaps.length} 个可探索的跨域连接机会
                {communities && communities.length > 0 && ` · ${communities.length} 个知识社区`}
              </p>
            </div>

            <div className="kg-eureka-scroll-content">
              <div className="kg-gap-content">
                {gaps.filter((gap) => gap && typeof gap === 'object').map((gap, index) => (
                  <div key={index} className="kg-gap-card">
                    <div className="kg-gap-communities">
                      <div className="kg-gap-community">
                        <div className="kg-gap-community-title">{gap.theme_a || `社区 ${gap.community_a}`}</div>
                        {communities && communities[gap.community_a] && (
                          <div className="kg-gap-community-terms">
                            {asList(communities[gap.community_a].top_terms).slice(0, 4).map((t, i) => (
                              <span key={i} className="kg-gap-community-term">{t}</span>
                            ))}
                          </div>
                        )}
                      </div>
                      <span className="kg-gap-arrow">⇄</span>
                      <div className="kg-gap-community">
                        <div className="kg-gap-community-title">{gap.theme_b || `社区 ${gap.community_b}`}</div>
                        {communities && communities[gap.community_b] && (
                          <div className="kg-gap-community-terms">
                            {asList(communities[gap.community_b].top_terms).slice(0, 4).map((t, i) => (
                              <span key={i} className="kg-gap-community-term">{t}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="kg-gap-explanation">{gap.explanation}</div>

                    {asList(gap.bridge_concepts).length > 0 && (
                      <div className="kg-gap-bridges">
                        <span style={{ fontSize: 12, color: '#6b7280', marginRight: 4 }}>桥梁概念:</span>
                        {asList(gap.bridge_concepts).map((c, i) => (
                          <span key={i} className="kg-gap-bridge-tag">{c}</span>
                        ))}
                      </div>
                    )}

                    {asList(gap.suggested_topics).length > 0 && (
                      <div className="kg-gap-suggestions">
                        <span style={{ fontSize: 12, color: '#6b7280', marginRight: 4 }}>推荐阅读:</span>
                        {asList(gap.suggested_topics).map((t, i) => (
                          <span key={i} className="kg-gap-suggestion-tag">{t}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <div className="kg-eureka-footer">
              <div className="kg-eureka-controls">
                <button
                  className={savedId ? "kg-btn-saved" : "kg-btn-secondary"}
                  onClick={handleToggleSave}
                  disabled={saving}
                >
                  {savedId ? '★ 已收藏' : '☆ 收藏'}
                </button>
                <button className="kg-btn-secondary" onClick={onStartAnalysis} disabled={saving}>
                  重新分析
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AnimatedModal>
  )
}

export default KnowledgeGapDialog
