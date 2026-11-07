import React from 'react'

const GenerationConfigModal = ({
  open,
  title,
  onClose,
  onSubmit,
  submitLabel = '生成',
  isSubmitting = false,
  submitDisabled = false,
  groups = [],
  textFields = [],
  children,
}) => {
  if (!open) {
    return null
  }

  return (
    <div className="note-preview-overlay" onClick={onClose}>
      <div className="note-preview-modal generation-custom-note-modal" onClick={(e) => e.stopPropagation()}>
        <div className="note-preview-content generation-custom-content">
          <div className="note-dialog-toolbar">
            <h3 className="note-dialog-title">{title}</h3>
            <div className="note-dialog-actions">
              <button
                type="button"
                className="preview-cancel-btn slim"
                onClick={onClose}
                disabled={isSubmitting}
              >
                取消
              </button>
              <button
                type="button"
                className="preview-save-btn primary slim"
                onClick={onSubmit}
                disabled={isSubmitting || submitDisabled}
              >
                {isSubmitting ? '提交中...' : submitLabel}
              </button>
            </div>
          </div>

          {groups.length > 0 && (
            <div className="generation-custom-grid">
              {groups.map((group) => (
                <div
                  className="generation-custom-group"
                  key={group.key || group.label}
                  style={group.fullWidth ? { gridColumn: '1 / -1' } : undefined}
                >
                  <label className="note-form-label">{group.label}</label>
                  {group.description && (
                    <div className="generation-custom-group-description">{group.description}</div>
                  )}
                  <div
                    className={`generation-custom-options ${group.optionsClassName || ''}`.trim()}
                    style={group.noWrap ? { flexWrap: 'nowrap', overflowX: 'auto' } : undefined}
                  >
                    {group.options.map((option) => (
                      <button
                        type="button"
                        key={option.value}
                        className={`generation-custom-pill ${group.value === option.value ? 'active' : ''} ${option.description ? 'has-description' : ''}`.trim()}
                        onClick={() => group.onChange(option.value)}
                        disabled={isSubmitting}
                        style={group.noWrap ? { whiteSpace: 'nowrap', flex: '0 0 auto' } : undefined}
                      >
                        <span className="generation-custom-pill-label">{option.label}</span>
                        {option.description && (
                          <span className="generation-custom-pill-description">{option.description}</span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {textFields.map((field) => (
            <div className="note-form-group generation-custom-field" key={field.key || field.label}>
              <label className="note-form-label">{field.label}</label>
              {field.multiline ? (
                <textarea
                  className="note-form-input generation-custom-textarea"
                  placeholder={field.placeholder}
                  value={field.value}
                  onChange={(e) => field.onChange(e.target.value)}
                  disabled={isSubmitting}
                />
              ) : (
                <input
                  type="text"
                  className="note-form-input"
                  placeholder={field.placeholder}
                  value={field.value}
                  onChange={(e) => field.onChange(e.target.value)}
                  disabled={isSubmitting}
                />
              )}
            </div>
          ))}

          {children}
        </div>
      </div>
    </div>
  )
}

export default GenerationConfigModal
