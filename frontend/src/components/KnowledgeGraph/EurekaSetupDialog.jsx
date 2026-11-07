import { useState, useMemo } from 'react'
import AnimatedModal from '../ui/AnimatedModal'
import { formatDisplayLabel } from './utils'

// 与后端 EUREKA_MAX_NODES 一致：一次连接太多概念，灵感会变得空泛
const EUREKA_MAX_NODES = 6

const EurekaSetupDialog = ({ isOpen, onClose, onGenerate, allNodes, edges, costLabel }) => {
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [innovationType, setInnovationType] = useState('auto')

  const toggleSelection = (id) => {
    const next = new Set(selectedIds)
    if (next.has(id)) {
      next.delete(id)
    } else if (next.size < EUREKA_MAX_NODES) {
      next.add(id)
    }
    setSelectedIds(next)
  }
  const selectionFull = selectedIds.size >= EUREKA_MAX_NODES

  const filteredNodes = allNodes.filter(n => {
    const label = (n.label || '').toLowerCase()
    return label.includes(searchQuery.toLowerCase())
  })

  const displayNodes = filteredNodes.slice(0, 100)

  const existingConnections = useMemo(() => {
    if (selectedIds.size < 2) return []
    const ids = Array.from(selectedIds)
    const connections = []
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const id1 = ids[i]
        const id2 = ids[j]
        const hasEdge = edges.some(e => {
          const s = typeof e.source === 'object' ? e.source.id : e.source
          const t = typeof e.target === 'object' ? e.target.id : e.target
          return (s === id1 && t === id2) || (s === id2 && t === id1)
        })
        if (hasEdge) {
          const n1 = allNodes.find(n => n.id === id1)
          const n2 = allNodes.find(n => n.id === id2)
          if (n1 && n2) connections.push({ n1, n2 })
        }
      }
    }
    return connections
  }, [selectedIds, edges, allNodes])

  const handleAction = () => {
    if (selectedIds.size === 0) {
      onGenerate([], innovationType)
    } else if (selectedIds.size >= 2) {
      onGenerate(Array.from(selectedIds), innovationType)
    }
  }

  const innovationTypes = [
    { value: 'auto', label: '🎲 随机', desc: 'AI 自动选择最合适的类型' },
    { value: 'product', label: '💡 产品构想', desc: '一个创新的产品或服务' },
    { value: 'theory', label: '🧪 理论假设', desc: '一个可验证的科学假设' },
    { value: 'perspective', label: '👁 独特视角', desc: '一个新颖的看法或观点' },
    { value: 'paper', label: '📝 论文 Idea', desc: '一个学术研究方向' },
    { value: 'metaphor', label: '🎭 隐喻类比', desc: '一个深刻的隐喻或类比' },
  ]

  return (
    <AnimatedModal
      isOpen={isOpen}
      onClose={onClose}
      size="lg"
      width={1200}
      height="700px"
      className="kg-dialog kg-eureka-dialog kg-eureka-setup-modal"
    >

      <div className="kg-dialog-body kg-eureka-setup-body">
        <div className="kg-eureka-setup-header">
          <div className="kg-eureka-icon-wrapper kg-eureka-icon-wrapper-medium">
            <div className="kg-eureka-icon">🔭</div>
          </div>
          <h2 className="kg-eureka-title kg-eureka-title-medium">寻找灵感连接</h2>
          <p className="kg-loading-subtext kg-loading-subtext-setup">
            "创新往往源于看似无关概念的意外碰撞。"
          </p>
        </div>

        <div className="kg-eureka-setup-content">
          <div className="kg-eureka-type-section">
            <label className="kg-eureka-type-label">
              选择创新类型
            </label>
            <div className="kg-eureka-type-list">
              {innovationTypes.map(type => (
                <button
                  key={type.value}
                  onClick={() => setInnovationType(type.value)}
                  className={`kg-eureka-type-btn ${innovationType === type.value ? 'active' : ''}`}
                  title={type.desc}
                >
                  {type.label}
                </button>
              ))}
            </div>
          </div>

          <div className="kg-filter-input-wrapper kg-eureka-filter-wrap">
            <input
              type="text"
              className="kg-filter-input"
              placeholder="搜索节点..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              autoFocus
            />
          </div>

          {selectedIds.size > 0 && (
            <div className="kg-eureka-nodes kg-eureka-selected-nodes">
              {Array.from(selectedIds).map(id => {
                const node = allNodes.find(n => n.id === id)
                if (!node) return null
                return (
                  <span key={id} className="kg-eureka-node-tag clickable active" onClick={() => toggleSelection(id)}>
                    {formatDisplayLabel(node.label)} ✕
                  </span>
                )
              })}
            </div>
          )}

          {existingConnections.length > 0 && (
            <div className="kg-warning-box kg-warning-box-compact">
              <div className="kg-warning-box-row">
                <span>⚠️ 注意：以下节点之间已经存在连接</span>
              </div>
              <ul className="kg-warning-list">
                {existingConnections.map((c, i) => (
                  <li key={i}>
                    {formatDisplayLabel(c.n1.label)} ↔ {formatDisplayLabel(c.n2.label)}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="kg-eureka-candidate-list">
            <div className="kg-eureka-nodes">
              {displayNodes.map(node => {
                const isSelected = selectedIds.has(node.id)
                const isDisabled = !isSelected && selectionFull
                return (
                  <span
                    key={node.id}
                    className={`kg-eureka-node-tag clickable ${isSelected ? 'selected' : ''} ${isDisabled ? 'disabled' : ''}`}
                    title={isDisabled ? `最多选择 ${EUREKA_MAX_NODES} 个节点` : undefined}
                    onClick={() => toggleSelection(node.id)}
                    style={isSelected ? { background: '#0f766e', color: '#fff', borderColor: '#0f766e' } : {}}
                  >
                    {formatDisplayLabel(node.label)}
                  </span>
                )
              })}
              {filteredNodes.length > 100 && (
                <span className="kg-loading-subtext kg-eureka-more-hint">... 更多结果请搜索</span>
              )}
            </div>
          </div>
        </div>

        <div className="kg-eureka-footer kg-eureka-footer-plain">
          <div className="kg-eureka-footer-spacer">
            <span className="kg-loading-subtext">
              已选: {selectedIds.size} / {EUREKA_MAX_NODES} 个{selectionFull ? '（已达上限）' : ''}
            </span>
          </div>
          <button
            className="kg-primary-btn"
            onClick={handleAction}
            disabled={selectedIds.size === 1}
            style={selectedIds.size === 0 ? { background: 'linear-gradient(135deg, #f59e0b 0%, #d97706 100%)' } : {}}
          >
            {selectedIds.size === 0 ? '🎲 手气不错' : '✨ 生成灵感'}
            {costLabel && <span className="kg-cost-badge">{costLabel}</span>}
          </button>
        </div>
      </div>
    </AnimatedModal>
  )
}

export default EurekaSetupDialog
