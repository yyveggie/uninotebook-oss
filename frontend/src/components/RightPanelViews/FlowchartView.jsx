import React from 'react'
import BackButton from '../BackButton'
import { SkeletonListItem } from '../Skeleton'

const formatDate = (dateString) => {
  if (!dateString) return ''
  const date = new Date(dateString)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit'
  })
}

const FlowchartView = ({
  flowchart,
  tempFlowchart,
  onViewChange,
  showHistory,
  setShowHistory,
  loadingHistory,
  flowchartHistory,
  selectedFlowchartId,
  loadFlowchartVersion,
  expandedNodes,
  toggleNodeExpand,
  onExplainPathNode,
}) => {
  const displayFlowchart = tempFlowchart || flowchart

  if (!displayFlowchart || !displayFlowchart.nodes || displayFlowchart.nodes.length === 0) {
    return (
      <div className="empty-view">
        <p>暂无学习路径数据</p>
      </div>
    )
  }

  const { nodes, recommended_sequence } = displayFlowchart

  const sortedNodes = recommended_sequence
    ? recommended_sequence.map(id => nodes.find(n => n.id === id)).filter(Boolean)
    : nodes

  const displayNodes = sortedNodes.length > 0 ? sortedNodes : nodes

  return (
    <div className="flowchart-view">
      <div className="view-header">
        <BackButton onClick={() => onViewChange('studio')} />
        <h3>学习路径</h3>
      </div>

      {showHistory && (
        <div className="history-panel">
          <div className="history-header">
            <h4>历史版本</h4>
            <button className="history-close-btn" onClick={() => setShowHistory(false)}>✕</button>
          </div>
          <div className="history-list">
            {loadingHistory ? (
              <div className="history-loading"><SkeletonListItem lines={2} /><SkeletonListItem lines={2} /></div>
            ) : flowchartHistory.length === 0 ? (
              <div className="history-empty">暂无笔记</div>
            ) : (
              flowchartHistory.map((item) => (
                <div
                  key={item.id}
                  className={`history-item ${selectedFlowchartId === item.id ? 'active' : ''} ${item.is_current ? 'current' : ''}`}
                  onClick={() => loadFlowchartVersion(item.id)}
                >
                  <div className="history-item-title">
                    版本 {item.version}
                    {item.is_current && <span className="current-badge">当前</span>}
                  </div>
                  <div className="history-item-meta">
                    <span>{item.node_count} 个节点</span>
                    <span>{formatDate(item.created_at)}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      <div className="view-content">
        <div className="flowchart-nodes">
          {displayNodes.map((node, index) => {
            const nodeKey = `flowchart-${node.id || index}`
            const isExpanded = expandedNodes.has(nodeKey)
            const text = node.description || ''
            const limit = 70
            const shouldTruncate = text.length > limit
            const displayText = (shouldTruncate && !isExpanded)
              ? text.slice(0, limit) + '...'
              : text

            return (
              <div
                key={node.id || index}
                className={`text-node ${isExpanded ? 'expanded' : ''}`}
                onClick={() => shouldTruncate && toggleNodeExpand(nodeKey)}
                style={{
                  cursor: shouldTruncate ? 'pointer' : 'default',
                  flexDirection: 'row',
                  alignItems: 'flex-start',
                  gap: '10px',
                  padding: '16px 12px'
                }}
              >
                <div className="text-node-number" style={{ marginTop: '2px' }}>
                  {index + 1}
                </div>

                <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
                  <h4 className="text-node-title" style={{ marginBottom: '4px' }}>
                    {node.title}
                  </h4>

                  <div style={{ marginBottom: '8px' }}>
                    <button
                      className="path-node-explain-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        onExplainPathNode(node, index)
                      }}
                      title="详细解释此节点"
                      style={{ marginLeft: 0 }}
                    >
                      详细解释
                    </button>
                  </div>

                  <p className="text-node-description" style={{ margin: 0 }}>
                    {displayText}
                    {shouldTruncate && !isExpanded && (
                      <span style={{ color: '#2196F3', fontSize: '12px', marginLeft: '4px', fontWeight: 500 }}>
                        展开
                      </span>
                    )}
                  </p>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default FlowchartView