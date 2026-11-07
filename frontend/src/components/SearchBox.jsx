import { useState, useEffect } from 'react'
import { motion as Motion } from 'framer-motion'
import './SearchBox.css'

function SearchBox({ onSearch }) {
  const [topic, setTopic] = useState('')
  const [currentLevel, setCurrentLevel] = useState('beginner')
  const [learningGoal, setLearningGoal] = useState('')
  const [languagePreference, setLanguagePreference] = useState('chinese')
  const [isExpanded, setIsExpanded] = useState(false)
  const [placeholderText, setPlaceholderText] = useState('我想学习...')
  const [currentExampleIndex, setCurrentExampleIndex] = useState(0)
  const [hasStartedTyping, setHasStartedTyping] = useState(false)

  // 示例文本列表
  const examples = [
    '我想学习微积分',
    '我想学习Python编程',
    '我想学习机器学习',
    '我想学习Web开发',
    '我想学习数据分析',
    '我想学习UI设计'
  ]

  // 打字机效果 - 只在首次加载时运行
  useEffect(() => {
    if (hasStartedTyping || isExpanded || topic) return // 如果已经运行过、已展开或用户已输入，停止动画

    setHasStartedTyping(true)

    let currentText = ''
    let currentIndex = 0
    let isDeleting = false
    let timeoutId

    const type = () => {
      const currentExample = examples[currentExampleIndex]
      
      if (!isDeleting) {
        // 打字
        currentText = currentExample.substring(0, currentIndex + 1)
        currentIndex++
        
        if (currentIndex === currentExample.length) {
          // 打完了，等待2秒后开始删除
          timeoutId = setTimeout(() => {
            isDeleting = true
            type()
          }, 2000)
          setPlaceholderText(currentText)
          return
        }
      } else {
        // 删除
        currentText = currentExample.substring(0, currentIndex - 1)
        currentIndex--
        
        if (currentIndex === 0) {
          // 删完了，切换到下一个例子
          isDeleting = false
          setCurrentExampleIndex((prev) => (prev + 1) % examples.length)
          timeoutId = setTimeout(type, 500)
          setPlaceholderText(currentText)
          return
        }
      }
      
      setPlaceholderText(currentText)
      timeoutId = setTimeout(type, isDeleting ? 50 : 100)
    }

    timeoutId = setTimeout(type, 500)

    return () => clearTimeout(timeoutId)
  }, [currentExampleIndex, isExpanded, topic])

  const handleSubmit = (e) => {
    e.preventDefault()
    
    if (topic.trim()) {
      try {
        onSearch(topic.trim(), currentLevel, learningGoal.trim(), languagePreference)
      } catch (error) {
        void error;
      }
    }
  }

  const handleInputFocus = () => {
    setIsExpanded(true)
  }

  const handleCollapse = () => {
    setIsExpanded(false)
    setPlaceholderText('我想学习...')
  }

  return (
    <Motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.6 }}
      className="search-box"
    >
      <form onSubmit={handleSubmit}>
        <div className="input-group">
          <input
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            onFocus={handleInputFocus}
            placeholder={placeholderText || '我想学习...'}
            className="search-input"
          />
        </div>

        {isExpanded && (
          <>
            <button
              type="button"
              className="collapse-button"
              onClick={handleCollapse}
            >
              ✕
            </button>
            <Motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ 
                duration: 0.4,
                ease: [0.4, 0, 0.2, 1]
              }}
              className="advanced-options"
            >
              <div className="option-group">
                <label>当前水平</label>
                <div className="level-buttons">
                  {['beginner', 'intermediate', 'advanced'].map((level) => (
                    <button
                      key={level}
                      type="button"
                      onClick={() => setCurrentLevel(level)}
                      className={`level-button ${
                        currentLevel === level ? 'active' : ''
                      }`}
                    >
                      {level === 'beginner'
                        ? '初学者'
                        : level === 'intermediate'
                        ? '中级'
                        : '高级'}
                    </button>
                  ))}
                </div>
              </div>

              <div className="option-group">
                <label>资源语言</label>
                <div className="level-buttons">
                  <button
                    type="button"
                    onClick={() => setLanguagePreference('chinese')}
                    className={`level-button ${
                      languagePreference === 'chinese' ? 'active' : ''
                    }`}
                  >
                    中文
                  </button>
                  <button
                    type="button"
                    onClick={() => setLanguagePreference('english')}
                    className={`level-button ${
                      languagePreference === 'english' ? 'active' : ''
                    }`}
                  >
                    英文
                  </button>
                </div>
              </div>

              <div className="option-group">
                <label>学习目标（可选）</label>
                <input
                  type="text"
                  value={learningGoal}
                  onChange={(e) => setLearningGoal(e.target.value)}
                  placeholder="例如：找到工作、完成项目、兴趣学习..."
                  className="goal-input"
                />
              </div>
            </Motion.div>

            <button
              type="submit"
              className="search-button"
              disabled={!topic.trim()}
            >
              开始学习之旅
            </button>
          </>
        )}
      </form>
    </Motion.div>
  )
}

export default SearchBox


