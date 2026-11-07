import { useEffect } from 'react'
import { getMermaid } from '../utils/mermaidLoader'

const isMermaidErrorSvg = (el) =>
    el?.tagName?.toLowerCase() === 'svg' && el.getAttribute('aria-roledescription') === 'error'

/**
 * 🔧 全局清理：移除 Mermaid 渲染失败后残留在 body 下的错误容器
 *
 * mermaid.render() 成功时会自行删除临时容器；解析 / 绘制出错时则把错误图
 * （svg[aria-roledescription="error"]）画进 body 下的 `d${id}` 容器后直接抛错，不做清理。
 * 因此只删这类 Mermaid 自己的错误容器。以前按文本内容匹配「Syntax error … mermaid」——
 * #root 只要显示过这类文字就会被整个删掉、应用随即白屏；按 id 前缀宽泛匹配
 * 还会误删其它实例正在渲染中的临时容器。
 */
export const cleanupOrphanedMermaidElements = () => {
    if (typeof document === 'undefined') return

    const root = document.getElementById('root')

    document.querySelectorAll('body > div, body > svg').forEach(el => {
        if (el === root || (root && el.contains(root))) return

        const id = el.id || ''
        if (isMermaidErrorSvg(el) || (id.startsWith('d') && isMermaidErrorSvg(el.firstElementChild))) {
            el.remove()
        }
    })
}

/**
 * Mermaid 补丁 Hook
 * 负责全局 Mermaid 配置、错误处理覆盖和 DOM 清理
 */
export const useMermaidPatch = () => {
    useEffect(() => {
        let isMounted = true

        void getMermaid().catch((error) => {
            if (isMounted) {
                console.warn('Mermaid runtime load failed:', error)
            }
        })

        // 立即清理一次
        cleanupOrphanedMermaidElements()

        // 监听 DOMContentLoaded（如果是初次加载）
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', cleanupOrphanedMermaidElements)
        }

        // 组件卸载时也尝试清理一次
        return () => {
            isMounted = false
            cleanupOrphanedMermaidElements()
            document.removeEventListener('DOMContentLoaded', cleanupOrphanedMermaidElements)
        }
    }, [])
}
