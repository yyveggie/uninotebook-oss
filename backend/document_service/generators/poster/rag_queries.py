# Optimized RAG Queries for Academic Poster
# 分成 3 批，每批 2 个类别

# 第一批：论文基本信息 + 动机
BATCH_1 = {
    "paper_info": [
        "列出论文标题、作者姓名及其所属机构。",
    ],
    "motivation": [
        "本文旨在解决什么问题或任务？请描述具体的挑战。",
    ],
}

# 第二批：方法 + 结果
BATCH_2 = {
    "method": [
        "本文提出了什么方法、途径或框架？请提供概述。",
    ],
    "results": [
        "主要实验结果是什么？与基线方法相比表现如何？请给出具体数值。",
    ],
}

# 第三批：贡献 + 图表
BATCH_3 = {
    "contributions": [
        "论文的主要贡献是什么？有什么创新之处？",
    ],
    "figures": [
        "描述论文的架构图或框架图。图中展示了哪些主要组件？",
        "论文中最重要的结果图表是什么？展示了什么信息？",
    ],
}

# 所有批次
POSTER_RAG_BATCHES = [BATCH_1, BATCH_2, BATCH_3]

# 兼容旧代码
POSTER_RAG_QUERIES = {
    **BATCH_1,
    **BATCH_2,
    **BATCH_3,
}
