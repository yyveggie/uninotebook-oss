import { useEffect, useRef, useState } from 'react'
import { PROVIDER_META } from '../../stores/runtimeStore'
import { apiPost } from '../../utils/api'
import { describeError, useSettingsT } from './messages'

/** 支持「测试连接」与「本地服务免认证」的 OpenAI 兼容服务 */
const TESTABLE = ['openai', 'qwen', 'moonshot', 'deepseek', 'dashscope']

const hostOf = (url) => {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/**
 * 单个服务的配置卡片：状态、当前生效的模型 / 端点 / Key（打码），以及内联编辑表单。
 *
 * openToken 变化且指向本卡片时自动展开表单——概览与功能状态里的「去配置」用它直达。
 */
export default function ProviderCard({ provider, state, onSave, onTested, openToken }) {
  const { t, locale } = useSettingsT()
  const meta = PROVIDER_META[provider] || {}
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [dimensions, setDimensions] = useState('')
  const [authRequired, setAuthRequired] = useState(true)
  const [testResult, setTestResult] = useState(null)
  const [testing, setTesting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const firstInputRef = useRef(null)

  const configured = Boolean(state?.configured)
  const userProvided = Boolean(state?.user_provided)
  const testable = TESTABLE.includes(provider)
  const hasEndpoint = provider !== 'dashscope_native'

  useEffect(() => {
    if (openToken?.provider === provider) setEditing(true)
  }, [openToken, provider])

  useEffect(() => {
    if (!editing) return
    setBaseUrl(state?.base_url || meta.defaultBaseUrl || '')
    setModel(state?.model || '')
    setDimensions(state?.dimensions || '')
    setAuthRequired(state?.auth_required !== false)
    setError(null)
    firstInputRef.current?.focus({ preventScroll: true })
  }, [editing, state, meta.defaultBaseUrl])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      await onSave(provider, {
        apiKey: apiKey.trim() || undefined,
        baseUrl: hasEndpoint ? baseUrl.trim() : undefined,
        model: hasEndpoint ? model.trim() : undefined,
        dimensions: provider === 'dashscope' ? Number(dimensions) : undefined,
        authRequired: testable ? authRequired : undefined,
      })
      setApiKey('')
      setTestResult(null)
      setEditing(false)
    } catch (err) {
      setError({ err, key: 'errors.saveFailed' })
    } finally {
      setSaving(false)
    }
  }

  const handleClear = async () => {
    setSaving(true)
    setError(null)
    try {
      // 空字符串表示清除覆盖，回落到环境变量
      await onSave(provider, { apiKey: '' })
      setApiKey('')
      setTestResult(null)
      setEditing(false)
    } catch (err) {
      setError({ err, key: 'errors.clearFailed' })
    } finally {
      setSaving(false)
    }
  }

  const testConnection = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      // 非 2xx 会抛出 ApiError，不会把 502/400 误报为成功
      const result = await apiPost('/api/system/credentials/test', { provider })
      setTestResult({ ok: true, dimensions: result.data?.dimensions })
    } catch (err) {
      setTestResult({ ok: false, err })
    } finally {
      setTesting(false)
      // 测试可能更新探测到的维度等信息：以服务端为准刷新凭据与能力清单
      onTested?.()
    }
  }

  // 结果与错误存原始对象，渲染时按当前语言出文案
  const testText = !testResult ? '' : testResult.ok
    ? (testResult.dimensions ? t('provider.testOkDimensions', { dimensions: testResult.dimensions }) : t('provider.testOk'))
    : describeError(t, locale, testResult.err, 'errors.testFailed')

  const canSave = !saving && !(authRequired && !apiKey.trim() && !configured) && !(!authRequired && !baseUrl.trim())
  const keyText = state?.auth_required === false ? t('provider.noAuth') : state?.masked_api_key

  return (
    <div id={`settings-provider-${provider}`} className={`settings-card ${configured ? 'is-configured' : ''}`}>
      <div className="settings-card__head">
        <div className="settings-card__title">
          <span className={`settings-dot ${configured ? 'is-on' : ''}`} aria-hidden="true" />
          <span className="settings-card__name">{t(`provider.${provider}.label`)}</span>
          <span className={`settings-pill ${configured ? 'settings-pill--ok' : ''}`}>
            {configured ? t('provider.configured') : t('provider.notConfigured')}
          </span>
          {state?.default_for_chat && <span className="settings-pill settings-pill--accent">{t('provider.isDefault')}</span>}
          {configured && !userProvided && (
            <span className="settings-pill" title={t('provider.fromEnvTitle')}>
              {provider === 'dashscope_native' ? t('provider.reusesDashscope') : t('provider.fromEnv')}
            </span>
          )}
        </div>

        <div className="settings-card__actions">
          {meta.url && (
            <a className="settings-link" href={meta.url} target="_blank" rel="noreferrer noopener">
              {t('provider.getKey')}
            </a>
          )}
          {configured && testable && (
            <button type="button" className="settings-btn settings-btn--ghost" onClick={testConnection}
              disabled={testing || editing} title={t('provider.testTitle')}>
              {testing ? t('provider.testing') : t('provider.test')}
            </button>
          )}
          <button type="button" className={`settings-btn ${configured ? 'settings-btn--ghost' : 'settings-btn--primary'}`}
            onClick={() => setEditing((v) => !v)} disabled={saving} aria-expanded={editing}>
            {editing ? t('provider.cancel') : configured ? t('provider.edit') : t('provider.configure')}
          </button>
        </div>
      </div>

      <p className="settings-card__hint">{t(`provider.${provider}.hint`)}</p>

      {configured && !editing && (
        <dl className="settings-facts">
          {hasEndpoint && (
            <div>
              <dt>{t('provider.model')}</dt>
              <dd>{state.model || '—'}</dd>
            </div>
          )}
          {hasEndpoint && (
            <div>
              <dt>{t('provider.endpoint')}</dt>
              <dd title={state.base_url || ''}>{state.base_url ? hostOf(state.base_url) : t('provider.officialEndpoint')}</dd>
            </div>
          )}
          <div>
            <dt>{t('provider.apiKey')}</dt>
            <dd>{keyText || '—'}</dd>
          </div>
          {provider === 'dashscope' && (
            <div>
              <dt>{t('provider.dimensions')}</dt>
              <dd>{state.dimensions || t('provider.dimensionsAuto')}</dd>
            </div>
          )}
        </dl>
      )}

      {testResult && (
        <p role="status" className={`settings-card__result ${testResult.ok ? 'is-ok' : 'is-error'}`}>{testText}</p>
      )}

      {editing && (
        <form className="settings-form" onSubmit={(e) => { e.preventDefault(); if (canSave) handleSave() }}>
          {hasEndpoint && (
            <label className="settings-field">
              <span className="settings-field__label">{t('provider.modelLabel')}</span>
              <input ref={firstInputRef} className="settings-input" value={model} spellCheck={false}
                onChange={(e) => setModel(e.target.value)} placeholder={t('provider.modelPlaceholder')} />
            </label>
          )}
          {provider === 'dashscope' && (
            <label className="settings-field">
              <span className="settings-field__label">{t('provider.dimensionsLabel')}</span>
              <input className="settings-input" type="number" min="1" max="65536" value={dimensions}
                onChange={(e) => setDimensions(e.target.value)} />
            </label>
          )}
          {testable && (
            <label className="settings-check">
              <input type="checkbox" checked={!authRequired} onChange={(e) => setAuthRequired(!e.target.checked)} />
              <span>{t('provider.noAuthLabel')}</span>
            </label>
          )}
          <label className="settings-field">
            <span className="settings-field__label">{t('provider.apiKey')}</span>
            <input
              ref={hasEndpoint ? undefined : firstInputRef}
              className="settings-input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder={configured ? t('provider.keyPlaceholderKeep') : 'sk-...'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </label>
          {hasEndpoint && (
            <label className="settings-field">
              <span className="settings-field__label">
                Base URL <span className="settings-field__opt">{t('provider.baseUrlOptional')}</span>
              </span>
              <input className="settings-input" type="text" spellCheck={false} placeholder={meta.defaultBaseUrl || ''}
                value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            </label>
          )}

          {error && <p className="settings-error" role="alert">{describeError(t, locale, error.err, error.key)}</p>}

          <div className="settings-form__actions">
            <button type="submit" className="settings-btn settings-btn--primary" disabled={!canSave}>
              {saving ? t('provider.saving') : t('provider.save')}
            </button>
            {configured && userProvided && (
              <button type="button" className="settings-btn settings-btn--danger" onClick={handleClear} disabled={saving}>
                {t('provider.clear')}
              </button>
            )}
          </div>
        </form>
      )}
    </div>
  )
}
