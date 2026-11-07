import React from 'react'
import { MarkdownEditor } from '../ui/MarkdownEditor'

const NoteEditorModal = ({
  open,
  editingNoteId,
  isSavingNote,
  noteTitle,
  noteContent,
  setNoteTitle,
  setNoteContent,
  onOverlayClose,
  onCancel,
  onSave,
}) => {
  if (!open) {
    return null
  }

  return (
    <div className="note-preview-overlay" onClick={onOverlayClose}>
      <div className="note-preview-modal" onClick={(e) => e.stopPropagation()}>
        <div className="note-preview-content">
          <div className="note-dialog-toolbar">
            <h3 className="note-dialog-title">{editingNoteId ? '编辑笔记' : '创建笔记'}</h3>
            <div className="note-dialog-actions">
              <button
                className="preview-cancel-btn slim"
                onClick={onCancel}
                disabled={isSavingNote}
              >
                取消
              </button>
              <button
                className="preview-save-btn primary slim"
                onClick={onSave}
                disabled={isSavingNote}
              >
                {isSavingNote ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
          <div className="note-form-group">
            <label className="note-form-label">标题</label>
            <input
              type="text"
              className="note-form-input"
              value={noteTitle}
              onChange={(e) => setNoteTitle(e.target.value)}
              autoFocus
            />
          </div>
          <div className="note-form-group" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <label className="note-form-label">内容</label>
            <MarkdownEditor
              value={noteContent}
              onChange={setNoteContent}
              height="100%"
              style={{ flex: 1 }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

export default NoteEditorModal