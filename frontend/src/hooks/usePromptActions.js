import { useEffect } from 'react'
import { apiGet, apiRequest, getApiErrorMessage } from '../utils/api'
import useAuthStore from '../stores/authStore'
import useDocumentStore from '../stores/documentStore'
import useUIStore from '../stores/uiStore'

/**
 * 提示词模板 + 快捷 prompt hook
 * 所有函数名/参数名与原 App.jsx 完全一致
 */
export default function usePromptActions(documentChatRef) {
  const getUI = () => useUIStore.getState()

  // ========== fetchPromptTemplates ==========
  const fetchPromptTemplates = async (forceRefresh = false) => {
    const user = useAuthStore.getState().user
    if (!user) return
    try {
      const data = await apiGet('/api/prompt-templates', {
        cacheMode: forceRefresh ? 'no-cache' : 'default'
      })
      if (data.success && data.data) {
        getUI().setPromptTemplates(data.data)
      }
    } catch (err) {
      console.error('获取提示词模板失败:', err)
    }
  }

  // ========== handleTemplateClick ==========
  const handleTemplateClick = (template) => {
    if (!useDocumentStore.getState().currentDocument || !documentChatRef.current) return
    documentChatRef.current.sendQuestion(template.prompt)
  }

  // ========== handleTemplateEdit ==========
  const handleTemplateEdit = (template) => {
    getUI().setEditingTemplate(template)
    getUI().setShowPromptModal(true)
  }

  // ========== handleTemplateSave ==========
  const handleTemplateSave = async (templateData) => {
    if (templateData.id) {
      const response = await apiRequest(`/api/prompt-templates/${templateData.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: templateData.title,
          icon: templateData.icon,
          prompt: templateData.prompt
        })
      })
      const data = await response.json()
      if (!data.success) throw new Error(getApiErrorMessage(data, '更新失败'))
    } else {
      const response = await apiRequest('/api/prompt-templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: templateData.title,
          icon: templateData.icon,
          prompt: templateData.prompt
        })
      })
      const data = await response.json()
      if (!data.success) throw new Error(getApiErrorMessage(data, '创建失败'))
    }
    await fetchPromptTemplates(true)
  }

  // ========== handleTemplateDelete ==========
  const handleTemplateDelete = async (templateId) => {
    const response = await apiRequest(`/api/prompt-templates/${templateId}`, {
      method: 'DELETE'
    })
    const data = await response.json()
    if (!data.success) throw new Error(getApiErrorMessage(data, '删除失败'))
    await fetchPromptTemplates(true)
  }

  // ========== Quick prompts: 通过对话触发 ==========

  const handleQuickKeyPointsLogic = () => {
    if (!useDocumentStore.getState().currentDocument || !documentChatRef.current) return

    const prompt = `请系统梳理这篇文档的关键观点，并明确每个观点背后的论证逻辑链条。

## 🔑 关键观点清单

- 列出3-6个核心观点
- 每个观点包含：一句话概括、核心结论、针对的对象或问题

## 🧠 论证支撑

对每个观点，补充以下信息：
1. 论据来源：引用了哪些数据、案例或权威观点？
2. 推理方式：使用了演绎、归纳、类比还是反证？
3. 逻辑过渡：从前一个观点如何过渡到当前观点？

## 🧩 全局逻辑结构

- 用流程或层级描述从开篇设问到最终结论的推理路径
- 标记关键转折点与逻辑节点（如假设、反驳、总结）
- 指出论证中可能的薄弱环节或仍需补强的证据

请保持结构化输出，确保读者能一目了然地看到观点之间的逻辑关系。`

    documentChatRef.current.sendQuestion(prompt)
  }

  const handleQuickCentralIdea = () => {
    if (!useDocumentStore.getState().currentDocument || !documentChatRef.current) return

    const prompt = `请提取并阐述这篇文档的中心思想——即作者最想传达的核心观点、主旨或立场。

## 💡 中心思想提取

### 1. 核心观点（Core Thesis）
**用一句话概括：作者到底想说什么？**
- 这句话应该是全文的"魂"
- 必须体现作者的立场或价值判断

### 2. 关键主张（Key Claims）
支撑中心思想的3-5个关键主张：
1. [主张1]：...
2. [主张2]：...
3. [主张3]：...
- 每个主张应该是中心思想的子观点
- 说明它们如何共同构成完整观点

### 3. 写作动机（Motivation）
作者为什么要传达这个思想？
- 想解决什么问题？
- 想改变读者什么认知？
- 针对什么现象或争议？

### 4. 立场与态度（Position）
作者对这个主题持什么态度？
- 支持/反对/中立/批判？
- 乐观/悲观/谨慎？
- 有什么价值倾向？

### 5. 意义与影响（Significance）
这个中心思想有什么重要性？
- 理论贡献
- 实践价值
- 对读者的启发

📌 重点：聚焦"作者想告诉我们什么"，而不是"文档包含什么内容"。要提炼出价值判断和立场。`

    documentChatRef.current.sendQuestion(prompt)
  }

  const handleQuickProsCons = () => {
    if (!useDocumentStore.getState().currentDocument || !documentChatRef.current) return

    const prompt = `请以第三方独立评审专家的视角，批判性地分析这篇文档的内容。

请从独立、客观、批判的角度，列出：

## 优点排行（Top 5）
1. [第一个优点] - 详细说明
2. [第二个优点] - 详细说明
3. [第三个优点] - 详细说明
4. [第四个优点] - 详细说明
5. [第五个优点] - 详细说明

## 缺点排行（Top 5）
1. [第一个缺点] - 详细说明
2. [第二个缺点] - 详细说明
3. [第三个缺点] - 详细说明
4. [第四个缺点] - 详细说明
5. [第五个缺点] - 详细说明

注意：
- 扮演黑白脸角色，既要肯定优点，也要毫不留情地指出缺点
- 不要参考作者自己在文档中提到的优缺点
- 从逻辑严密性、论证充分性、实用价值、创新性、可操作性等维度分析
- 每个点都要具体、有建设性`

    documentChatRef.current.sendQuestion(prompt)
  }

  const handleQuickSimpleExample = () => {
    if (!useDocumentStore.getState().currentDocument || !documentChatRef.current) return

    const prompt = `请用一个通俗易懂的例子，给小白解释这篇文档的核心方法/流程。

请按以下结构输出：

## 核心方法/流程是什么？
用1-2句话总结文档的核心方法或流程。

## 通俗例子
用一个日常生活中的具体例子来类比说明：
- 例子要贴近生活，让完全不懂的人也能理解
- 要把文档中的抽象概念映射到例子的具体元素上
- 要说明每个步骤在例子中对应什么

## 回到文档
把例子中的概念对应回文档，说明文档中的关键步骤/概念：
1. 第一个关键点
2. 第二个关键点
3. 第三个关键点
（以此类推）

## 小白总结
用最简单的话，一句话总结这个文档想要教什么。

注意：
- 例子要真的很简单，像给5岁小孩解释一样
- 避免使用专业术语，如果必须用要立刻解释
- 例子要有趣、生动、容易记住`

    documentChatRef.current.sendQuestion(prompt)
  }

  // ========== Effect: 加载用户登录后的提示词模板 ==========
  const user = useAuthStore(s => s.user)
  useEffect(() => {
    if (user) {
      fetchPromptTemplates()
    } else {
      getUI().setPromptTemplates([])
    }
  }, [user])

  return {
    fetchPromptTemplates,
    handleTemplateClick,
    handleTemplateEdit,
    handleTemplateSave,
    handleTemplateDelete,
    handleQuickKeyPointsLogic,
    handleQuickCentralIdea,
    handleQuickProsCons,
    handleQuickSimpleExample,
  }
}
