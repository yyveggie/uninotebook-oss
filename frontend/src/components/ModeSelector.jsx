import { motion as Motion } from 'framer-motion'
import './ModeSelector.css'

function ModeSelector({ onSelectMode }) {
  return (
    <Motion.div
      className="mode-selector-container"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6 }}
    >
      <div className="mode-selector-header">
        <h1 className="mode-selector-title">UniNotebook</h1>
        <p className="mode-selector-subtitle">选择你的学习方式</p>
      </div>

      <div className="mode-selector-cards">
        {/* 主题搜索模式 */}
        <Motion.div
          className="mode-card"
          whileHover={{ scale: 1.02, y: -4 }}
          whileTap={{ scale: 0.98 }}
          onClick={() => onSelectMode('topic')}
        >
          <div className="mode-card-icon">🔍</div>
          <h3 className="mode-card-title">主题搜索</h3>
          <p className="mode-card-description">
            输入任何学习主题
            <br />
            AI 为你生成完整学习路径
          </p>
          <div className="mode-card-footer">
            <span className="mode-card-badge">推荐新手</span>
          </div>
        </Motion.div>

        {/* 文档学习模式 */}
        <Motion.div
          className="mode-card mode-card-highlight"
          whileHover={{ scale: 1.02, y: -4 }}
          whileTap={{ scale: 0.98 }}
          onClick={() => onSelectMode('document')}
        >
          <div className="mode-card-icon">📄</div>
          <h3 className="mode-card-title">文档学习</h3>
          <p className="mode-card-description">
            上传 PDF 或粘贴文本
            <br />
            AI 分析内容定制路径
          </p>
          <div className="mode-card-footer">
            <span className="mode-card-badge mode-card-badge-new">NEW</span>
          </div>
        </Motion.div>
      </div>

      <div className="mode-selector-footer">
        <p className="mode-selector-hint">
          两种模式都支持 AI 深度解释、节点扩展等功能
        </p>
      </div>
    </Motion.div>
  )
}

export default ModeSelector

