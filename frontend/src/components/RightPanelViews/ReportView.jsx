import React from 'react'
import NoteView from './NoteView'

const REPORT_COPY = {
  defaultTitle: '长报告',
  generatingTitle: '长报告生成中...',
  emptyContent: '暂无报告内容',
  generatingContent: '报告正在生成，请稍候。',
}

const ReportView = ({
  report,
  tempReport,
  isGeneratingReport,
  onBack,
}) => {
  const displayReport = tempReport || report
  const reportAsNote = displayReport
    ? {
        id: displayReport.id,
        title: displayReport.title || REPORT_COPY.defaultTitle,
        content: displayReport.report_markdown || REPORT_COPY.emptyContent,
      }
    : {
        id: null,
        title: isGeneratingReport ? REPORT_COPY.generatingTitle : REPORT_COPY.defaultTitle,
        content: isGeneratingReport ? REPORT_COPY.generatingContent : REPORT_COPY.emptyContent,
      }

  return (
    <NoteView
      tempNote={reportAsNote}
      onBack={onBack}
      canEdit={false}
      onEdit={() => {}}
    />
  )
}

export default ReportView