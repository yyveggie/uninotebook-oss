
import { useState, useEffect } from 'react'
import { apiPut, apiRequest, createApiError } from '../utils/api'
import useUIStore from '../stores/uiStore'

const stripOuterQuotes = (text) => {
    if (!text) return ''
    return String(text)
        .replace(/^[\s`'"“”‘’]+/, '')
        .replace(/[\s`'"“”‘’]+$/, '')
}

const formatKeyTermDisplay = (raw) => {
    if (!raw) return ''
    const str = String(raw)
    const parts = str.split(/\s*\/\s*/)
    if (parts.length >= 2) {
        const left = stripOuterQuotes(parts[0] || '')
        const right = stripOuterQuotes(parts.slice(1).join(' / '))
        return right ? `${left} / ${right}` : left
    }
    return stripOuterQuotes(str)
}

/**
 * Key Term Editor Logic Hook
 */
export const useKeyTermEditor = (documentId) => {
    const [terms, setTerms] = useState([])
    const [newTerm, setNewTerm] = useState('')
    const [isLoading, setIsLoading] = useState(false)
    const [isSaving, setIsSaving] = useState(false)
    const [isDirty, setIsDirty] = useState(false)
    const [loadFailed, setLoadFailed] = useState(false)

    // Load terms
    useEffect(() => {
        if (!documentId) return
        // 快速切换文档时，丢弃上一篇迟到的响应，免得把它的关键词保存到这一篇
        let ignore = false

        const loadTerms = async () => {
            setIsLoading(true)
            setLoadFailed(false)
            try {
                const response = await apiRequest(`/api/knowledge-graph/document/${documentId}/terms`)
                if (!response.ok) throw await createApiError(response)
                const data = await response.json()
                if (!data?.success || !Array.isArray(data.data)) throw new Error('返回数据格式异常')
                if (ignore) return
                setTerms(
                    data.data.map((item) => ({
                        id: item.id,
                        term: formatKeyTermDisplay(item.term || ''),
                        description: item.description || ''
                    }))
                )
                setIsDirty(false)
            } catch (err) {
                if (ignore) return
                // 保存是覆盖式的：没加载到现有关键词就保存，会把它们全部清掉，所以加载失败时禁止编辑
                setLoadFailed(true)
                useUIStore.getState().showToast(`关键词加载失败：${err?.message || '请刷新后重试'}`)
            } finally {
                if (!ignore) setIsLoading(false)
            }
        }

        loadTerms()
        return () => {
            ignore = true
        }
    }, [documentId])

    const handleChangeTerm = (index, value) => {
        setTerms((prev) => {
            const next = [...prev]
            next[index] = { ...next[index], term: value }
            return next
        })
        setIsDirty(true)
    }

    const handleRemove = (index) => {
        setTerms((prev) => prev.filter((_, i) => i !== index))
        setIsDirty(true)
    }

    const handleAdd = () => {
        const value = newTerm.trim()
        if (!value) return
        setTerms((prev) => [
            ...prev,
            { id: null, term: value, description: '' }
        ])
        setNewTerm('')
        setIsDirty(true)
    }

    const handleSave = async () => {
        if (!documentId || loadFailed) return
        setIsSaving(true)
        try {
            const payload = {
                terms: terms
                    .map((t) => ({
                        term: (t.term || '').trim(),
                        description: (t.description || '').trim()
                    }))
                    .filter((t) => t.term)
            }
            await apiPut(`/api/knowledge-graph/document/${documentId}/terms`, payload)
            setIsDirty(false)
        } catch (err) {
            useUIStore.getState().showToast(`关键词保存失败：${err?.message || '请稍后重试'}`)
        } finally {
            setIsSaving(false)
        }
    }

    return {
        terms,
        newTerm,
        setNewTerm,
        isLoading,
        isSaving,
        isDirty,
        loadFailed,
        handleChangeTerm,
        handleRemove,
        handleAdd,
        handleSave
    }
}
