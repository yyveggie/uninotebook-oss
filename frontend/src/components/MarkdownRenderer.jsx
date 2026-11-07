/**
 * MarkdownRenderer 组件
 * 
 * 封装 ReactMarkdown + LaTeX 渲染的统一组件。
 * 当前行为：始终直接渲染原始 Markdown 文本，不做额外 LaTeX 预处理。
 */

import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'

// KaTeX 配置：支持 CJK 字符和更宽松的解析
const katexOptions = {
    // 内容来自 LLM / 文档，属于不可信输入：只放行 http(s) 链接的 \href / \url，
    // 拒绝 \htmlStyle、\htmlData、\includegraphics 等可注入样式或外链资源的命令
    trust: (context) =>
        (context.command === '\\href' || context.command === '\\url') &&
        (context.protocol === 'http' || context.protocol === 'https'),
    // 严格模式设置为 'ignore' 完全静默 CJK 字符警告
    strict: 'ignore',
    // 错误时不抛出异常，显示错误信息
    throwOnError: false,
    // 允许的协议（用于 \href 等）
    allowedProtocols: ['http', 'https'],
    // 宏定义：可以添加自定义命令
    macros: {
        "\\R": "\\mathbb{R}",
        "\\N": "\\mathbb{N}",
        "\\Z": "\\mathbb{Z}",
        "\\Q": "\\mathbb{Q}",
        "\\C": "\\mathbb{C}",
    }
}

/**
 * 错误边界组件：捕获 Markdown/LaTeX 渲染错误
 */
class MarkdownErrorBoundary extends React.Component {
    constructor(props) {
        super(props)
        this.state = { hasError: false, error: null }
    }

    static getDerivedStateFromError(error) {
        return { hasError: true, error }
    }

    componentDidCatch(error) {
        console.warn('[MarkdownRenderer] 渲染错误:', error.message)
    }

    render() {
        if (this.state.hasError) {
            // 渲染失败时显示原始文本
            return (
                <div className="markdown-fallback" style={{
                    whiteSpace: 'pre-wrap',
                    fontFamily: 'inherit',
                    color: 'inherit'
                }}>
                    {this.props.fallbackContent}
                </div>
            )
        }
        return this.props.children
    }
}

/**
 * 内部渲染组件（被错误边界包裹）
 */
function MarkdownContent({ content, components }) {
    // 不启用 rehype-raw：渲染的是 LLM / 文档内容，原始 HTML 一律按文本处理
    const rehypePlugins = [[rehypeKatex, katexOptions]]

    return (
        <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={rehypePlugins}
            components={components}
        >
            {content}
        </ReactMarkdown>
    )
}

/**
 * Markdown 渲染组件，支持 LaTeX 公式
 * 
 * @param {Object} props
 * @param {string} props.children - Markdown 内容
 * @param {Object} [props.components={}] - 自定义 ReactMarkdown 组件
 * @param {string} [props.className=''] - CSS 类名
 * @param {boolean} [props.allowHtml=false] - 已废弃：出于安全考虑不再渲染原始 HTML（保留以兼容旧调用方）
 * @param {boolean} [props.isStreaming=false] - 保留字段（兼容旧调用方）
 */
export function MarkdownRenderer({
    children,
    components = {},
    className = '',
}) {
    // 安全检查：确保 children 是字符串
    const rawContent = children != null ? String(children) : ''


    // 预处理内容：修复常见的 LaTeX 格式问题
    const preprocessContent = (text) => {
        if (!text) return ''

        // 1. 拆分 block math $$...$$
        // 使用非贪婪匹配，且这简单的正则无法处理嵌套 $$，但对 LLM 输出通常足够
        const blockParts = text.split(/(\$\$[\s\S]*?\$\$)/g)

        const processed = blockParts.map((part, index) => {
            // 偶数索引是普通文本（或包含 inline math）
            if (index % 2 === 0) {
                // 2. 拆分 inline math $...$
                const inlineParts = part.split(/(\$[^$\n]*?\$)/g)

                return inlineParts.map((subPart, subIndex) => {
                    if (subIndex % 2 === 0) {

                        // 替换常见的数学符号和箭头，包裹在 $...$ 中
                        // 包含：箭头、关系符、运算符、希腊字母等
                        const latexPatterns = [
                            // 箭头
                            'rightarrow', 'leftarrow', 'leftrightarrow', 'Rightarrow', 'Leftarrow', 'Leftrightarrow', 'mapsto', 'to',
                            // 关系符
                            'approx', 'sim', 'cong', 'equiv', 'neq', 'leq?', 'geq?', 'subset', 'supset', 'in', 'notin', 'propto',
                            // 运算符
                            'times', 'div', 'pm', 'mp', 'cdot', 'circ',
                            // 希腊字母 (小写)
                            'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta', 'iota', 'kappa', 'lambda', 'mu', 'nu', 'xi', 'pi', 'rho', 'sigma', 'tau', 'upsilon', 'phi', 'chi', 'psi', 'omega',
                            // 希腊字母 (大写)
                            'Gamma', 'Delta', 'Theta', 'Lambda', 'Xi', 'Pi', 'Sigma', 'Upsilon', 'Phi', 'Psi', 'Omega',
                            // 其他
                            'infty', 'partial', 'nabla', 'forall', 'exists', 'empty'
                        ].join('|')

                        // 构造正则：匹配以 \ 开头，后面跟上述关键字，且后续不是字母的字符
                        // 替换为： $match$
                        const regex = new RegExp(`\\\\(${latexPatterns})(?![a-zA-Z])`, 'g')

                        return subPart.replace(regex, ' $$$&$$ ')
                    }
                    // 这是 math 区域，保持原样
                    return subPart
                }).join('')
            }
            // 这是 block math 区域，保持原样
            return part
        }).join('')

        // 3. 🆕 自动包裹独立的 LaTeX 环境 (从后端迁移至此)
        // Fix for raw environments not rendering in frontend
        // 如果 \begin{...} 没有被包裹在 $ 或 $$ 中，则强制包裹 $$
        // JS 正则不支持后顾(?<!), 使用捕获组替换
        const envPattern = /(\\begin\{(aligned|align|equation|gather|split)\}[\s\S]*?\\end\{\2\})/g
        return processed.replace(envPattern, (match) => {
            // 简单检查是否已经被包裹 (并不完美，但够用)
            // 如果 match 周围已经是 $$，则忽略 (这里简化处理，假设 LLM 输出要么完全没包裹，要么包裹了)
            // 更严谨的方法是检查整个字符串索引，但对于 stream 输出太重了
            return `$$\n${match}\n$$`
        })
    }

    const content = preprocessContent(rawContent)

    return (
        <div className={className}>
            <MarkdownErrorBoundary fallbackContent={rawContent}>
                <MarkdownContent
                    content={content}
                    components={components}
                />
            </MarkdownErrorBoundary>
        </div>
    )
}

export default MarkdownRenderer
