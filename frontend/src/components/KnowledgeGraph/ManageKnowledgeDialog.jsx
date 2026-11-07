import AnimatedModal from '../ui/AnimatedModal'
import { formatDisplayLabel } from './utils'

const ManageKnowledgeDialog = ({
  isOpen,
  onClose,
  loading,
  manageNodeId,
  setManageNodeId,
  manageLabel,
  setManageLabel,
  manageDescription,
  setManageDescription,
  manageSearchQuery,
  setManageSearchQuery,
  nodeSuggestionList,
  mergeSearchQuery,
  setMergeSearchQuery,
  mergeTargetId,
  setMergeTargetId,
  mergeSuggestionList,
  onSave,
  onDelete,
  onMerge,
  onAiOrganize,
  organizeCostLabel
}) => {
  const dialogHeight = loading
    ? { height: '300px', maxHeight: '50vh' }
    : { height: '700px', maxHeight: '90vh' }

  return (
    <AnimatedModal
      isOpen={isOpen}
      onClose={onClose}
      size="lg"
      width={1200}
      className="kg-dialog kg-manage-modal"
      style={{
        ...dialogHeight
      }}
    >
      <div className="kg-dialog-body kg-manage-dialog-body">
        <div className="kg-manage-header">
          <h2 className="kg-manage-title">知识节点管理</h2>
          <p className="kg-manage-subtitle">编辑节点详情或合并重复内容</p>
        </div>

        <div className="kg-manage-columns">

          <div className="kg-manage-col kg-manage-col-current">
            <h3 className="kg-manage-col-title">当前节点</h3>
            <div className="kg-manage-field kg-manage-field-col">
              {/* 搜索框只负责挑选节点；改名在下面的「名称」里，两者分开，输入名称不会丢掉当前选中的节点 */}
              <div className="kg-manage-input-wrap">
                <input
                  type="text"
                  value={manageSearchQuery}
                  onChange={(e) => setManageSearchQuery(e.target.value)}
                  disabled={loading}
                  className="kg-manage-input"
                  placeholder="搜索要管理的节点..."
                />
                <span className="kg-manage-search-icon">🔍</span>
                {manageSearchQuery && nodeSuggestionList && nodeSuggestionList.length > 0 && (
                  <div className="kg-filter-suggestions kg-manage-suggestions">
                    {nodeSuggestionList.map((term) => (
                      <button
                        key={term.id}
                        type="button"
                        className="kg-filter-suggestion-item kg-manage-suggestion-item"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => {
                          setManageNodeId(term.id)
                          setManageLabel(formatDisplayLabel(term.label))
                          setManageDescription(term.description || '')
                          setManageSearchQuery('')
                        }}
                      >
                        <div className="kg-manage-suggestion-title">{formatDisplayLabel(term.label)}</div>
                        {term.description && (
                          <div className="kg-manage-suggestion-desc">
                            {term.description}
                          </div>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <label className="kg-manage-name-label" htmlFor="kg-manage-name-input">名称</label>
              <input
                id="kg-manage-name-input"
                type="text"
                value={manageLabel}
                onChange={(e) => setManageLabel(e.target.value)}
                disabled={!manageNodeId || loading}
                maxLength={255}
                className="kg-manage-input kg-manage-name-input"
                placeholder={manageNodeId ? '节点名称' : '先在上方选择一个节点'}
              />
            </div>
          </div>

          <div className="kg-manage-col kg-manage-col-description">
            <h3 className="kg-manage-col-title">描述</h3>
            <textarea
              value={manageDescription}
              onChange={(e) => setManageDescription(e.target.value)}
              disabled={!manageNodeId || loading}
              maxLength={2000}
              placeholder="输入节点描述..."
              className="kg-manage-textarea kg-manage-textarea-custom"
            />
          </div>

          <div className="kg-manage-col kg-manage-col-merge">
            <h3 className="kg-manage-col-title">合并到目标节点 <span className="kg-manage-col-title-optional">(可选)</span></h3>
            <div className="kg-manage-field kg-manage-field-col">
              <div className="kg-manage-input-wrap">
                <input
                  type="text"
                  placeholder="搜索其他节点..."
                  value={mergeSearchQuery}
                  onChange={(e) => {
                    setMergeSearchQuery(e.target.value)
                    if (!e.target.value) setMergeTargetId('')
                  }}
                  disabled={!manageNodeId || loading}
                  className="kg-manage-input"
                />
                <span className="kg-manage-search-icon">🔍</span>
              </div>

              {mergeTargetId && (
                <div className="kg-manage-merge-target-box">
                  <strong>将合并到:</strong>
                  <div className="kg-manage-merge-target-name">{mergeSearchQuery}</div>
                  <div className="kg-manage-merge-target-id">ID: {mergeTargetId}</div>
                  <button
                    onClick={() => {
                      setMergeTargetId('')
                      setMergeSearchQuery('')
                    }}
                    className="kg-manage-merge-cancel-btn"
                  >
                    取消合并
                  </button>
                </div>
              )}

              {mergeSuggestionList.length > 0 && (
                <div className="kg-filter-suggestions kg-manage-suggestions kg-manage-merge-suggestions">
                  {mergeSuggestionList.map((term) => (
                    <button
                      key={term.id}
                      type="button"
                      className="kg-filter-suggestion-item kg-manage-suggestion-item"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => {
                        setMergeTargetId(term.id)
                        setMergeSearchQuery(formatDisplayLabel(term.label))
                      }}
                    >
                      <div className="kg-manage-suggestion-title">{formatDisplayLabel(term.label)}</div>
                      <div className="kg-manage-suggestion-id">ID: {term.id}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="kg-manage-footer">
          <button
            className="kg-danger-btn"
            onClick={onDelete}
            disabled={loading || !manageNodeId}
          >
            删除节点
          </button>

          <div className="kg-manage-footer-actions">
            <button
              className="kg-secondary-btn"
              onClick={() => {
                onAiOrganize()
              }}
              disabled={loading}
            >
              整理建议
              {organizeCostLabel && <span className="kg-cost-badge">{organizeCostLabel}</span>}
            </button>

            {mergeTargetId && (
              <button
                className="kg-secondary-btn"
                onClick={onMerge}
                disabled={loading || !mergeTargetId}
              >
                合并到目标
              </button>
            )}

            <button
              className="kg-primary-btn"
              onClick={onSave}
              disabled={loading || !manageNodeId || !manageLabel.trim()}
            >
              保存修改
            </button>
          </div>
        </div>
      </div>
    </AnimatedModal>
  )
}

export default ManageKnowledgeDialog
