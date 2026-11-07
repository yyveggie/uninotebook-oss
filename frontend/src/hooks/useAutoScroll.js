
import { useEffect, useRef, useState } from 'react'

/**
 * 自动滚动 Hook
 * @param {Array} dependencies - 触发滚动的依赖项数组 (通常是消息列表)
 * @param {boolean} shouldScroll - 是否应该滚动 (例如：是否正在流式传输或加载中)
 * @returns {Object} { scrollRef, scrollToBottom, isAtBottom }
 */
export const useAutoScroll = (dependencies = [], shouldScroll = true) => {
    const scrollRef = useRef(null)
    const [isAtBottom, setIsAtBottom] = useState(true)

    // 滚动到底部函数
    const scrollToBottom = (behavior = 'smooth') => {
        if (scrollRef.current) {
            scrollRef.current.scrollTo({
                top: scrollRef.current.scrollHeight,
                behavior
            })
        }
    }

    // 依赖项变化时自动滚动
    useEffect(() => {
        if (shouldScroll) {
            // 使用 requestAnimationFrame 确保 DOM 已更新
            requestAnimationFrame(() => {
                scrollToBottom('smooth')
            })
        }
    }, dependencies)

    // 监听滚动事件，判断是否在底部 (可选，用于显示"回到底部"按钮)
    useEffect(() => {
        const handleScroll = () => {
            if (!scrollRef.current) return
            const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
            const distance = scrollHeight - scrollTop - clientHeight
            setIsAtBottom(distance < 50)
        }

        const element = scrollRef.current
        if (element) {
            element.addEventListener('scroll', handleScroll)
            return () => element.removeEventListener('scroll', handleScroll)
        }
    }, [])

    return { scrollRef, scrollToBottom, isAtBottom }
}
