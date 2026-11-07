import React, { useRef } from 'react';
import { MarkdownRenderer } from '../MarkdownRenderer';
import './MarkdownEditor.css';

export function MarkdownEditor({ value, onChange, placeholder, style, height = 400 }) {
    const textareaRef = useRef(null);

    const insertText = (before, after = '') => {
        const textarea = textareaRef.current;
        if (!textarea) return;

        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const text = textarea.value;
        const selectedText = text.substring(start, end);

        const newText = text.substring(0, start) + before + selectedText + after + text.substring(end);
        onChange(newText);

        setTimeout(() => {
            textarea.focus();
            textarea.setSelectionRange(start + before.length, end + before.length);
        }, 0);
    };

    const toolbarItems = [
        { label: 'H1', tooltip: '一级标题', action: () => insertText('# ') },
        { label: 'H2', tooltip: '二级标题', action: () => insertText('## ') },
        { label: 'H3', tooltip: '三级标题', action: () => insertText('### ') },
        { label: 'B', tooltip: '粗体', action: () => insertText('**', '**') },
        { label: 'I', tooltip: '斜体', action: () => insertText('*', '*') },
        { label: '<>', tooltip: '代码', action: () => insertText('`', '`') },
        { label: '{}', tooltip: '代码块', action: () => insertText('```\n', '\n```') },
        { label: '•', tooltip: '无序列表', action: () => insertText('- ') },
        { label: '1.', tooltip: '有序列表', action: () => insertText('1. ') },
        { label: '""', tooltip: '引用', action: () => insertText('> ') },
    ];

    return (
        <div className="markdown-editor mode-split" style={{ ...style, height }}>
            <div className="markdown-editor-header">
                <div className="markdown-toolbar">
                    {toolbarItems.map((item, index) => (
                        <button
                            key={index}
                            className="markdown-tool-btn"
                            onClick={item.action}
                            title={item.tooltip}
                            type="button"
                        >
                            {item.label}
                        </button>
                    ))}
                </div>
                {/* Tabs removed as requested */}
            </div>

            <div className="markdown-editor-body">
                {/* Editor Area */}
                <div className="markdown-pane editor-pane visible">
                    <textarea
                        ref={textareaRef}
                        className="markdown-textarea"
                        placeholder={placeholder}
                        value={value}
                        onChange={(e) => onChange(e.target.value)}
                    />
                </div>

                {/* Preview Area */}
                <div className="markdown-pane preview-pane visible">
                    <div className="markdown-body">
                        {value ? (
                            <MarkdownRenderer>
                                {value}
                            </MarkdownRenderer>
                        ) : (
                            <div className="markdown-preview-empty">实时预览区域</div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
