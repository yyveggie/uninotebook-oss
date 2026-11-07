import { cloneElement, isValidElement, useState } from 'react'
import { useCapability } from '../stores/runtimeStore'
import './CapabilityGate.css'

/**
 * 能力闸门
 *
 * 把任意功能入口包起来，条件不满足时自动置灰、拦截点击，并在悬停时说明
 * 「缺什么、怎么解决」——而不是让用户点下去撞一个 500。
 *
 * 用法：
 *   <CapabilityGate capability="poster" onConfigure={openSettings}>
 *     <button onClick={generatePoster}>生成海报</button>
 *   </CapabilityGate>
 *
 * 能力清单尚未加载时一律放行，避免把本可用的功能误置灰。
 */
export default function CapabilityGate({
  capability,
  children,
  onConfigure,
  // 'disable' 置灰原元素；'hide' 直接不渲染（用于本地版无意义的入口）
  mode = 'disable',
}) {
  // 订阅能力清单本身（而非只选出函数），清单加载 / 更新后才会重渲染
  const { available, reason } = useCapability(capability)
  const [hovering, setHovering] = useState(false)

  if (available) return children

  if (mode === 'hide') return null

  const canJumpToSettings = reason?.type === 'key' && typeof onConfigure === 'function'

  const disabledChild = isValidElement(children)
    ? cloneElement(children, {
        // 不设 disabled：禁用的按钮不触发点击与悬停事件，「点击前往设置」与原因提示都会失效
        'aria-disabled': true,
        tabIndex: -1,
        onClick: (e) => {
          e.preventDefault()
          e.stopPropagation()
          if (canJumpToSettings) onConfigure(reason.providers)
        },
      })
    : children

  return (
    <span
      className="capability-gate"
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
    >
      {disabledChild}
      {hovering && reason && (
        <span className="capability-gate__tip" role="tooltip">
          <span className="capability-gate__tip-text">{reason.text}</span>
          {reason.command && (
            <code className="capability-gate__tip-code">{reason.command}</code>
          )}
          {canJumpToSettings && (
            <span className="capability-gate__tip-action">点击前往设置</span>
          )}
        </span>
      )}
    </span>
  )
}
