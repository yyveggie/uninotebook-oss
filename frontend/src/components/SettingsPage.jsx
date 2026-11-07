import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useRuntimeStore from '../stores/runtimeStore'
import useLocaleStore from '../stores/localeStore'
import LocalTasksPanel from './LocalTasksPanel'
import ProviderCard from './Settings/ProviderCard'
import FeatureGrid from './Settings/FeatureGrid'
import { CHAT_PROVIDERS, OPTIONAL_PROVIDERS } from './Settings/constants'
import RuntimeInfo from './Settings/RuntimeInfo'
import { describeError, useSettingsT } from './Settings/messages'
import './SettingsPage.css'

/**
 * 设置页：本地版所有配置集中在这一页。
 *
 * 自上而下：概览（配置进度 + 可用功能）→ 对话模型 → 向量模型 → 可选服务 →
 * 功能状态 → 运行环境（只读）→ 后台任务。宽屏左侧有分区导航，窄屏变为顶部横向标签。
 *
 * Key 由后端持久化到数据目录下的 credentials.json（权限 0600），保存后立即生效；
 * 前端**从不持有明文**——读取时后端只返回打码值。
 *
 * 文案中英双语（见 Settings/messages.js），语言选择记在本机。
 */

const SECTIONS = ['overview', 'chat', 'embedding', 'optional', 'features', 'runtime', 'tasks']

const sectionId = (id) => `settings-section-${id}`

function StepCard({ status, title, text, action, statusLabel }) {
  return (
    <div className={`settings-step settings-step--${status}`}>
      <div className="settings-step__head">
        <span className="settings-step__icon" aria-hidden="true">
          {status === 'done' ? '✓' : status === 'todo' ? '!' : '+'}
        </span>
        <span className="settings-step__title">{title}</span>
        <span className="settings-step__status">{statusLabel}</span>
      </div>
      <p className="settings-step__text">{text}</p>
      {action}
    </div>
  )
}

function Section({ id, title, tag, tagTone, desc, children }) {
  return (
    <section id={sectionId(id)} className="settings-section" aria-labelledby={`${sectionId(id)}-title`}>
      <div className="settings-section__head">
        <h2 id={`${sectionId(id)}-title`} className="settings-section__title">{title}</h2>
        {tag && <span className={`settings-tag ${tagTone ? `settings-tag--${tagTone}` : ''}`}>{tag}</span>}
      </div>
      {desc && <p className="settings-section__desc">{desc}</p>}
      {children}
    </section>
  )
}

export default function SettingsPage({ onClose }) {
  const { t, locale } = useSettingsT()
  const setLocale = useLocaleStore((s) => s.setLocale)
  const credentials = useRuntimeStore((s) => s.credentials)
  const capabilities = useRuntimeStore((s) => s.capabilities)
  const fetchCredentials = useRuntimeStore((s) => s.fetchCredentials)
  const fetchCapabilities = useRuntimeStore((s) => s.fetchCapabilities)
  const saveCredential = useRuntimeStore((s) => s.saveCredential)
  const refreshRuntimeState = useRuntimeStore((s) => s.refreshRuntimeState)

  const [openToken, setOpenToken] = useState(null)
  const [activeSection, setActiveSection] = useState('overview')
  const [defaultSaving, setDefaultSaving] = useState(false)
  const [defaultError, setDefaultError] = useState(null)
  const contentRef = useRef(null)

  useEffect(() => {
    fetchCredentials()
    fetchCapabilities({ force: true })
  }, [fetchCredentials, fetchCapabilities])

  const scrollToElement = useCallback((elementId) => {
    // 等表单展开渲染后再滚动，定位才准
    requestAnimationFrame(() => {
      document.getElementById(elementId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [])

  const scrollToSection = useCallback((id) => scrollToElement(sectionId(id)), [scrollToElement])

  // 「去配置」：对话模型任选其一，滚到分组即可；其余直接展开对应卡片的表单
  const setUp = useCallback((provider) => {
    if (CHAT_PROVIDERS.includes(provider)) {
      scrollToSection('chat')
      return
    }
    setOpenToken({ provider, at: Date.now() })
    scrollToElement(`settings-provider-${provider}`)
  }, [scrollToElement, scrollToSection])

  // 支持 /settings#embedding 这类深链（如上传页因缺向量模型被拦时直达）
  useEffect(() => {
    const hash = window.location.hash.replace('#', '')
    if (SECTIONS.includes(hash)) scrollToSection(hash)
  }, [scrollToSection])

  // 分区导航高亮当前可见的区块
  useEffect(() => {
    const root = contentRef.current
    if (!root || typeof IntersectionObserver === 'undefined') return undefined
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      if (visible[0]) setActiveSection(visible[0].target.id.replace('settings-section-', ''))
    }, { rootMargin: '-96px 0px -55% 0px' })
    root.querySelectorAll('.settings-section').forEach((el) => observer.observe(el))
    return () => observer.disconnect()
  }, [])

  const chatConfigured = CHAT_PROVIDERS.filter((p) => credentials[p]?.configured)
  const embeddingReady = Boolean(credentials.dashscope?.configured)
  const optionalConfigured = OPTIONAL_PROVIDERS.filter((p) => credentials[p]?.configured)
  const currentDefault = CHAT_PROVIDERS.find((p) => credentials[p]?.default_for_chat) || null

  const { readyCount, totalCount } = useMemo(() => {
    const values = Object.values(capabilities)
    return { readyCount: values.filter((c) => c.available).length, totalCount: values.length }
  }, [capabilities])
  const percent = totalCount ? Math.round((readyCount / totalCount) * 100) : 0

  const chooseDefault = async (provider) => {
    if (provider === currentDefault) return
    setDefaultSaving(true)
    setDefaultError(null)
    try {
      // 「自动」= 取消当前默认；选具体服务 = 设为默认
      if (provider) await saveCredential(provider, { useForChat: true })
      else if (currentDefault) await saveCredential(currentDefault, { useForChat: false })
    } catch (err) {
      setDefaultError(err)
    } finally {
      setDefaultSaving(false)
    }
  }

  const navStatus = {
    chat: chatConfigured.length > 0,
    embedding: embeddingReady,
    optional: optionalConfigured.length > 0,
  }

  const providerCards = (providers) => providers.map((p) => (
    <ProviderCard
      key={p}
      provider={p}
      state={credentials[p]}
      onSave={saveCredential}
      onTested={refreshRuntimeState}
      openToken={openToken}
    />
  ))

  return (
    <div className="settings-page" lang={locale === 'en' ? 'en' : 'zh-CN'}>
      <header className="settings-header">
        <div>
          <h1 className="settings-title">{t('page.title')}</h1>
          <p className="settings-subtitle">{t('page.subtitle')}</p>
        </div>
        <div className="settings-header__actions">
          <div className="settings-segmented settings-lang" role="group" aria-label={t('page.language')}>
            <button type="button" aria-pressed={locale === 'zh'} onClick={() => setLocale('zh')}>中文</button>
            <button type="button" aria-pressed={locale === 'en'} onClick={() => setLocale('en')}>English</button>
          </div>
          {onClose && (
            <button type="button" className="settings-btn settings-btn--ghost" onClick={onClose}>{t('page.back')}</button>
          )}
        </div>
      </header>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label={t('page.title')}>
          {SECTIONS.map((id) => (
            <button
              type="button"
              key={id}
              className={`settings-nav__item ${activeSection === id ? 'is-active' : ''}`}
              aria-current={activeSection === id ? 'true' : undefined}
              onClick={() => scrollToSection(id)}
            >
              <span>{t(`nav.${id}`)}</span>
              {id in navStatus && <span className={`settings-dot ${navStatus[id] ? 'is-on' : ''}`} aria-hidden="true" />}
              {id === 'features' && totalCount > 0 && <span className="settings-nav__count">{readyCount}/{totalCount}</span>}
            </button>
          ))}
        </nav>

        <main className="settings-content" ref={contentRef}>
          <Section id="overview" title={t('overview.title')}>
            <div className="settings-steps">
              <StepCard
                status={chatConfigured.length ? 'done' : 'todo'}
                statusLabel={chatConfigured.length ? t('overview.done') : t('overview.todo')}
                title={t('overview.chatTitle')}
                text={chatConfigured.length
                  ? t('overview.chatReady', { names: chatConfigured.map((p) => t(`provider.${p}.label`)).join(' · ') })
                  : t('overview.chatMissing')}
                action={!chatConfigured.length && (
                  <button type="button" className="settings-btn settings-btn--primary" onClick={() => scrollToSection('chat')}>
                    {t('overview.setUp')}
                  </button>
                )}
              />
              <StepCard
                status={embeddingReady ? 'done' : 'todo'}
                statusLabel={embeddingReady ? t('overview.done') : t('overview.todo')}
                title={t('overview.embeddingTitle')}
                text={embeddingReady
                  ? t('overview.embeddingReady', { model: credentials.dashscope?.model || t('provider.dashscope.label') })
                  : t('overview.embeddingMissing')}
                action={!embeddingReady && (
                  <button type="button" className="settings-btn settings-btn--primary" onClick={() => setUp('dashscope')}>
                    {t('overview.setUp')}
                  </button>
                )}
              />
              <StepCard
                status={optionalConfigured.length ? 'done' : 'optional'}
                statusLabel={t('overview.optional')}
                title={t('overview.optionalTitle')}
                text={t('overview.optionalSummary', { count: optionalConfigured.length, total: OPTIONAL_PROVIDERS.length })}
              />
            </div>

            {totalCount > 0 && (
              <div className="settings-progress">
                <div className="settings-progress__label">
                  <span>{t('overview.features')}</span>
                  <button type="button" className="settings-inline-btn" onClick={() => scrollToSection('features')}>
                    <strong>{readyCount}</strong> / {totalCount}
                  </button>
                </div>
                <div className="settings-progress__bar" role="progressbar" aria-valuemin={0} aria-valuemax={totalCount}
                  aria-valuenow={readyCount} aria-label={t('overview.features')}>
                  <span style={{ width: `${percent}%` }} />
                </div>
                {chatConfigured.length > 0 && embeddingReady && (
                  <p className="settings-progress__note">{t('overview.allReady')}</p>
                )}
              </div>
            )}
          </Section>

          <Section id="chat" title={t('groups.chat.title')} tag={t('groups.chat.tag')} desc={t('groups.chat.desc')}>
            <div className="settings-default">
              <div>
                <div className="settings-default__title">{t('defaultChat.title')}</div>
                <p className="settings-default__desc">{t('defaultChat.desc')}</p>
              </div>
              <div className="settings-segmented" role="radiogroup" aria-label={t('defaultChat.title')}>
                <button type="button" role="radio" aria-checked={!currentDefault} disabled={defaultSaving}
                  onClick={() => chooseDefault(null)}>
                  {t('defaultChat.auto')}
                </button>
                {CHAT_PROVIDERS.map((p) => {
                  const configured = Boolean(credentials[p]?.configured)
                  return (
                    <button type="button" role="radio" key={p} aria-checked={currentDefault === p}
                      disabled={defaultSaving || !configured}
                      title={configured ? undefined : t('defaultChat.notConfigured')}
                      onClick={() => chooseDefault(p)}>
                      {t(`provider.${p}.label`)}
                    </button>
                  )
                })}
              </div>
              {defaultError && (
                <p className="settings-error" role="alert">{describeError(t, locale, defaultError, 'errors.saveFailed')}</p>
              )}
            </div>
            {providerCards(CHAT_PROVIDERS)}
          </Section>

          <Section id="embedding" title={t('groups.embedding.title')} tag={t('groups.embedding.tag')} tagTone="required"
            desc={t('groups.embedding.desc')}>
            {providerCards(['dashscope'])}
          </Section>

          <Section id="optional" title={t('groups.optional.title')} tag={t('groups.optional.tag')} desc={t('groups.optional.desc')}>
            {providerCards(OPTIONAL_PROVIDERS)}
          </Section>

          <Section id="features" title={t('features.title')} desc={t('features.desc')}>
            <FeatureGrid capabilities={capabilities} onSetUp={setUp} />
          </Section>

          <Section id="runtime" title={t('runtime.title')} desc={t('runtime.desc')}>
            <RuntimeInfo />
          </Section>

          <Section id="tasks" title={t('tasks.title')} desc={t('tasks.desc')}>
            <LocalTasksPanel />
          </Section>
        </main>
      </div>
    </div>
  )
}
