import { useState } from 'react'
import AnimatedModal from '../ui/AnimatedModal'
import { formatDisplayLabel } from './utils'

/**
 * 灵感结果弹窗。是否已收藏（savedId）由页面持有：关闭弹窗时页面要据此判断是否提示「尚未收藏」。
 */
const EurekaDialog = ({ isOpen, onClose, result, loading, error, onRetry, onSave, onDelete, savedId, onSavedIdChange, onDeepDive }) => {
  const [saving, setSaving] = useState(false)
  const nodes = Array.isArray(result?.nodes) ? result.nodes : []

  const saveIfNeeded = async () => {
    if (savedId) return savedId
    setSaving(true)
    try {
      const savedItem = await onSave(result)
      if (savedItem?.id) {
        onSavedIdChange(savedItem.id)
        return savedItem.id
      }
      return null
    } finally {
      setSaving(false)
    }
  }

  const handleToggleSave = async () => {
    if (!result || saving) return
    if (savedId) {
      setSaving(true)
      try {
        if (await onDelete(savedId, true)) onSavedIdChange(null)
      } finally {
        setSaving(false)
      }
    } else {
      await saveIfNeeded()
    }
  }

  const dialogHeight = loading
    ? { height: '300px', maxHeight: '50vh' }
    : { height: '700px', maxHeight: '90vh' }

  return (
    <AnimatedModal
      isOpen={isOpen}
      onClose={onClose}
      size={loading ? 'sm' : 'lg'}
      width={loading ? undefined : 1200}
      className="kg-dialog kg-eureka-dialog"
      style={dialogHeight}
    >

      <div className="kg-dialog-body kg-eureka-dialog-body" style={{ justifyContent: loading ? 'center' : 'flex-start' }}>
        {loading ? (
          <div className="kg-loading-state">
            <div className="kg-spinner eureka-spinner"></div>
            <p className="kg-loading-text">正在星海中寻找未曾连接的孤岛...</p>
          </div>
        ) : error ? (
          <div className="kg-error-state">
            <p>灵感枯竭了: {error}</p>
            <button className="kg-retry-btn" onClick={onRetry}>再试一次</button>
          </div>
        ) : result ? (
          <div className="kg-eureka-content">
            <div className="kg-eureka-hero">
              <div className="kg-eureka-icon-wrapper">
                <div className="kg-eureka-icon">💡</div>
              </div>
              <h2 className="kg-eureka-title">{result.idea}</h2>
            </div>

            <div className="kg-eureka-scroll-content">
              <div className="kg-eureka-section">
                <div className="kg-eureka-nodes">
                  {nodes.map((node, index) => (
                    <span key={node?.id ?? index} className="kg-eureka-node-tag">
                      {formatDisplayLabel(node?.label)}
                    </span>
                  ))}
                </div>
              </div>

              <div className="kg-eureka-section">
                <div className="kg-eureka-reasoning">
                  {result.reasoning}
                </div>
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

                {onDeepDive && (
                  <button
                    className="kg-btn-primary"
                    disabled={saving}
                    onClick={async () => {
                      const eurekaId = await saveIfNeeded()
                      if (eurekaId) {
                        onDeepDive(eurekaId)
                      }
                    }}
                  >
                    深入讨论
                  </button>
                )}
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </AnimatedModal>
  )
}

export default EurekaDialog
