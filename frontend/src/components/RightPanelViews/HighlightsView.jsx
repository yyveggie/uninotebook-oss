import React from 'react'
import { FaListUl } from 'react-icons/fa'
import BackButton from '../BackButton'
import EmptyState from '../EmptyState'

const HighlightsView = ({
  highlights,
  tempHighlights,
  isGeneratingHighlights,
  onBack,
  onExplainHighlight
}) => {
  const displayHighlights = tempHighlights || highlights

  if (!displayHighlights) {
    if (isGeneratingHighlights) {
      return (
        <div className="empty-view" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '16px' }}>
          <div className="spinner-icon" style={{
            width: '32px',
            height: '32px',
            border: '3px solid #e5e7eb',
            borderTopColor: '#1a1a1a',
            borderRadius: '50%',
            animation: 'spin 1s linear infinite'
          }}></div>
          <p style={{ color: '#6b7280', fontSize: '14px' }}>正在生成重点提炼，请稍候...</p>
        </div>
      )
    }
    return (
      <EmptyState
        icon={<FaListUl />}
        text="暂无重点提炼数据"
        subtext="点击左侧生成按钮开始创建"
      />
    )
  }

  const { summary, key_points } = displayHighlights

  const groupedPoints = key_points.reduce((acc, point) => {
    const category = point.category || '其他'
    if (!acc[category]) {
      acc[category] = []
    }
    acc[category].push(point)
    return acc
  }, {})

  const categoryPrefix = {
    '关键要点': '▪',
    '核心创新': '▪',
    '注意事项': '▪',
    '细节设置': '▪',
    '其他': '▪'
  }

  const importanceLabels = {
    '高': '重要',
    '中': '一般',
    '低': '次要'
  }

  return (
    <div className="highlights-view">
      <div className="view-header">
        <BackButton onClick={onBack} />
        <h3>重点提炼</h3>
      </div>
      <div className="view-content">
        <h4 className="highlights-section-title">
          <span className="category-icon">▪</span>
          核心摘要
        </h4>
        <div className="highlights-summary">
          <p>{summary}</p>
        </div>

        <div className="highlights-points">
          {Object.entries(groupedPoints).map(([category, points]) => (
            <div key={category} className="points-category">
              <h4 className="category-title">
                <span className="category-icon">{categoryPrefix[category] || '▪'}</span>
                {category}
                <span className="category-count">({points.length})</span>
              </h4>
              <div className="points-list">
                {points.map((point, index) => (
                  <div key={index} className="highlight-point">
                    <div className="point-header">
                      <h5 className="point-title">{point.title}</h5>
                      <div className="point-header-right">
                        <span className="point-importance">
                          {importanceLabels[point.importance] || point.importance}
                        </span>
                        <button
                          className="point-explain-btn"
                          onClick={() => onExplainHighlight(point)}
                          title="详细解释此要点"
                        >
                          详细解释
                        </button>
                      </div>
                    </div>
                    <p className="point-content">{point.content}</p>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default HighlightsView