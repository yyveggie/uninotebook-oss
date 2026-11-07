import { motion as Motion } from 'framer-motion'
import { useState } from 'react'
import './ExpandDialog.css'

function ExpandDialog({ node, onClose, onExpand }) {
  const [userFeedback, setUserFeedback] = useState('')

  const handleSubmit = () => {
    if (userFeedback.trim()) {
      onExpand(node, userFeedback)
      onClose()
    } else {
      alert('请输入您对该节点的扩展需求')
    }
  }

  return (
    <Motion.div
      className="expand-dialog"
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ type: 'tween', duration: 0.3 }}
    >
        <div className="expand-header">
          <div>
            <h2 className="expand-title">扩展节点</h2>
            <p className="expand-subtitle">{node.title}</p>
          </div>
        <button onClick={onClose} className="expand-close-button">
          ✕
        </button>
      </div>

      <div className="expand-content">
        <p className="expand-hint">
          将这个节点拆解成更多个学习步骤，其他节点保持不变：
        </p>
        <textarea
          className="expand-textarea"
          placeholder="例如：我想把这个节点学得更深入，请帮我拆分成更详细的多个步骤，包含实践练习..."
          value={userFeedback}
          onChange={(e) => setUserFeedback(e.target.value)}
          rows={6}
          autoFocus
        />
        <div className="expand-buttons">
          <button
            className="expand-submit-button"
            onClick={handleSubmit}
          >
            开始扩展
          </button>
          <button
            className="expand-cancel-button"
            onClick={onClose}
          >
            取消
          </button>
        </div>
      </div>
    </Motion.div>
  )
}

export default ExpandDialog
