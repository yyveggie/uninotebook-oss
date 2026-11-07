import { Component } from 'react'
import './ErrorBoundary.css'

/**
 * 页面级错误边界：某个页面渲染出错时只替换这一块，不让整个应用白屏。
 * resetKey 变化（例如切换路由）时自动恢复。
 */
class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('页面渲染出错', error, info?.componentStack)
  }

  componentDidUpdate(prevProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="error-boundary" role="alert">
        <div className="error-boundary-card">
          <h2 className="error-boundary-title">页面出了点问题</h2>
          <p className="error-boundary-text">这一页暂时无法显示，其他页面不受影响。可以重试，或刷新页面。</p>
          <div className="error-boundary-actions">
            <button type="button" className="error-boundary-btn" onClick={() => this.setState({ error: null })}>
              重试
            </button>
            <button type="button" className="error-boundary-btn primary" onClick={() => window.location.reload()}>
              刷新页面
            </button>
          </div>
        </div>
      </div>
    )
  }
}

export default ErrorBoundary
