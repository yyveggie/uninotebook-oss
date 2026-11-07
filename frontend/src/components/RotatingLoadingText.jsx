import { useState, useEffect, useRef } from 'react'

// 加载提示标语集合 — 随机轮换，每条停留 5 秒
const LOADING_MESSAGES = [
  '正在处理中...٩(๑•̀ω•́๑)۶',
  '请保持当前页面，不要刷新或返回...(ﾟдﾟ≡ﾟдﾟ)',
  '这可能需要几十秒到几分钟...(¦3[▓▓]',
  '正在努力阅读你的文档... ᕙ(⇀‸↼‶)ᕗ',
  '泡杯咖啡，马上就好... ☕(´,,•ω•,,)',
  '正在将文字转化为知识... (ง •̀_•́)ง',
  '复杂的文档需要更多耐心哦... (๑•̀ㅂ•́)و✧',
  '快了快了，再等一小会儿... ₍ᐢ..ᐢ₎♡',
  '正在拆解文档结构... ✂️(•̀ᴗ•́)و',
  '每一段文字都不会被遗漏... 📖(ˊᗜˋ*)',
]

/**
 * 随机轮换显示加载提示文字
 * @param {string} className - 自定义 CSS 类名
 * @param {object} style - 自定义内联样式
 */
export default function RotatingLoadingText({ className = 'loading-text', style = {} }) {
  const [text, setText] = useState('')
  const [fade, setFade] = useState(true)
  const usedRef = useRef(new Set())

  // 从剩余未使用的消息中随机选一条（全部用完后重置）
  const pickRandom = () => {
    if (usedRef.current.size >= LOADING_MESSAGES.length) {
      usedRef.current.clear()
    }
    const remaining = LOADING_MESSAGES.filter((_, i) => !usedRef.current.has(i))
    const idx = Math.floor(Math.random() * remaining.length)
    const globalIdx = LOADING_MESSAGES.indexOf(remaining[idx])
    usedRef.current.add(globalIdx)
    return remaining[idx]
  }

  useEffect(() => {
    // 首次立即显示
    setText(pickRandom())

    const interval = setInterval(() => {
      setFade(false) // 开始淡出
      setTimeout(() => {
        setText(pickRandom())
        setFade(true) // 淡入新文字
      }, 300)
    }, 5000) // 每 5 秒切换一次

    return () => clearInterval(interval)
  }, [])

  return (
    <p
      className={`${className} rotating-loading-text ${fade ? 'is-visible' : 'is-hidden'}`}
      style={Object.keys(style).length ? style : undefined}
    >
      {text}
    </p>
  )
}
