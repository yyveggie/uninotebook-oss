import { useState } from 'react'
import { CHAT_PROVIDERS } from './constants'
import { useSettingsT } from './messages'

/**
 * 功能状态网格：每项功能一张小卡，可用 / 不可用一目了然；
 * 不可用时给出缺什么、去哪配，或可复制的安装命令。
 */
export default function FeatureGrid({ capabilities, onSetUp }) {
  const { t } = useSettingsT()
  const [copied, setCopied] = useState(null)

  const entries = Object.entries(capabilities)
  if (entries.length === 0) return null

  // 对话模型是「任选其一」，后端缺失时三家都会列出，合并成一项；其余服务都是必需的，逐项并列
  const keyNames = (keys) => {
    const missingChat = CHAT_PROVIDERS.every((p) => keys.includes(p))
    const names = keys.filter((k) => !(missingChat && CHAT_PROVIDERS.includes(k))).map((k) => t(`provider.${k}.label`))
    if (missingChat) names.unshift(t('groups.chat.title'))
    return names.join(t('features.and'))
  }

  const copy = (command) => {
    navigator.clipboard?.writeText(command).then(() => {
      setCopied(command)
      setTimeout(() => setCopied((value) => (value === command ? null : value)), 1500)
    }).catch(() => {})
  }

  return (
    <div className="settings-features">
      {entries.map(([key, cap]) => {
        const missingKeys = cap.missing_keys || []
        const missingPackages = cap.missing_packages || []
        const missingCommands = cap.missing_commands || []
        const label = t(`features.${key}`)
        return (
          <div key={key} className={`settings-feature ${cap.available ? 'is-on' : ''}`}>
            <div className="settings-feature__head">
              <span className={`settings-dot ${cap.available ? 'is-on' : ''}`} aria-hidden="true" />
              <span className="settings-feature__name">{label === `features.${key}` ? cap.label : label}</span>
              <span className="settings-visually-hidden">
                {cap.available ? t('features.available') : t('features.unavailable')}
              </span>
            </div>
            {cap.available ? (
              <p className="settings-feature__status">{t('features.available')}</p>
            ) : (
              <div className="settings-feature__needs">
                {missingKeys.length > 0 && (
                  <p>
                    {t('features.needsKey', { names: keyNames(missingKeys) })}
                    {' '}
                    <button type="button" className="settings-inline-btn" onClick={() => onSetUp(missingKeys[0])}>
                      {t('features.setUp')}
                    </button>
                  </p>
                )}
                {missingPackages.length > 0 && <p>{t('features.needsPackage', { names: missingPackages.join(', ') })}</p>}
                {missingCommands.length > 0 && <p>{t('features.needsCommand', { names: missingCommands.join(', ') })}</p>}
                {missingPackages.length > 0 && cap.install_command && (
                  <div className="settings-command">
                    <code>{cap.install_command}</code>
                    <button type="button" className="settings-inline-btn" onClick={() => copy(cap.install_command)}>
                      {copied === cap.install_command ? t('features.copied') : t('features.copy')}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
