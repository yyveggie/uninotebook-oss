import React, { useState } from 'react'
import { createPortal } from 'react-dom'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import './ConfirmDialog.css'

function ConfirmDialog({
  isOpen,
  type = 'confirm', // 'confirm' | 'alert'
  title = '确认操作',
  message,
  confirmLabel = '确定',
  cancelLabel = '取消',
  showDontRemind = false,
  onConfirm,
  onCancel,
  // 点遮罩关闭时调用；不传则与「取消」相同。三选一的场景（如「收藏并关闭 / 不收藏 / 返回」）
  // 需要把「点空白处」和「取消按钮」区分开，避免误触遮罩就执行了取消按钮的动作
  onDismiss
}) {
  const [dontRemind, setDontRemind] = useState(false)

  if (!isOpen) return null

  const handleConfirm = () => {
    onConfirm(dontRemind)
  }

  // 挂到 body 上：页面容器（如 .home-page）有自己的 z-index 层叠上下文，
  // 内联渲染时确认框会被同样挂在 body 上的弹窗（AnimatedModal）盖住，看得见却点不到
  return createPortal(
    <AnimatePresence>
      <div className="confirm-overlay" onClick={type === 'alert' ? undefined : (onDismiss || onCancel)}>
        <Motion.div
          className="confirm-modal"
          initial={{ opacity: 0, scale: 0.95, y: 10 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 10 }}
          transition={{ duration: 0.2 }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="confirm-content">
            <h3 className="confirm-title">{title}</h3>
            <p className="confirm-message">{message}</p>
          </div>

          {showDontRemind && (
            <div className="confirm-checkbox-wrapper">
              <label className="confirm-checkbox-label">
                <input
                  type="checkbox"
                  checked={dontRemind}
                  onChange={(e) => setDontRemind(e.target.checked)}
                />
                <span>不再提醒</span>
              </label>
            </div>
          )}

          <div className="confirm-actions">
            {type !== 'alert' && (
              <button className="confirm-btn-cancel" onClick={onCancel}>
                {cancelLabel}
              </button>
            )}
            <button
              className="confirm-btn-submit"
              onClick={handleConfirm}
            >
              {confirmLabel}
            </button>
          </div>
        </Motion.div>
      </div>
    </AnimatePresence>,
    document.body
  )
}

export default ConfirmDialog
