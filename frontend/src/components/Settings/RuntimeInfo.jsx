import { useEffect, useState } from 'react'
import { apiGet } from '../../utils/api'
import { useSettingsT } from './messages'

/**
 * 运行环境（只读）：数据与凭据在哪、读的是哪个配置文件、当前生效的上限与并发。
 * 每项标出对应的环境变量或启动参数，告诉用户「要改去哪改」。
 */
export default function RuntimeInfo() {
  const { t } = useSettingsT()
  const [info, setInfo] = useState(null)
  const [failed, setFailed] = useState(false)
  const [copied, setCopied] = useState(null)

  useEffect(() => {
    let active = true
    apiGet('/api/system/info', { cacheMode: 'no-cache' })
      .then((res) => { if (active) setInfo(res?.data || null) })
      .catch(() => { if (active) setFailed(true) })
    return () => { active = false }
  }, [])

  if (failed) return <p className="settings-error">{t('runtime.loadFailed')}</p>
  if (!info) return null

  const copy = (value) => {
    navigator.clipboard?.writeText(value).then(() => {
      setCopied(value)
      setTimeout(() => setCopied((current) => (current === value ? null : current)), 1500)
    }).catch(() => {})
  }

  const formatSetting = ({ key, value }) => {
    if (key === 'max_upload_mb') return `${value} MB`
    if (key === 'local_task_timeout_seconds') return `${Math.round(value)} ${t('runtime.seconds')}`
    if (key === 'document_parser_strategy') {
      if (value === 'simple') return t('runtime.parserSimple')
      if (value === 'hybrid') return t('runtime.parserHybrid')
    }
    if (key === 'local_allowed_hosts') {
      if (!value) return t('runtime.hostsDefault')
      if (value.trim() === '*') return t('runtime.hostsAny')
    }
    return String(value)
  }

  const pathRows = [
    { label: t('runtime.dataDir'), value: info.data_dir, hint: t('runtime.dataDirHint'), source: '--data-dir · LOCAL_DATA_DIR' },
    { label: t('runtime.credentialsFile'), value: info.credentials_file, hint: t('runtime.credentialsFileHint') },
    {
      label: t('runtime.envFile'),
      value: info.env_file?.path,
      hint: info.env_file?.exists ? t('runtime.envFileFound') : t('runtime.envFileMissing'),
    },
  ]

  return (
    <div className="settings-runtime">
      <div className="settings-runtime__paths">
        {pathRows.map((row) => (
          <div className="settings-path" key={row.label}>
            <div className="settings-path__head">
              <span className="settings-path__label">{row.label}</span>
              {row.source && <code className="settings-env">{row.source}</code>}
            </div>
            <div className="settings-path__value">
              <code title={row.value}>{row.value}</code>
              <button type="button" className="settings-inline-btn" onClick={() => copy(row.value)}>
                {copied === row.value ? t('runtime.copied') : t('runtime.copy')}
              </button>
            </div>
            <p className="settings-path__hint">{row.hint}</p>
          </div>
        ))}
      </div>

      <table className="settings-table">
        <tbody>
          <tr>
            <th scope="row">{t('runtime.listen')}</th>
            <td>{info.listen?.host}:{info.listen?.port}</td>
            <td><code className="settings-env">--host · --port</code></td>
          </tr>
          {(info.settings || []).map((item) => (
            <tr key={item.key}>
              <th scope="row">{t(`runtime.${item.key}`)}</th>
              <td>{formatSetting(item)}</td>
              <td><code className="settings-env">{item.env}</code></td>
            </tr>
          ))}
          <tr>
            <th scope="row">{t('runtime.python')}</th>
            <td>{info.python}</td>
            <td />
          </tr>
        </tbody>
      </table>
    </div>
  )
}
