import React from 'react'
import { MarkdownRenderer } from '../MarkdownRenderer'
import BackButton from '../BackButton'

const NoteView = ({
  tempNote,
  onBack,
  onEdit,
  canEdit = true,
}) => {
  const displayNote = tempNote

  if (!displayNote) {
    return (
      <div className="note-view">
        <div className="view-header">
          <BackButton onClick={onBack} />
          <h3>笔记详情</h3>
        </div>
        <div className="view-empty">笔记不存在</div>
      </div>
    )
  }

  return (
    <div className="note-view">
      <div className="view-header">
        <BackButton onClick={onBack} />
        <h3>{displayNote.title}</h3>
        {canEdit && (
          <button
            className="edit-note-btn"
            onClick={() => onEdit(displayNote)}
            title="编辑笔记"
          >
            编辑
          </button>
        )}
      </div>

      <div className="note-content">
        <div className="note-text markdown-content">
          <MarkdownRenderer>
            {displayNote.content}
          </MarkdownRenderer>
        </div>
      </div>
    </div>
  )
}

export default NoteView
