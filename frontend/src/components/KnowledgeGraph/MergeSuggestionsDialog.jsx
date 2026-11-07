import AnimatedModal from '../ui/AnimatedModal'
import { formatDisplayLabel } from './utils'

const MergeSuggestionsDialog = ({ isOpen, onClose, suggestions, onMerge, onMergeAll, loading, merging, error, message }) => {
  const validSuggestions = (suggestions || []).filter((s) => {
    const r = s.reason || ''
    return !r.includes('不建议') && !r.includes('保留两者')
  })

  const dialogHeight = loading
    ? { height: '300px', maxHeight: '50vh' }
    : { height: '700px', maxHeight: '90vh' }

  return (
    <AnimatedModal
      isOpen={isOpen}
      onClose={onClose}
      size={loading ? 'sm' : 'lg'}
      className="kg-dialog kg-eureka-dialog"
      style={dialogHeight}
    >

      <div className="kg-dialog-body kg-eureka-dialog-body" style={{ justifyContent: loading ? 'center' : 'flex-start' }}>
        {loading ? (
          <div className="kg-loading-state">
            <div className="kg-spinner eureka-spinner"></div>
            <p className="kg-loading-text">正在分析您的知识网络...</p>
            <p className="kg-loading-subtext">寻找可合并的重复节点</p>
          </div>
        ) : error ? (
          <div className="kg-error-state">
            <p>分析失败: {error}</p>
            <button className="kg-retry-btn" onClick={onClose}>关闭</button>
          </div>
        ) : validSuggestions.length === 0 ? (
          <div className="kg-empty-state">
            <div className="kg-eureka-icon-wrapper kg-eureka-icon-wrapper-large">
              <div className="kg-eureka-icon">🎉</div>
            </div>
            <p className="kg-loading-text">太棒了！</p>
            <p className="kg-loading-subtext">{message || 'AI 觉得您的知识网络非常清晰，没有发现明显的重复节点。'}</p>
          </div>
        ) : (
          <div className="kg-eureka-content">
            <div className="kg-eureka-hero kg-eureka-hero-spacious">
              <div className="kg-eureka-icon-wrapper">
                <div className="kg-eureka-icon">✨</div>
              </div>
              <h2 className="kg-eureka-title">智能整理建议</h2>
              <p className="kg-loading-subtext kg-loading-subtext-tight-top">
                发现了 {validSuggestions.length} 组可能重复的节点
              </p>
              {validSuggestions.length > 1 && (
                <button
                  className="kg-primary-btn"
                  style={{ marginTop: 16 }}
                  onClick={() => onMergeAll(validSuggestions)}
                  disabled={merging}
                >
                  {merging ? '正在合并…' : `全部合并 (${validSuggestions.length})`}
                </button>
              )}
            </div>

            <div className="kg-eureka-scroll-content">
              {validSuggestions.map((s) => (
                <div key={`${s.source_id}-${s.target_id}`} className="kg-merge-card">
                  <div className="kg-merge-header">
                    <div className="kg-merge-nodes">
                      <div className="kg-merge-node source">
                        <span className="kg-merge-tag delete">删除</span>
                        <span className="kg-merge-text">{formatDisplayLabel(s.source_label)}</span>
                      </div>
                      <div className="kg-merge-arrow">➔</div>
                      <div className="kg-merge-node target">
                        <span className="kg-merge-tag keep">保留</span>
                        <span className="kg-merge-text">{formatDisplayLabel(s.target_label)}</span>
                      </div>
                    </div>
                    <button
                      className="kg-primary-btn kg-merge-btn"
                      onClick={() => onMerge(s)}
                      disabled={merging}
                    >
                      合并
                    </button>
                  </div>
                  <div className="kg-merge-reason">
                    💡 {s.reason}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </AnimatedModal>
  )
}

export default MergeSuggestionsDialog
