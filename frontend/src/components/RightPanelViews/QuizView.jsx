import React, { useEffect, useState } from 'react'
import { FaBrain } from 'react-icons/fa'
import BackButton from '../BackButton'
import EmptyState from '../EmptyState'
import { MarkdownRenderer } from '../MarkdownRenderer'
import { SkeletonListItem } from '../Skeleton'

const formatDate = (dateString) => {
  if (!dateString) return ''
  const date = new Date(dateString)
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit'
  })
}

const QuizView = ({
  quiz,
  tempQuiz,
  isGeneratingQuiz,
  onViewChange,
  showHistory,
  setShowHistory,
  loadingHistory,
  quizHistory,
  selectedQuizId,
  loadQuizVersion,
  onSendQuestionToChat
}) => {
  const [currentQuizIndex, setCurrentQuizIndex] = useState(0)
  const [userAnswers, setUserAnswers] = useState({})

  useEffect(() => {
    setUserAnswers({})
    setCurrentQuizIndex(0)
  }, [quiz, tempQuiz])

  const rawQuiz = tempQuiz || quiz
  const displayQuiz = Array.isArray(rawQuiz)
    ? rawQuiz
    : Array.isArray(rawQuiz?.quiz)
      ? rawQuiz.quiz
      : []

  if (!displayQuiz || displayQuiz.length === 0) {
    if (isGeneratingQuiz) {
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
          <p style={{ color: '#6b7280', fontSize: '14px' }}>正在生成测验，请稍候...</p>
        </div>
      )
    }

    return (
      <div className="quiz-view">
        <div className="view-header">
          <BackButton onClick={() => onViewChange('studio')} />
          <h3>推理测验</h3>
        </div>
        <EmptyState
          icon={<FaBrain />}
          text="暂无测验数据"
          subtext="请先生成，或从历史版本中选择可用内容"
        />
      </div>
    )
  }

  const currentQuestion = displayQuiz[currentQuizIndex] || displayQuiz[0]
  const totalQuestions = displayQuiz.length
  const hasAnswered = userAnswers[currentQuizIndex] !== undefined

  const handleOptionSelect = (questionIndex, optionIndex) => {
    setUserAnswers(prev => ({
      ...prev,
      [questionIndex]: optionIndex
    }))
  }

  const handleNextQuestion = () => {
    if (currentQuizIndex < totalQuestions - 1) {
      setCurrentQuizIndex(prev => prev + 1)
    } else {
      alert('测验完成！')
    }
  }

  const handlePrevQuestion = () => {
    if (currentQuizIndex > 0) {
      setCurrentQuizIndex(prev => prev - 1)
    }
  }

  const optionLabels = ['A', 'B', 'C', 'D', 'E', 'F']

  const handleExplain = () => {
    if (!onSendQuestionToChat) return

    const userIdx = userAnswers[currentQuizIndex]
    const correctIdx = currentQuestion.correct_answer
    const userOptionText = currentQuestion.options[userIdx]
    const correctOptionText = currentQuestion.options[correctIdx]
    const isCorrect = userIdx === correctIdx

    let prompt = `我在做测验时遇到了这个问题：\n\n**${currentQuestion.question}**\n\n`

    if (userIdx !== undefined) {
      prompt += `我选择的答案是：**${optionLabels[userIdx]}. ${userOptionText}**\n`
      if (!isCorrect) {
        prompt += `\n正确答案是：**${optionLabels[correctIdx]}. ${correctOptionText}**\n\n`
        prompt += '请解释为什么我选错了，以及为什么正确答案是这个。'
      } else {
        prompt += '\n我答对了，但我想深入了解一下这个知识点。'
      }
    } else {
      prompt += '我还没有回答，请帮我解析一下这个问题。'
    }

    onSendQuestionToChat(prompt)
  }

  return (
    <div className="quiz-view quiz-single-mode">
      <div className="view-header">
        <BackButton onClick={() => onViewChange('studio')} />
        <h3>推理测验</h3>
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
            ) : quizHistory.length === 0 ? (
              <div className="history-empty">暂无笔记</div>
            ) : (
              quizHistory.map((item) => (
                <div
                  key={item.id}
                  className={`history-item ${selectedQuizId === item.id ? 'active' : ''} ${item.is_current ? 'current' : ''}`}
                  onClick={() => loadQuizVersion(item.id)}
                >
                  <div className="history-item-title">
                    版本 {item.version}
                    {item.is_current && <span className="current-badge">当前</span>}
                  </div>
                  <div className="history-item-meta">
                    <span>{item.question_count} 道题</span>
                    <span>{formatDate(item.created_at)}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      <div className="quiz-content-single">
        <div className="quiz-progress-indicator">
          {currentQuizIndex + 1} / {totalQuestions}
        </div>

        <div className="quiz-question-text">
          <MarkdownRenderer
            components={{
              p: ({ ...props }) => <p style={{ margin: 0, display: 'inline' }} {...props} />
            }}
          >
            {currentQuestion.question}
          </MarkdownRenderer>
        </div>

        <div className="quiz-options-list">
          {currentQuestion.options.map((option, idx) => {
            const isSelected = userAnswers[currentQuizIndex] === idx
            const isCorrect = idx === currentQuestion.correct_answer
            const isUserWrong = isSelected && !isCorrect

            let optionClass = 'quiz-option-item'
            if (hasAnswered) {
              if (isCorrect) optionClass += ' correct'
              if (isUserWrong) optionClass += ' wrong'
              if (!isSelected && !isCorrect) optionClass += ' disabled'
            } else if (isSelected) {
              optionClass += ' selected'
            }

            return (
              <div
                key={idx}
                className={optionClass}
                onClick={() => !hasAnswered && handleOptionSelect(currentQuizIndex, idx)}
              >
                <div className="quiz-option-content">
                  <span className="quiz-option-label">{optionLabels[idx]}</span>
                  <span className="quiz-option-text">
                    <MarkdownRenderer
                      components={{
                        p: ({ ...props }) => <span {...props} />
                      }}
                    >
                      {option}
                    </MarkdownRenderer>
                  </span>
                </div>

                {hasAnswered && isCorrect && (
                  <div className="quiz-status-text correct">✓ 正确答案</div>
                )}
                {hasAnswered && isUserWrong && (
                  <div className="quiz-status-text wrong">✕ 不太对</div>
                )}
              </div>
            )
          })}
        </div>

        {hasAnswered && (
          <div className="quiz-explanation-card" style={{
            marginTop: '24px',
            padding: '16px',
            backgroundColor: '#f8f9fa',
            borderRadius: '8px',
            border: '1px solid #e9ecef'
          }}>
            <div style={{
              fontSize: '14px',
              fontWeight: '600',
              marginBottom: '8px',
              color: '#495057'
            }}>解析</div>
            <div style={{
              fontSize: '14px',
              lineHeight: '1.6',
              color: '#212529'
            }}>
              <MarkdownRenderer>
                {currentQuestion.explanation || '暂无详细解析。'}
              </MarkdownRenderer>
            </div>
          </div>
        )}
      </div>

      <div className="quiz-footer">
        <button className="quiz-explain-btn" onClick={handleExplain}>
          <span className="icon">💬</span> 解释
        </button>
        <div style={{ display: 'flex', gap: '12px' }}>
          {currentQuizIndex > 0 && (
            <button className="quiz-prev-btn" onClick={handlePrevQuestion}>
              上一个
            </button>
          )}
          <button className="quiz-next-btn" onClick={handleNextQuestion}>
            {currentQuizIndex < totalQuestions - 1 ? '下一个' : '完成'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default QuizView