const DEFAULT_CONCURRENCY = 1
const GET_CONCURRENCY = 3
const DEFAULT_MAX_ATTEMPTS = 5
const DEFAULT_TIMEOUT_MS = 120000  // 120秒超时（LLM生成任务可能较慢）
const POLISH_TIMEOUT_MS = 240000   // 文章润色（思考模型）允许更长时间
const DEEP_RESEARCH_TIMEOUT_MS = 420000 // Deep Research 两步搜索+研究流程更慢

const parseRetryAfter = (response) => {
  const retryAfter = response.headers.get('Retry-After')
  if (retryAfter) {
    const numeric = Number(retryAfter)
    if (!Number.isNaN(numeric) && numeric > 0) {
      return numeric * 1000
    }
    const parsedDate = Date.parse(retryAfter)
    if (!Number.isNaN(parsedDate)) {
      const diff = parsedDate - Date.now()
      if (diff > 0) {
        return diff
      }
    }
  }

  const resetTs = response.headers.get('X-RateLimit-Reset')
  if (resetTs) {
    const numeric = Number(resetTs)
    if (!Number.isNaN(numeric)) {
      const diff = numeric * 1000 - Date.now()
      if (diff > 0) {
        return diff
      }
    }
  }

  // 默认退避 5 秒
  return 5000
}

class RequestScheduler {
  constructor() {
    this.queues = new Map()
    this.dedupeWindows = new Map()
  }

  schedule(url, options = {}, { key, dedupeWindowMs = 0, dedupeKey } = {}) {
    const method = options?.method?.toUpperCase() || 'GET'
    const queueKey = key ?? this._resolveKey(url, options.method)
    const canUseDedupeWindow =
      dedupeWindowMs > 0 &&
      (method === 'GET' || method === 'HEAD') &&
      !options?.signal

    if (canUseDedupeWindow) {
      const resolvedDedupeKey = dedupeKey ?? queueKey
      const existing = this.dedupeWindows.get(resolvedDedupeKey)
      if (existing && existing.expiresAt > Date.now()) {
        return existing.promise
      }
      if (existing) {
        clearTimeout(existing.timer)
        this.dedupeWindows.delete(resolvedDedupeKey)
      }

      const promise = this._enqueue(queueKey, url, options)
      const timer = setTimeout(() => {
        const current = this.dedupeWindows.get(resolvedDedupeKey)
        if (current?.promise === promise) {
          this.dedupeWindows.delete(resolvedDedupeKey)
        }
      }, dedupeWindowMs)

      this.dedupeWindows.set(resolvedDedupeKey, {
        promise,
        expiresAt: Date.now() + dedupeWindowMs,
        timer,
      })

      return promise
    }

    return this._enqueue(queueKey, url, options)
  }

  _enqueue(queueKey, url, options) {
    const queue = this._ensureQueue(queueKey)

    return new Promise((resolve, reject) => {
      queue.items.push({
        url,
        options,
        resolve,
        reject,
        attempt: 0,
      })
      this._processQueue(queueKey)
    })
  }

  _resolveKey(url, method = 'GET') {
    try {
      const parsed = new URL(url)
      return `${method.toUpperCase()} ${parsed.pathname}`
    } catch {
      return `${method?.toUpperCase() ?? 'GET'} ${url}`
    }
  }

  _ensureQueue(key) {
    if (!this.queues.has(key)) {
      const isGet = key.startsWith('GET ')
      this.queues.set(key, {
        items: [],
        running: 0,
        concurrency: isGet ? GET_CONCURRENCY : DEFAULT_CONCURRENCY,
        pausedUntil: 0,
        timer: null,
      })
    }
    return this.queues.get(key)
  }

  _resolveTimeoutMs(url, options = {}) {
    const isUploadRequest = url.includes('/upload') || options?.body instanceof FormData
    if (isUploadRequest) {
      return 0
    }

    if (url.includes('/api/document/') && url.includes('/sources/deep-research')) {
      return DEEP_RESEARCH_TIMEOUT_MS
    }

    if (url.includes('/api/polish/rewrite')) {
      return POLISH_TIMEOUT_MS
    }

    return DEFAULT_TIMEOUT_MS
  }

  _processQueue(key) {
    const queue = this.queues.get(key)
    if (!queue) {
      return
    }

    if (queue.running >= queue.concurrency) {
      return
    }

    const now = Date.now()
    if (queue.pausedUntil > now) {
      if (!queue.timer) {
        queue.timer = setTimeout(() => {
          queue.timer = null
          this._processQueue(key)
        }, queue.pausedUntil - now)
      }
      return
    }

    const task = queue.items.shift()
    if (!task) {
      return
    }

    queue.running += 1

    this._executeTask(key, queue, task)
      .catch((error) => {
        task.reject(error)
      })
      .finally(() => {
        queue.running -= 1
        this._processQueue(key)
      })
  }

  async _executeTask(key, queue, task) {
    const controller = new AbortController()
    const originalSignal = task.options?.signal
    const mergedOptions = {
      ...task.options,
      signal: controller.signal,
    }

    if (originalSignal) {
      if (originalSignal.aborted) {
        task.reject(new DOMException('请求已取消', 'AbortError'))
        return
      }
      originalSignal.addEventListener(
        'abort',
        () => {
          controller.abort()
        },
        { once: true }
      )
    }

    // 🔧 添加超时控制（按接口类型设置不同超时）
    const timeoutMs = this._resolveTimeoutMs(task.url, task.options)
    let timeoutId = null
    
    if (timeoutMs > 0) {
      timeoutId = setTimeout(() => {
        controller.abort()
      }, timeoutMs)
    }

    try {
      const response = await fetch(task.url, mergedOptions)

      if (response.status === 429) {
        task.attempt += 1
        if (task.attempt >= DEFAULT_MAX_ATTEMPTS) {
          task.reject(
            new Error(
              `请求 ${task.url} 超过速率限制且达到最大重试次数`
            )
          )
          return
        }

        const delay = parseRetryAfter(response)
        queue.pausedUntil = Date.now() + delay
        queue.items.unshift(task)

        if (!queue.timer) {
          queue.timer = setTimeout(() => {
            queue.timer = null
            this._processQueue(key)
          }, delay)
        }
        return
      }

      task.resolve(response)
    } catch (error) {
      if (controller.signal.aborted) {
        // 区分超时和手动取消
        if (timeoutId !== null) {
          task.reject(new Error('请求超时，请稍后重试'))
        } else {
          task.reject(new Error(`请求 ${task.url} 被取消`))
        }
      } else {
        task.reject(error)
      }
    } finally {
      // 清理超时定时器
      if (timeoutId !== null) {
        clearTimeout(timeoutId)
      }
    }
  }
}

export const requestScheduler = new RequestScheduler()


