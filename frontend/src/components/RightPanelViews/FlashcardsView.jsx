import React, { useState } from 'react'
import { motion as Motion, AnimatePresence } from 'framer-motion'
import { MarkdownRenderer } from '../MarkdownRenderer'
import BackButton from '../BackButton'
import EmptyState from '../EmptyState'
import { SkeletonListItem } from '../Skeleton'

const formatDate = (dateString) => {
  if (!dateString) return ''
  const date = new Date(dateString)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit'
  })
}

const FlashcardsView = ({
  flashcards,
  tempFlashcards,
  onViewChange,
  showHistory,
  setShowHistory,
  loadingHistory,
  flashcardsHistory,
  selectedFlashcardsId,
  loadFlashcardsVersion
}) => {
  const [currentCardIndex, setCurrentCardIndex] = useState(0)
  const [isFlipped, setIsFlipped] = useState(false)

  const rawFlashcards = tempFlashcards || flashcards
  const displayFlashcards = Array.isArray(rawFlashcards)
    ? rawFlashcards
    : Array.isArray(rawFlashcards?.flashcards)
      ? rawFlashcards.flashcards
      : []

  if (displayFlashcards.length === 0) {
    return (
      <div className="flashcards-view">
        <div className="view-header">
          <BackButton onClick={() => onViewChange('studio')} />
          <h3>闪卡</h3>
        </div>
        <EmptyState
          icon={<span>📇</span>}
          text="暂无闪卡数据"
          subtext="请先生成，或从历史版本中选择可用内容"
        />
      </div>
    )
  }

  const currentCard = displayFlashcards[currentCardIndex]
  if (!currentCard) return null

  const handleNextCard = () => {
    if (currentCardIndex < displayFlashcards.length - 1) {
      setIsFlipped(false)
      setTimeout(() => {
        setCurrentCardIndex(prev => prev + 1)
      }, 150)
    }
  }

  const handlePrevCard = () => {
    if (currentCardIndex > 0) {
      setIsFlipped(false)
      setTimeout(() => {
        setCurrentCardIndex(prev => prev - 1)
      }, 150)
    }
  }

  const handleFlipCard = () => {
    setIsFlipped(!isFlipped)
  }

  const handleRefreshCards = () => {
    setIsFlipped(false)
    setTimeout(() => {
      setCurrentCardIndex(0)
    }, 150)
  }

  return (
    <div className="flashcards-view">
      <div className="view-header">
        <BackButton onClick={() => onViewChange('studio')} />
        <h3>闪卡</h3>
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
            ) : flashcardsHistory.length === 0 ? (
              <div className="history-empty">暂无笔记</div>
            ) : (
              flashcardsHistory.map((item) => (
                <div
                  key={item.id}
                  className={`history-item ${selectedFlashcardsId === item.id ? 'active' : ''} ${item.is_current ? 'current' : ''}`}
                  onClick={() => loadFlashcardsVersion(item.id)}
                >
                  <div className="history-item-title">
                    版本 {item.version}
                    {item.is_current && <span className="current-badge">当前</span>}
                  </div>
                  <div className="history-item-meta">
                    <span>{item.card_count} 张卡片</span>
                    <span>{formatDate(item.created_at)}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      <div className="flashcards-content-panel">
        <div className="card-navigation">
          <button
            className="nav-btn-small prev"
            onClick={handlePrevCard}
            disabled={currentCardIndex === 0}
          >
            ←
          </button>

          <div className="card-counter">
            {currentCardIndex + 1} / {displayFlashcards.length}
          </div>

          <button
            className="nav-btn-small next"
            onClick={handleNextCard}
            disabled={currentCardIndex === displayFlashcards.length - 1}
          >
            →
          </button>
        </div>

        <AnimatePresence mode="wait">
          <Motion.div
            key={currentCardIndex}
            className={`flashcard-small ${isFlipped ? 'flipped' : ''}`}
            initial={{ opacity: 0, x: 50 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -50 }}
            transition={{ duration: 0.3 }}
            onClick={handleFlipCard}
          >
            <div className="card-inner-small">
              <div className="card-face-small card-front-small">
                <div className="card-text-small">
                  <MarkdownRenderer
                    components={{
                      p: ({ ...props }) => <p style={{ margin: 0, display: 'inline' }} {...props} />
                    }}
                  >
                    {currentCard.question}
                  </MarkdownRenderer>
                </div>
                <div className="card-hint-small">点击查看答案</div>
              </div>
              <div className="card-face-small card-back-small">
                <div className="card-text-small">
                  <MarkdownRenderer
                    components={{
                      p: ({ ...props }) => <p style={{ margin: 0, display: 'inline' }} {...props} />
                    }}
                  >
                    {currentCard.answer}
                  </MarkdownRenderer>
                </div>
              </div>
            </div>
          </Motion.div>
        </AnimatePresence>

        <div className="flashcards-controls">
          <button className="control-btn-small" onClick={handleRefreshCards} title="重新开始">
            ⟲
          </button>
          <div className="progress-bar-small">
            <div
              className="progress-fill-small"
              style={{ width: `${((currentCardIndex + 1) / displayFlashcards.length) * 100}%` }}
            ></div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default FlashcardsView
