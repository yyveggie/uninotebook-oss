from typing import Optional, List, Dict
from sqlalchemy import select
from db_models import Document, DocumentMindmap
from ..schema import MindMapResponse, MindMapNode, SearchQueriesResponse
from ..retrieval_strategy import RetrievalStrategy, RetrievalConfig
import traceback
from pydantic import BaseModel
import json
from utils.logger import get_logger
logger = get_logger(__name__)

class MindMapGeneratorMixin:
    """思维导图生成功能混入类"""

    async def _generate_mindmap_queries(self, document: Document) -> SearchQueriesResponse:
        """基于文档摘要生成覆盖文档宏观结构的检索查询"""
        try:
            base_content = document.summary if document.summary else document.original_text[:2000]
            
            # 获取 SearchQueriesResponse 的 Schema
            schema_json = json.dumps(SearchQueriesResponse.model_json_schema(), indent=2, ensure_ascii=False)
            
            prompt = f"""你是一个专业的知识架构师。请分析以下文档内容，设计一组检索查询词，用于获取构建“思维导图”所需的详细信息。

文档：{document.filename}
摘要/内容：
{base_content}

要求：
1. `semantic_queries`: 3-5个自然语言问题，侧重于挖掘文档的宏观结构和核心逻辑（例如："本文的主要章节结构是怎样的？", "核心论点是如何逐层展开的？"）。
2. `keywords`: 5-8个具体的专有名词、核心概念或关键实体，用于精准匹配（例如："Hierarchical Structure", "Key Methodology"）。

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：

{schema_json}
"""
            messages = [{"role": "user", "content": prompt}]
            response = await self.lc_client.ainvoke(messages)
            
            from ..utils import parse_json_safely
            data = parse_json_safely(response.content)
            
            # 使用 Pydantic 模型校验
            validated_data = SearchQueriesResponse.model_validate(data)
           
            logger.info(f"🔍 思维导图检索方案:")
            logger.info(f"   - 语义查询: {validated_data.semantic_queries}")
            logger.info(f"   - 关键词: {validated_data.keywords}")
            
            return validated_data
            
        except Exception as e:
            logger.warning(f"⚠️ 生成检索查询失败，使用默认策略: {e}")
            return SearchQueriesResponse(
                queries=[],
                semantic_queries=[f"Structure of {document.filename}"],
                keywords=[document.filename]
            )

    async def generate_mindmap(
        self,
        document: Document,
        user_id: Optional[int] = None
    ) -> Dict:
        """从文档生成思维导图（使用多路覆盖检索策略）"""
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"🗺️ 开始生成思维导图 (Multi-Query RAG Mode)...")
            logger.info(f"{'='*60}\n")
            
            from document_service.vector_store import vector_store_manager
            
            # 1. 生成覆盖性查询词 (Hybrid)
            search_plan = await self._generate_mindmap_queries(document)
            
            # 2. 执行高级混合检索 (Semantic + Keyword)
            relevant_chunks = await vector_store_manager.advanced_hybrid_search(
                collection_id=document.vector_store_id,
                semantic_queries=search_plan.semantic_queries,
                keywords=search_plan.keywords,
                top_k=8 # 思维导图需要宏观+微观
            )
            
            # 3. 构建上下文
            # 思维导图需要宏观视角，但也需要细节，所以上下文可以稍微长一点
            MAX_CONTEXT_CHARS = 15000 
            
            # 🔧 优化：如果文档本身很短，直接用全文
            doc_length = len(document.original_text)
            if doc_length < 5000:
                 logger.info(f"📄 文档较短 (<5000字)，直接使用全文作为上下文")
                 context_text = document.original_text
            else:
                context_text = ""
                current_chars = 0
                for i, chunk in enumerate(relevant_chunks):
                    chunk_text = chunk['text']
                    if len(chunk_text) > 1200:
                        chunk_text = chunk_text[:1200] + "..."
                    
                    entry = f"[片段 {i+1}]\n{chunk_text}\n\n"
                    if current_chars + len(entry) > MAX_CONTEXT_CHARS:
                        break
                    context_text += entry
                    current_chars += len(entry)
                logger.info(f"📄 使用多路检索内容（{len(relevant_chunks)}个片段，{current_chars}字符）")

            # 摘要部分
            summary_section = ""
            if document.summary and len(document.summary) > 50:
                summary_section = f"文档摘要:\n{document.summary}\n"
            
            # 获取 JSON Schema
            schema_json = json.dumps(MindMapResponse.model_json_schema(), indent=2, ensure_ascii=False)

            prompt = f"""你是一位专业的知识架构师。请基于以下文档内容，生成一个**深度分析的思维导图**，不是简单的关键词罗列。
    
文档标题: {document.filename}
    
{summary_section}
参考内容：
{context_text}
    
🎯 **核心要求**：
    
1. **理解文档逻辑结构**，而非关键词提取：
   - 识别文档的核心论点/方法/理论
   - 分析概念之间的因果关系、递进关系、对比关系
   - 提炼方法论的步骤和流程
   
2. **层次化组织**（严格遵循文档逻辑）：
   - 第一层：核心主题/主要观点（2-4个）
   - 第二层：支撑论据/子方法/关键概念（3-6个/分支）
   - 第三层：具体细节/实例/公式（2-4个/分支）
   
3. **节点命名规范**：
   - ❌ 错误示例："研究背景"、"核心内容"、"相关工作"（太泛）
   - ✅ 正确示例："POMDP的双重特性"、"贝叶斯风险vs主观效用"、"ICL作为外推机制"
   - 使用**完整的概念描述**，而非单个关键词
   
4. **结构化思考**（推荐结构）：
   ```
   根节点：文档核心主题（一句话概括）
   ├─ 问题定义（这篇文档要解决什么问题？）
   │  ├─ 传统方法的局限
   │  ├─ 本文的核心挑战
   │  └─ 研究动机
   ├─ 核心方法/理论（如何解决问题？）
   │  ├─ 关键创新点1
   │  ├─ 关键创新点2
   │  └─ 技术实现细节
   ├─ 实验验证（效果如何？）
   │  ├─ 实验设置
   │  ├─ 主要结果
   │  └─ 对比分析
   └─ 结论与启示（贡献是什么？）
      ├─ 理论贡献
      ├─ 实践价值
      └─ 未来方向
   ```

5. **输出格式**：
   - 根节点(root)：id、title、children
   - 子节点：id、title、children
   - 总节点数：20-35个（根据文档复杂度）
   - 深度：3-4层（避免过深或过浅）

⚠️ **避免的错误**：
- 不要只列出章节标题（要分析内在逻辑）
- 不要提取关键词（要用完整概念）
- 不要平铺直叙（要有层次递进）
- 不要遗漏核心创新点和方法论细节

请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串，不要包含 markdown 代码块：

{schema_json}
"""
            
            messages = [
                {"role": "system", "content": """你是一位资深的学术知识架构师和思维导图专家。请生成一个有深度、有洞察的思维导图。"""},
                {"role": "user", "content": prompt}
            ]
            
            logger.info(f"🤖 调用AI生成思维导图...")
            
            # 调用 LLM
            ai_response = await self.lc_client.ainvoke(messages)
            response_content = ai_response.content
            
            from ..utils import parse_json_safely
            data = parse_json_safely(response_content)
            
            # 验证和转换
            mindmap_response = MindMapResponse.model_validate(data)
            
            try:
                mindmap_data = mindmap_response.model_dump()
                logger.info(f"✅ 成功转换为字典格式")
            except Exception as dump_error:
                logger.error(f"❌ model_dump 失败: {dump_error}")
                raise
            
            def count_nodes(node):
                count = 1
                if 'children' in node and node['children']:
                    for child in node['children']:
                        count += count_nodes(child)
                return count
            
            node_count = count_nodes(mindmap_data['root'])
            logger.info(f"✅ 思维导图生成成功！")
            logger.info(f"   节点总数: {node_count}")
            logger.info(f"{'='*60}\n")
            
            # 注意：不再保存到旧的 DocumentMindmap 表，改为在 main.py 中统一保存到 Note 表
            
            return mindmap_data
            
        except Exception as e:
            context = self._build_context(document, {"user_id": user_id})
            self._log_error("思维导图", f"生成失败: {e}", error=e, context=context)
            error_msg = str(e)
            if "Connection error" in error_msg or "ConnectError" in error_msg:
                raise Exception("网络连接失败，请检查网络连接或稍后重试") from e
            else:
                raise

    async def expand_mindmap_node(
        self,
        document: Document,
        node_id: str,
        current_mindmap: Dict,
        user_id: Optional[int] = None
    ) -> List[Dict]:
        """
        扩展思维导图的特定节点，生成更细致的子节点
        
        Args:
            document: 文档对象
            node_id: 要扩展的节点ID
            current_mindmap: 当前完整的思维导图数据
            user_id: 用户ID（用于保存到数据库）
            
        Returns:
            新生成的子节点列表
        """
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"🔍 扩展思维导图节点: {node_id}")
            logger.info(f"{'='*60}\n")
            
            # 1. 找到目标节点
            def find_node(node, target_id):
                """递归查找节点"""
                if node['id'] == target_id:
                    return node
                if 'children' in node and node['children']:
                    for child in node['children']:
                        found = find_node(child, target_id)
                        if found:
                            return found
                return None
            
            target_node = find_node(current_mindmap['root'], node_id)
            
            if not target_node:
                raise Exception(f"未找到节点 {node_id}")
            
            logger.info(f"✅ 找到节点: {target_node['title']}")
            
            # 2. 获取节点的上下文路径（从根到当前节点）
            def get_node_path(node, target_id, path=[]):
                """获取从根节点到目标节点的路径"""
                current_path = path + [node['title']]
                if node['id'] == target_id:
                    return current_path
                if 'children' in node and node['children']:
                    for child in node['children']:
                        result = get_node_path(child, target_id, current_path)
                        if result:
                            return result
                return None
            
            node_path = get_node_path(current_mindmap['root'], node_id)
            path_str = " > ".join(node_path)
            logger.info(f"   节点路径: {path_str}")
            
            # 3. 获取已有子节点信息
            existing_children = target_node.get('children', [])
            existing_children_str = "\n".join([f"- {child['title']}" for child in existing_children])
            
            if existing_children:
                logger.info(f"   已有子节点: {len(existing_children)} 个")
            else:
                logger.info(f"   当前无子节点，将创建初始子节点")
            
            # 4. 使用局部检索策略
            from document_service.vector_store import vector_store_manager
            
            context_text, chunk_count = await RetrievalStrategy.retrieve_for_node_expansion(
                document=document,
                vector_store_manager=vector_store_manager,
                node_path=path_str,
                target_node=target_node,
                **RetrievalConfig.NODE_EXPANSION
            )
            
            # 5. 构建扩展提示
            existing_children_section = (
                f"已有子节点:\n{existing_children_str}"
                if existing_children else
                "当前无子节点"
            )
            complement_rule = (
                "- 与已有子节点互补，避免重复"
                if existing_children else
                "- 从最重要的方面开始细化"
            )
            
            # 定义响应模型
            class ExpandedNodesResponse(BaseModel):
                """扩展节点响应"""
                nodes: List[MindMapNode]

            schema_json = json.dumps(ExpandedNodesResponse.model_json_schema(), indent=2, ensure_ascii=False)
            
            prompt = f"""为思维导图节点生成 3-5 个子节点。

文档: {document.filename}

文档摘要:
{document.summary if document.summary else '无'}

当前节点: {target_node['title']}
节点路径: {path_str}

检索到的相关内容:
{context_text}

{existing_children_section}

要求:
1. 生成 3-5 个新的子节点，每个节点包含：
   - id: 唯一标识（如 {node_id}_expand_1）
   - title: 简短标题
   - children: 空列表

2. 新节点应该：
   {complement_rule}
   - 基于文档实际内容
   - 符合层级关系
   - 逻辑递进

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串，不要包含 markdown 代码块：

{schema_json}
"""
            
            messages = [
                {"role": "system", "content": "你是一个专业的思维导图扩展专家，擅长将知识点进行细化和层级化。"},
                {"role": "user", "content": prompt}
            ]
            
            try:
                logger.info(f"🤖 调用AI扩展思维导图节点...")
                ai_response = await self.lc_client.ainvoke(messages)
                response_content = ai_response.content
                
                from ..utils import parse_json_safely
                data = parse_json_safely(response_content)
                
                expansion_response = ExpandedNodesResponse.model_validate(data)

            except Exception as ai_error:
                self._log_error(
                    "思维导图扩展",
                    f"AI调用或解析失败: {ai_error}",
                    error=ai_error,
                    context=self._build_context(document, {
                        "node_id": node_id,
                        "node_path": path_str,
                        "user_id": user_id
                    })
                )
                raise
            
            if not expansion_response.nodes:
                 raise Exception("AI返回的节点列表为空")
            
            new_nodes = [node.model_dump() for node in expansion_response.nodes]
            
            logger.info(f"✅ 成功生成 {len(new_nodes)} 个新子节点")
            for i, node in enumerate(new_nodes, 1):
                logger.info(f"   {i}. {node['title']}")
            logger.info(f"{'='*60}\n")
            
            # 7. 可选：保存扩展后的完整思维导图到数据库
            if self.db and user_id:
                # 将新节点添加到目标节点的children中
                target_node['children'] = existing_children + new_nodes
                
                # 重新计算节点总数
                def count_nodes(node):
                    count = 1
                    if 'children' in node and node['children']:
                        for child in node['children']:
                            count += count_nodes(child)
                    return count
                
                new_node_count = count_nodes(current_mindmap['root'])
                
                # 更新数据库中的思维导图
                
                # 查找当前版本
                result = await self.db.execute(
                    select(DocumentMindmap).filter(
                        DocumentMindmap.document_id == document.id,
                        DocumentMindmap.user_id == user_id,
                        DocumentMindmap.is_current == True
                    )
                )
                current_mindmap_record = result.scalar_one_or_none()
                
                if current_mindmap_record:
                    # 更新当前版本（不创建新版本，因为是增量修改）
                    current_mindmap_record.mindmap_data = current_mindmap
                    current_mindmap_record.node_count = new_node_count
                    await self.db.commit()
                    logger.info(f"✅ 已更新数据库中的思维导图（总节点数: {new_node_count}）")
            
            return new_nodes
            
        except Exception as e:
            context = self._build_context(document, {
                "node_id": node_id,
                "user_id": user_id
            })
            self._log_error("思维导图扩展", f"执行失败: {e}", error=e, context=context)
            error_msg = str(e)
            if "Connection error" in error_msg or "ConnectError" in error_msg:
                raise Exception("网络连接失败，请检查网络连接或稍后重试") from e
            else:
                raise
