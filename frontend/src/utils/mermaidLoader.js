let mermaidPromise = null
let mermaidInitialized = false

const getIdleCallback = () => {
  if (typeof window === 'undefined') return null
  return typeof window.requestIdleCallback === 'function'
    ? window.requestIdleCallback.bind(window)
    : null
}

const canPrefetchByNetwork = () => {
  if (typeof navigator === 'undefined') return true

  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection
  if (!connection) return true

  if (connection.saveData) return false

  const effectiveType = (connection.effectiveType || '').toLowerCase()
  if (effectiveType === 'slow-2g' || effectiveType === '2g') return false

  return true
}

export const getMermaid = async () => {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then(module => module.default || module)
  }

  const mermaid = await mermaidPromise

  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      // 图表源码来自 LLM 输出 / 文档内容，属于不可信输入：
      // strict 会对生成的 SVG 做 DOMPurify 净化并禁用 click 回调，渲染结果才能安全地插入页面
      securityLevel: 'strict',
      fontFamily: 'Inter, sans-serif',
      flowchart: {
        useMaxWidth: false,
        htmlLabels: true,
        curve: 'basis',
      },
    })

    mermaid.parseError = (err) => {
      console.debug('Mermaid Parse Error (Suppressed):', err)
    }

    mermaidInitialized = true
  }

  return mermaid
}

export const prefetchMermaid = () => {
  if (typeof window === 'undefined') return () => {}
  if (mermaidPromise) return () => {}
  if (!canPrefetchByNetwork()) return () => {}

  const idleCallback = getIdleCallback()

  if (idleCallback) {
    const idleId = idleCallback(() => {
      getMermaid().catch(() => {
      })
    }, { timeout: 2500 })

    return () => {
      if (typeof window.cancelIdleCallback === 'function') {
        window.cancelIdleCallback(idleId)
      }
    }
  }

  const timeoutId = window.setTimeout(() => {
    getMermaid().catch(() => {
    })
  }, 600)

  return () => {
    window.clearTimeout(timeoutId)
  }
}
