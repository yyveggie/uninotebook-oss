import { useState, useEffect, useRef } from 'react'
import { apiRequest, buildApiUrl } from '../utils/api'
import './PodcastPlayer.css'

function PodcastPlayer({ taskId, onClose, onStatusChange }) {
    const [status, setStatus] = useState('loading')
    const [progress, setProgress] = useState(0)
    const [script, setScript] = useState([])
    const [audioUrl, setAudioUrl] = useState(null)
    const [error, setError] = useState(null)
    const [isMinimized, setIsMinimized] = useState(false)
    const audioRef = useRef(null)

    useEffect(() => {
        if (!taskId) return

        let pollInterval

        const checkStatus = async () => {
            try {
                const res = await apiRequest(`/api/podcast/status/${taskId}`)
                const data = await res.json()

                setStatus(data.status)
                setProgress(data.progress)
                if (data.script) setScript(data.script)

                // Notify parent of status change (to update UI or close player)
                if (onStatusChange) {
                    onStatusChange(data.status)
                }

                if (data.status === 'completed') {
                    // 优先使用后端返回的（带签名的）audio_url；旧版后端没有该字段时才自行拼路径。
                    // 相对路径经 buildApiUrl 解析，以便配置 VITE_API_BASE_URL 时指向后端所在域
                    const backendAudioUrl = data.audio_url
                    if (backendAudioUrl) {
                        setAudioUrl(/^https?:\/\//i.test(backendAudioUrl) ? backendAudioUrl : buildApiUrl(backendAudioUrl))
                    } else {
                        setAudioUrl(buildApiUrl(`/api/podcast/audio/${taskId}`))
                    }
                    clearInterval(pollInterval)
                } else if (data.status === 'failed') {
                    setError(data.error || '生成失败')
                    clearInterval(pollInterval)
                }
            } catch (e) {
                console.error("Poll error:", e)
            }
        }

        checkStatus()
        pollInterval = setInterval(checkStatus, 3000) // Poll every 3s

        return () => clearInterval(pollInterval)
    }, [taskId])

    if (!taskId) return null

    // User requested to hide the "generating" popup.
    // Only show player when completed or failed.
    // The main studio card will show the progress status.
    if (status !== 'completed' && status !== 'failed') {
        return null
    }

    return (
        <div className={`podcast-player-overlay ${isMinimized ? 'minimized' : ''}`}>
            {isMinimized ? (
                <div className="minimized-content" onClick={() => setIsMinimized(false)}>
                    <div className="minimized-icon">🎙️</div>
                    {/* Keep audio mounted but hidden if needed, though standard practice is just to hide the UI container */}
                </div>
            ) : (
                <div className="expanded-content">
                    <div className="podcast-header">
                        <div className="podcast-info">
                            <h3>AI 播客</h3>
                        </div>
                        <div className="podcast-controls">
                            <button className="podcast-btn" onClick={() => setIsMinimized(true)}>—</button>
                            <button className="podcast-btn" onClick={onClose}>✕</button>
                        </div>
                    </div>

                    <div className="podcast-content">
                        {status !== 'completed' && status !== 'failed' && (
                            <div className="podcast-progress">
                                <div className="spinner"></div>
                                <p className="status-text">{status} ({progress}%)</p>
                                <p style={{ fontSize: '12px', color: '#94a3b8', marginTop: '8px' }}>
                                    {status === 'processing' ? '正在撰写剧本与合成语音...' : '准备中...'}
                                </p>
                            </div>
                        )}

                        {status === 'failed' && (
                            <div className="error-msg" style={{ color: 'red', textAlign: 'center' }}>
                                Error: {error}
                            </div>
                        )}

                        {script && script.length > 0 && (
                            <div className="script-container">
                                {script.map((line, idx) => (
                                    <div key={idx} className={`script-line speaker-${line.speaker}`}>
                                        <div className="avatar">
                                            {line.speaker === 'A' ? '🎙️' : '🧠'}
                                        </div>
                                        <div className="bubble">
                                            {line.text}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Audio Element - Always rendered if we have URL, regardless of minimized state */}
            {status === 'completed' && audioUrl && (
                <div className="audio-controls-bar" style={isMinimized ? { display: 'none' } : {}}>
                    <audio
                        ref={audioRef}
                        controls
                        src={audioUrl}
                        style={{ width: '100%', height: '36px' }}
                        autoPlay
                    />
                </div>
            )}
        </div>
    )
}

export default PodcastPlayer
