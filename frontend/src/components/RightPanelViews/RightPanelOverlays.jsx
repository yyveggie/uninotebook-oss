import React from 'react'
import ConfirmDialog from '../ConfirmDialog'
import InterviewDialog from '../InterviewDialog'

const RightPanelOverlays = ({
  deleteConfirmState,
  onDeleteConfirm,
  onDeleteCancel,
  errorDialogState,
  onErrorClose,
  showInterviewDialog,
  documentId,
  resumeInterviewData,
  onInterviewClose,
}) => {
  return (
    <>
      <ConfirmDialog
        isOpen={deleteConfirmState.isOpen}
        title="确认删除"
        message={deleteConfirmState.title}
        confirmLabel="删除"
        isDangerous={true}
        onConfirm={onDeleteConfirm}
        onCancel={onDeleteCancel}
      />

      <ConfirmDialog
        isOpen={errorDialogState.isOpen}
        type="alert"
        title="提示"
        message={errorDialogState.message}
        confirmLabel="知道了"
        onConfirm={onErrorClose}
        onCancel={onErrorClose}
      />

      {showInterviewDialog && documentId && (
        <InterviewDialog
          documentId={documentId}
          resumeData={resumeInterviewData}
          onClose={onInterviewClose}
        />
      )}
    </>
  )
}

export default RightPanelOverlays