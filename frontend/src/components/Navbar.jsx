import { useState } from 'react'
import PropTypes from 'prop-types'
import useAuthStore from '../stores/authStore'
import useRuntimeStore from '../stores/runtimeStore'
import { InviteEntry, AccountMenuExtras, CheckinHintWatcher } from './AccountExtras'
import ReportIssueModal from './ReportIssueModal'

/**
 * @typedef {Object} NavbarProps
 * @property {() => void} [onLogout]
 * @property {() => void} [onLogoClick]
 * @property {string} [title]
 * @property {import('react').ReactNode} [titleComponent]
 * @property {'workspace' | 'knowledgeGraph' | 'creativeStudio' | 'polish' | 'recharge'} [activeNav]
 * @property {() => void} [onSelectWorkspace]
 * @property {() => void} [onSelectKnowledgeGraph]
 * @property {() => void} [onSelectPolish]
 * @property {() => void} [onSelectCreativeStudio]
 * @property {() => void} [onSelectRecharge]
 * @property {() => void} [onSelectSettings] 本地版：打开 API Key 设置页
 * @property {boolean} [hideLoginButton]
 */

/**
 * @param {NavbarProps} props
 */
function Navbar(props) {
  const {
    onLogout,
    onLogoClick,
    title,
    titleComponent,
    activeNav,
    onSelectWorkspace,
    onSelectKnowledgeGraph,
    onSelectPolish,
    onSelectCreativeStudio,
    onSelectRecharge,
    onSelectSettings,
    hideLoginButton = false,
  } = props

  // 从 authStore 读取用户状态（消除 props drilling）
  const user = useAuthStore(s => s.user)
  const showUserMenu = useAuthStore(s => s.showUserMenu)
  const setShowUserMenu = useAuthStore(s => s.setShowUserMenu)
  const setShowAuthModal = useAuthStore(s => s.setShowAuthModal)
  const onShowAuthModal = () => setShowAuthModal(true)

  // 本地版没有账号体系：登录、积分、签到、邀请、充值一概不展示
  const isLocalMode = useRuntimeStore(s => s.deployMode === 'local')

  // 未签到时头像旁的提示箭头（状态由 AccountMenuExtras 回传）
  const [showCheckinArrow, setShowCheckinArrow] = useState(false)
  const [showReportModal, setShowReportModal] = useState(false)

  return (
    <>
      <div className={`home-navbar ${hideLoginButton ? 'transparent' : ''}`}>
        <div className="home-nav-content">
          <div
            className="home-logo"
            onClick={onLogoClick}
            style={onLogoClick ? { cursor: 'pointer' } : {}}
          >
            <div className="home-logo-mark">Unb</div>
            <div className="home-logo-text-container">
              <span className="logo-text">UniNotebook</span>
            </div>
          </div>

          {user && (onSelectWorkspace || onSelectKnowledgeGraph) && (
            <div className="home-nav-links">
              {onSelectWorkspace && (
                <button
                  type="button"
                  className={`home-nav-link ${activeNav === 'workspace' ? 'active' : ''}`}
                  onClick={onSelectWorkspace}
                >
                  我的学习空间
                </button>
              )}
              {onSelectKnowledgeGraph && (
                <button
                  type="button"
                  className={`home-nav-link ${activeNav === 'knowledgeGraph' ? 'active' : ''}`}
                  onClick={onSelectKnowledgeGraph}
                >
                  我的知识网络
                </button>
              )}
              {onSelectCreativeStudio && (
                <button
                  type="button"
                  className={`home-nav-link ${activeNav === 'creativeStudio' ? 'active' : ''}`}
                  onClick={onSelectCreativeStudio}
                >
                  我的创作空间
                </button>
              )}
              {onSelectPolish && (
                <button
                  type="button"
                  className={`home-nav-link ${activeNav === 'polish' ? 'active' : ''}`}
                  onClick={onSelectPolish}
                >
                  文本仿写
                </button>
              )}

            </div>
          )}


          <div className="nav-title-container">
            {titleComponent ? titleComponent : (title && <h2 className="nav-title">{title}</h2>)}
          </div>
          {user ? (
            <div className="home-user-menu">
              {/* 问题报告：本地版报告只会写进本机数据库，没人会看到，故不展示 */}
              {!isLocalMode && (
                <button className="invite-friend-btn report-issue-btn" onClick={() => setShowReportModal(true)}>
                  <span>问题报告</span>
                </button>
              )}

              {/* 邀请入口（本地版无账号体系，整体不加载） */}
              {!isLocalMode && <InviteEntry />}
              {!isLocalMode && <CheckinHintWatcher onCheckinHint={setShowCheckinArrow} />}

              <div className="home-user-avatar-wrap">
                <button className="home-user-button" onClick={() => setShowUserMenu(!showUserMenu)}>
                  <span className="home-user-avatar">{user.username[0].toUpperCase()}</span>
                </button>

                {/* 🆕 签到提示：固定在头像正下方 */}
                {showCheckinArrow && !showUserMenu && (
                  <div className="checkin-reminder-container">
                    <div className="checkin-reminder-text">签到领积分</div>
                  </div>
                )}
              </div>

              {showUserMenu && (
                <div className="home-user-dropdown">
                  <div className="home-dropdown-item home-user-info-dropdown">
                    <div className="home-user-email">{user.email}</div>
                    <div className="home-user-full-name">{user.full_name || user.username}</div>
                  </div>

                  {/* 手机端导航链接 */}
                  <div className="mobile-nav-links">
                    {onSelectWorkspace && (
                      <button
                        type="button"
                        className={`home-dropdown-item ${activeNav === 'workspace' ? 'active' : ''}`}
                        onClick={() => { onSelectWorkspace(); setShowUserMenu(false); }}
                      >
                        我的学习空间
                      </button>
                    )}
                    {onSelectKnowledgeGraph && (
                      <button
                        type="button"
                        className={`home-dropdown-item ${activeNav === 'knowledgeGraph' ? 'active' : ''}`}
                        onClick={() => { onSelectKnowledgeGraph(); setShowUserMenu(false); }}
                      >
                        我的知识网络
                      </button>
                    )}
                    {onSelectPolish && (
                      <button
                        type="button"
                        className={`home-dropdown-item ${activeNav === 'polish' ? 'active' : ''}`}
                        onClick={() => { onSelectPolish(); setShowUserMenu(false); }}
                      >
                        文本仿写
                      </button>
                    )}
                  </div>

                  {/* 签到 / 积分 / 充值：本地版无计费体系，整体不加载 */}
                  {!isLocalMode && (
                    <AccountMenuExtras
                      menuOpen={showUserMenu}
                      onSelectRecharge={onSelectRecharge}
                      onNavigate={() => setShowUserMenu(false)}
                      onCheckinHint={setShowCheckinArrow}
                    />
                  )}

                  {/* 本地版：API Key 设置入口 */}
                  {isLocalMode && onSelectSettings && (
                    <button
                      className="home-dropdown-item"
                      onClick={() => {
                        onSelectSettings()
                        setShowUserMenu(false)
                      }}
                    >
                      <span>设置</span>
                    </button>
                  )}
                  {/* 登出：本地版是固定单用户，没有可退出的账号。
                      若保留，点击后会退到落地页却无处登录，形成死路。 */}
                  {!isLocalMode && (
                    <button className="home-dropdown-item" onClick={onLogout}>
                      登出
                    </button>
                  )}
                </div>
              )}
            </div>
          ) : (
            // 本地版免登录，不展示登录入口
            !hideLoginButton && !isLocalMode && (
              <div className="home-login-container">
                <button className="home-login-button-small" onClick={onShowAuthModal}>
                  登录
                </button>
              </div>
            )
          )}
        </div>
      </div>

      {/* 问题报告弹窗（本地版不加载） */}
      {!isLocalMode && (
        <ReportIssueModal
          isOpen={showReportModal}
          onClose={() => setShowReportModal(false)}
        />
      )}
    </>
  )
}

Navbar.propTypes = {
  onLogout: PropTypes.func,
  onLogoClick: PropTypes.func,
  title: PropTypes.string,
  titleComponent: PropTypes.node,
  activeNav: PropTypes.oneOf(['workspace', 'knowledgeGraph', 'creativeStudio', 'polish', 'recharge']),
  onSelectWorkspace: PropTypes.func,
  onSelectKnowledgeGraph: PropTypes.func,
  onSelectPolish: PropTypes.func,
  onSelectCreativeStudio: PropTypes.func,
  onSelectRecharge: PropTypes.func,
  onSelectSettings: PropTypes.func,
  hideLoginButton: PropTypes.bool,
}

export default Navbar

