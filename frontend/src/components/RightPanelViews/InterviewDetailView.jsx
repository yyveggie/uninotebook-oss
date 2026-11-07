import React from 'react'
import BackButton from '../BackButton'

const InterviewDetailView = ({
  selectedInterviewDetail,
  isResumingInterview,
  onBack,
  onResumeInterview,
}) => {
  if (!selectedInterviewDetail) {
    return null
  }

  return (
    <div className="interview-detail-view">
      <div className="interview-detail-header">
        <BackButton onClick={onBack} />
        <h3>面试记录</h3>
        {selectedInterviewDetail.status === 'aborted' && (
          <button
            className="resume-interview-btn"
            onClick={() => onResumeInterview(selectedInterviewDetail.id)}
            disabled={isResumingInterview}
          >
            {isResumingInterview ? '恢复中...' : '继续面试'}
          </button>
        )}
      </div>

      <div className="interview-detail-content">
        <div className="interview-section">
          <h4 className="section-title">总体情况</h4>
          <div className="info-row">
            <span className="info-label">总轮次</span>
            <span className="info-value">{selectedInterviewDetail.total_rounds}</span>
          </div>
          <div className="info-row">
            <span className="info-label">平均分</span>
            <span className="info-value">{selectedInterviewDetail.average_score.toFixed(1)}</span>
          </div>
          <div className="info-row">
            <span className="info-label">状态</span>
            <span className="info-value">
              {selectedInterviewDetail.status === 'completed' ? '已完成' : '已中止'}
            </span>
          </div>
        </div>

        {selectedInterviewDetail.summary && (
          <div className="interview-section">
            <h4 className="section-title">面试总结</h4>

            <div className="subsection">
              <h5 className="subsection-title">优势领域</h5>
              <ul className="simple-list">
                {selectedInterviewDetail.summary.strengths?.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </div>

            <div className="subsection">
              <h5 className="subsection-title">需要改进</h5>
              <ul className="simple-list">
                {selectedInterviewDetail.summary.weaknesses?.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>

            <div className="subsection">
              <h5 className="subsection-title">总体评价</h5>
              <p className="subsection-text">{selectedInterviewDetail.summary.overall_assessment}</p>
            </div>

            <div className="subsection">
              <h5 className="subsection-title">学习建议</h5>
              <p className="subsection-text">{selectedInterviewDetail.summary.recommendations}</p>
            </div>
          </div>
        )}

        <div className="interview-section">
          <h4 className="section-title">问答记录</h4>
          {selectedInterviewDetail.qa_history?.map((qa, index) => (
            <div key={index} className="qa-item">
              <div className="qa-meta">
                <span>第 {qa.round} 轮</span>
                <span className="qa-score-text">{qa.evaluation.score.toFixed(0)} 分</span>
              </div>

              <div className="qa-block">
                <div className="qa-label">问题</div>
                <div className="qa-content">{qa.question}</div>
              </div>

              <div className="qa-block">
                <div className="qa-label">回答</div>
                <div className="qa-content">{qa.answer}</div>
              </div>

              <div className="qa-block">
                <div className="qa-label">反馈</div>
                <div className="qa-content">{qa.evaluation.feedback}</div>
              </div>

              <div className="qa-block">
                <div className="qa-label">建议</div>
                <div className="qa-content">{qa.evaluation.suggestions}</div>
              </div>

              <div className="qa-block">
                <div className="qa-label">关键词覆盖率</div>
                <div className="qa-content">{(qa.evaluation.keyword_coverage * 100).toFixed(0)}%</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default InterviewDetailView