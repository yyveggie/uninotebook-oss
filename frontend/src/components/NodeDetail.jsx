import { motion as Motion } from 'framer-motion'
import './NodeDetail.css'

function NodeDetail({ node, onClose, isCompleted, isDocumentMode }) {
  const getResourceTypeIcon = (type) => {
    const icons = {
      video: '🎥',
      article: '📄',
      course: '🎓',
      book: '📚',
      documentation: '📖',
    }
    return icons[type] || '🔗'
  }

  const getDifficultyLabel = (difficulty) => {
    const labels = {
      beginner: '初级',
      intermediate: '中级',
      advanced: '高级',
    }
    return labels[difficulty] || difficulty
  }

  return (
    <Motion.div
      className="detail-panel"
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ type: 'tween', duration: 0.3 }}
    >
        <div className="detail-header">
          <div>
            <h2 className="node-title">
              {isCompleted && <span className="completed-check">✓ </span>}
              {node.title}
            </h2>
          </div>
          <button onClick={onClose} className="close-button">
            ✕
          </button>
        </div>

        <div className="detail-content">
          {/* 描述 */}
          <section className="detail-section">
            <h3>节点描述</h3>
            <p className="description">{node.description}</p>
          </section>

          {/* 文档学习模式下不显示学习时长、学习目标、学习资源 */}
          {!isDocumentMode && (
            <>
              {/* 学习时长 */}
              <section className="detail-section">
                <h3>预计学习时长</h3>
                <p className="time-estimate">{node.estimated_hours} 小时</p>
              </section>

              {/* 学习目标 */}
              <section className="detail-section">
                <h3>学习目标</h3>
                <ul className="goals-list">
                  {node.learning_goals.map((goal, index) => (
                    <Motion.li
                      key={index}
                      initial={{ opacity: 0, x: -20 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: index * 0.1 }}
                    >
                      {goal}
                    </Motion.li>
                  ))}
                </ul>
              </section>

              {/* 学习资源 */}
              <section className="detail-section">
                <h3>学习资源</h3>
                <div className="resources-list">
                  {node.resources.map((resource, index) => (
                    <Motion.a
                      key={index}
                      href={resource.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="resource-card"
                      initial={{ opacity: 0, y: 20 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: index * 0.1 }}
                      whileHover={{ scale: 1.02, y: -4 }}
                      whileTap={{ scale: 0.98 }}
                    >
                      <div className="resource-header">
                        <span className="resource-icon">
                          {getResourceTypeIcon(resource.type)}
                        </span>
                        <span className="resource-type">{resource.type}</span>
                        <span className={`difficulty-badge ${resource.difficulty}`}>
                          {getDifficultyLabel(resource.difficulty)}
                        </span>
                      </div>
                      <h4 className="resource-title">{resource.title}</h4>
                      <p className="resource-url">{resource.url}</p>
                    </Motion.a>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
    </Motion.div>
  )
}

export default NodeDetail


