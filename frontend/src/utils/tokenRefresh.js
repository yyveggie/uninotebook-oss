/**
 * Token自动刷新管理
 * 在用户活跃时自动刷新token，避免过期
 */

import { buildApiUrl, isLocalRuntime, setAccessToken } from './api'
import { requestScheduler } from './requestScheduler'

class TokenRefreshManager {
  constructor() {
    this.refreshTimer = null
    this.lastActivity = Date.now()
    this.refreshInterval = 10 * 60 * 1000 // 10分钟刷新一次
    this.activityCheckInterval = 1 * 60 * 1000 // 1分钟检查一次活动
    this.inactivityThreshold = 30 * 60 * 1000 // 30分钟无活动则停止刷新
  }

  /**
   * 启动自动刷新
   */
  start() {
    // 清除旧的定时器
    this.stop()

    // 监听用户活动
    this.setupActivityListeners()

    // 定时刷新token
    this.refreshTimer = setInterval(() => {
      this.checkAndRefresh()
    }, this.activityCheckInterval)
  }

  /**
   * 停止自动刷新
   */
  stop() {
    if (this.refreshTimer) {
      clearInterval(this.refreshTimer)
      this.refreshTimer = null
    }

    // 移除活动监听
    this.removeActivityListeners()
  }

  /**
   * 检查并刷新token
   */
  async checkAndRefresh() {
    // 检查用户是否活跃
    const timeSinceLastActivity = Date.now() - this.lastActivity
    
    // 如果超过30分钟无活动，停止刷新（节省资源）
    if (timeSinceLastActivity > this.inactivityThreshold) {
      return
    }

    // 如果距离上次活动不到10分钟，执行刷新
    if (timeSinceLastActivity < this.refreshInterval) {
      await this.refreshToken()
    }
  }

  /**
   * 刷新token
   */
  async refreshToken() {
    // 本地版没有会话可刷新（模式可能在定时器启动后才确定）
    if (isLocalRuntime()) {
      this.stop()
      return false
    }

    try {
      // 走 buildApiUrl：配置了 VITE_API_BASE_URL 时须请求后端所在域
      const response = await requestScheduler.schedule(buildApiUrl('/api/auth/refresh'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include' // 确保携带 Cookie
      }, { key: 'POST /api/auth/refresh' })

      if (response.ok) {
        const data = await response.json()
        if (data.access_token) {
          setAccessToken(data.access_token)
        }
        return true
      } else {
        return false
      }
    } catch (error) {
      void error;
      return false
    }
  }

  /**
   * 记录用户活动
   */
  recordActivity = () => {
    this.lastActivity = Date.now()
  }

  /**
   * 设置活动监听器
   */
  setupActivityListeners() {
    const events = ['mousedown', 'keydown', 'scroll', 'touchstart', 'click']
    
    events.forEach(event => {
      window.addEventListener(event, this.recordActivity, { passive: true })
    })
  }

  /**
   * 移除活动监听器
   */
  removeActivityListeners() {
    const events = ['mousedown', 'keydown', 'scroll', 'touchstart', 'click']
    
    events.forEach(event => {
      window.removeEventListener(event, this.recordActivity)
    })
  }
}

// 创建全局实例
export const tokenRefreshManager = new TokenRefreshManager()

/**
 * 在登录后调用
 */
export function startTokenRefresh() {
  // 本地版免登录，压根没有 token 可刷新。若照常启动定时器，
  // 会每分钟打一次 /api/auth/refresh 并稳定收到 401，白白刷屏日志。
  // 以运行时确定的模式为准（构建时未设置 VITE_DEPLOY_MODE 的本地版同样适用）。
  if (isLocalRuntime()) {
    tokenRefreshManager.stop()
    return
  }
  tokenRefreshManager.start()
}

/**
 * 在登出后调用
 */
export function stopTokenRefresh() {
  tokenRefreshManager.stop()
}

