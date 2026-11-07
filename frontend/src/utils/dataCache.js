import { CACHE_TTL, CACHE_LIMITS } from './cacheConfig'

/**
 * 带 LRU 淘汰机制的数据缓存
 * 
 * 特性：
 * - TTL 过期自动清理
 * - 最大条目数限制 (LRU 淘汰)
 * - 访问时更新 LRU 顺序
 * - 🆕 SWR (Stale-While-Revalidate) 模式支持
 */
class DataCache {
  constructor(maxEntries = CACHE_LIMITS.MAX_ENTRIES) {
    this.store = new Map()  // key -> { value, expiresAt, lastAccess, createdAt }
    this.maxEntries = maxEntries
    this.revalidating = new Set()  // 正在后台刷新的 keys
  }

  get(key) {
    if (!key) return null
    const entry = this.store.get(key)
    if (!entry) return null

    // 检查过期
    if (entry.expiresAt && entry.expiresAt < Date.now()) {
      this.store.delete(key)
      return null
    }

    // 更新访问时间 (LRU)
    entry.lastAccess = Date.now()
    return entry.value
  }

  set(key, value, ttl = CACHE_TTL.DEFAULT) {
    if (!key) return

    // 如果已存在，先删除（Map 会保持插入顺序）
    if (this.store.has(key)) {
      this.store.delete(key)
    }

    // 检查容量，淘汰最久未访问的条目
    if (this.store.size >= this.maxEntries) {
      this._evictLRU()
    }

    const now = Date.now()
    const expiresAt = typeof ttl === 'number' && ttl > 0 ? now + ttl : null
    this.store.set(key, {
      value,
      expiresAt,
      lastAccess: now,
      createdAt: now  // 🆕 用于 SWR 计算
    })
  }

  /**
   * 🆕 SWR (Stale-While-Revalidate) 模式获取数据
   * 
   * 策略：
   * 1. 数据新鲜 (< maxAge): 直接返回
   * 2. 数据过期但在 stale 窗口内 (< maxAge + staleWhileRevalidate): 
   *    返回旧数据 + 后台刷新
   * 3. 数据完全过期或不存在: 同步获取
   * 
   * @param {string} key - 缓存键
   * @param {Function} fetchFn - 获取数据的异步函数
   * @param {Object} options - 配置项
   * @param {number} options.maxAge - 数据新鲜期 (毫秒)，默认 60 秒
   * @param {number} options.staleWhileRevalidate - 过期后仍可返回的时间窗口 (毫秒)，默认 5 分钟
   * @returns {Promise<any>} 缓存或新获取的数据
   */
  async getSWR(key, fetchFn, options = {}) {
    const {
      maxAge = CACHE_TTL.DEFAULT,
      staleWhileRevalidate = 5 * 60 * 1000  // 默认 5 分钟
    } = options

    const entry = this.store.get(key)
    const now = Date.now()

    // 1. 新鲜数据：直接返回
    if (entry && entry.createdAt && (now - entry.createdAt) < maxAge) {
      entry.lastAccess = now
      return entry.value
    }

    // 2. 过期但在 stale 窗口内：返回旧数据 + 后台刷新
    if (entry && entry.createdAt) {
      const age = now - entry.createdAt
      if (age < maxAge + staleWhileRevalidate) {
        entry.lastAccess = now

        // 避免重复刷新
        if (!this.revalidating.has(key)) {
          this.revalidating.add(key)

          // 后台刷新（不阻塞返回）
          fetchFn()
            .then(data => {
              if (data !== undefined && data !== null) {
                this.set(key, data, maxAge + staleWhileRevalidate)
              }
            })
            .catch(err => {
              console.warn(`[SWR] Background revalidate failed for ${key}:`, err)
            })
            .finally(() => {
              this.revalidating.delete(key)
            })
        }

        return entry.value  // 立即返回旧数据
      }
    }

    // 3. 完全过期或不存在：同步获取
    try {
      const data = await fetchFn()
      if (data !== undefined && data !== null) {
        this.set(key, data, maxAge + staleWhileRevalidate)
      }
      return data
    } catch (err) {
      // 如果获取失败但有旧数据，返回旧数据
      if (entry) {
        console.warn(`[SWR] Fetch failed, returning stale data for ${key}`)
        return entry.value
      }
      throw err
    }
  }

  /**
   * 🆕 检查数据是否正在后台刷新
   */
  isRevalidating(key) {
    return this.revalidating.has(key)
  }

  invalidate(key) {
    if (!key) return
    this.store.delete(key)
    this.revalidating.delete(key)
  }

  invalidateByPrefix(prefix) {
    if (!prefix) return
    for (const key of this.store.keys()) {
      if (key.startsWith(prefix)) {
        this.store.delete(key)
        this.revalidating.delete(key)
      }
    }
  }

  clear() {
    this.store.clear()
    this.revalidating.clear()
  }

  /**
   * 获取缓存统计信息（用于调试）
   */
  getStats() {
    let expiredCount = 0
    let staleCount = 0
    const now = Date.now()

    for (const [, entry] of this.store.entries()) {
      if (entry.expiresAt && entry.expiresAt < now) {
        expiredCount++
      } else if (entry.createdAt && (now - entry.createdAt) > CACHE_TTL.DEFAULT) {
        staleCount++
      }
    }

    return {
      size: this.store.size,
      maxEntries: this.maxEntries,
      expiredCount,
      staleCount,
      revalidatingCount: this.revalidating.size,
    }
  }

  /**
   * 淘汰最久未访问的条目 (LRU)
   * 会淘汰 10% 的条目以减少频繁淘汰
   */
  _evictLRU() {
    const entries = Array.from(this.store.entries())

    // 先清理已过期的
    const now = Date.now()
    for (const [key, entry] of entries) {
      if (entry.expiresAt && entry.expiresAt < now) {
        this.store.delete(key)
      }
    }

    // 如果清理后仍超限，按 lastAccess 排序淘汰
    if (this.store.size >= this.maxEntries) {
      const sortedEntries = Array.from(this.store.entries())
        .sort((a, b) => a[1].lastAccess - b[1].lastAccess)

      // 淘汰 10% 的条目
      const evictCount = Math.max(1, Math.floor(this.maxEntries * 0.1))
      for (let i = 0; i < evictCount && i < sortedEntries.length; i++) {
        this.store.delete(sortedEntries[i][0])
      }
    }
  }
}

export const dataCache = new DataCache()

// 导出配置供组件使用
export { CACHE_TTL, CACHE_LIMITS }

