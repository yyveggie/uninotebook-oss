import { useEffect, useState } from 'react'
import { apiGet, apiPost } from '../utils/api'
import { describeError, useSettingsT } from './Settings/messages'

const TERMINAL = new Set(['SUCCESS', 'FAILURE', 'REVOKED', 'INTERRUPTED'])
const RETRYABLE = new Set(['FAILURE', 'REVOKED', 'INTERRUPTED'])

/** 设置页的「后台任务」区块（标题与说明由设置页统一渲染） */
export default function LocalTasksPanel() {
  const { t, locale } = useSettingsT()
  const [tasks, setTasks] = useState([])
  // 存错误对象与兜底文案键，渲染时按当前语言翻译（切换语言后提示随之变化）
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)
  const [busy, setBusy] = useState(null)
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const response = await apiGet('/api/system/tasks', { cacheMode: 'no-cache' })
        if (active) setTasks(response.data || [])
      } catch (err) {
        if (active) setError({ err, key: 'tasks.loadFailed' })
      }
    }
    load()
    const interval = setInterval(load, 3000)
    return () => { active = false; clearInterval(interval) }
  }, [refresh])

  const act = async (id, action) => {
    setBusy(id)
    setError(null)
    try {
      await apiPost(`/api/system/tasks/${encodeURIComponent(id)}/${action}`, {})
      setRefresh((value) => value + 1)
    } catch (err) {
      setError({ err, key: 'tasks.actionFailed' })
    } finally {
      setBusy(null)
    }
  }

  const reindex = async () => {
    setBusy('reindex')
    setError(null)
    try {
      const response = await apiPost('/api/system/embeddings/reindex', {})
      setMessage({ key: 'tasks.reindexQueued', vars: { count: response.data?.task_ids?.length || 0 } })
      setRefresh((value) => value + 1)
    } catch (err) {
      setError({ err, key: 'tasks.reindexFailed' })
    } finally {
      setBusy(null)
    }
  }

  const dateLocale = locale === 'en' ? 'en-US' : 'zh-CN'

  return (
    <div className="settings-tasks">
      <div className="settings-tasks__toolbar">
        <button type="button" className="settings-btn settings-btn--ghost" onClick={reindex} disabled={busy !== null}>
          {busy === 'reindex' ? t('tasks.submitting') : t('tasks.reindex')}
        </button>
        {message && <span role="status" className="settings-tasks__message">{t(message.key, message.vars)}</span>}
      </div>
      {error && <p className="settings-error" role="alert">{describeError(t, locale, error.err, error.key)}</p>}
      {tasks.length === 0 && <p className="settings-card__hint">{t('tasks.empty')}</p>}
      {tasks.slice(0, 30).map((task) => {
        const running = !TERMINAL.has(task.status)
        const statusText = task.cancel_requested && running
          ? t('tasks.cancelling')
          : t(`tasks.status.${task.status}`)
        return (
          <div className="settings-card settings-task" key={task.id}>
            <div className="settings-card__head">
              <div className="settings-card__title">
                <span className={`settings-dot ${task.status === 'SUCCESS' ? 'is-on' : ''} ${running ? 'is-busy' : ''}`} aria-hidden="true" />
                <span className="settings-card__name">
                  {t(`tasks.names.${task.name}`) === `tasks.names.${task.name}` ? task.name.split('.').at(-1) : t(`tasks.names.${task.name}`)}
                </span>
                <span className={`settings-pill ${task.status === 'FAILURE' ? 'settings-pill--error' : ''}`}>
                  {statusText === `tasks.status.${task.status}` ? task.status : statusText}
                  {task.meta?.progress != null && running ? ` ${task.meta.progress}%` : ''}
                </span>
              </div>
              <div className="settings-card__actions">
                {running && (
                  <button type="button" className="settings-btn settings-btn--ghost" disabled={busy === task.id || !!task.cancel_requested}
                    onClick={() => act(task.id, 'cancel')}>{t('tasks.cancel')}</button>
                )}
                {RETRYABLE.has(task.status) && (
                  <button type="button" className="settings-btn settings-btn--ghost" disabled={busy === task.id}
                    onClick={() => act(task.id, 'retry')}>{t('tasks.retry')}</button>
                )}
              </div>
            </div>
            <p className="settings-card__hint">
              {t('tasks.attempts', { time: new Date(task.created_at * 1000).toLocaleString(dateLocale), count: task.attempts })}
            </p>
            {task.error && <p className="settings-error">{task.error}</p>}
          </div>
        )
      })}
    </div>
  )
}
