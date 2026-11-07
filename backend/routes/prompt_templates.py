"""
提示词模板路由

包含: 提示词模板CRUD、默认模板初始化
"""
from fastapi import APIRouter, HTTPException, Depends, Body
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db_models
from database import get_db
from utils.logger import get_logger
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode
logger = get_logger(__name__)

router = APIRouter()


# 预设模板定义
DEFAULT_PROMPT_TEMPLATES = [
    {
        "title": "观点逻辑",
        "icon": "🧠",
        "prompt": """请系统梳理这篇文档的关键观点，并明确每个观点背后的论证逻辑链条。

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

请保持结构化输出，确保读者能一目了然地看到观点之间的逻辑关系。""",
        "sort_order": 0
    },
    {
        "title": "中心思想",
        "icon": "🎯",
        "prompt": """请提取并阐述这篇文档的中心思想——即作者最想传达的核心观点、主旨或立场。

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

📌 重点：聚焦"作者想告诉我们什么"，而不是"文档包含什么内容"。要提炼出价值判断和立场。""",
        "sort_order": 1
    },
    {
        "title": "优点与缺点",
        "icon": "⚖️",
        "prompt": """请以第三方独立评审专家的视角，批判性地分析这篇文档的内容。

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
- 每个点都要具体、有建设性""",
        "sort_order": 2
    },
    {
        "title": "通俗举例子",
        "icon": "💡",
        "prompt": """请用一个通俗易懂的例子，给小白解释这篇文档的核心方法/流程。

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
- 例子要有趣、生动、容易记住""",
        "sort_order": 3
    }
]


async def ensure_user_has_default_templates(db: AsyncSession, user_id: int):
    """确保用户拥有默认模板（首次访问时自动创建）"""
    result = await db.execute(
        select(db_models.UserPromptTemplate).filter(
            db_models.UserPromptTemplate.user_id == user_id
        ).limit(1)
    )
    existing = result.scalar_one_or_none()

    if existing:
        return  # 已有模板，不需要初始化

    for template_data in DEFAULT_PROMPT_TEMPLATES:
        new_template = db_models.UserPromptTemplate(
            user_id=user_id,
            title=template_data["title"],
            icon=template_data["icon"],
            prompt=template_data["prompt"],
            sort_order=template_data["sort_order"],
            is_default=True
        )
        db.add(new_template)

    await db.commit()
    logger.info(f"✅ 为用户 {user_id} 初始化了 {len(DEFAULT_PROMPT_TEMPLATES)} 个默认提示词模板")


@router.get("/api/prompt-templates", tags=["提示词模板"])
async def get_prompt_templates(
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取用户的提示词模板列表（首次访问自动创建默认模板）"""
    try:
        # 确保用户有默认模板
        await ensure_user_has_default_templates(db, current_user.id)
        
        # 查询用户所有模板
        result = await db.execute(
            select(db_models.UserPromptTemplate).filter(
                db_models.UserPromptTemplate.user_id == current_user.id
            ).order_by(db_models.UserPromptTemplate.sort_order)
        )
        templates = result.scalars().all()
        
        return {
            "success": True,
            "data": [
                {
                    "id": t.id,
                    "title": t.title,
                    "icon": t.icon,
                    "prompt": t.prompt,
                    "sort_order": t.sort_order,
                    "is_default": t.is_default,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None
                }
                for t in templates
            ],
            "max_count": 6
        }
    except Exception as e:
        logger.error(f"❌ 获取提示词模板失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.PROMPT_TEMPLATES_500_EXCEPTION, message="获取失败", details=str(e))


@router.post("/api/prompt-templates", tags=["提示词模板"])
async def create_prompt_template(
    title: str = Body(...),
    icon: str = Body("📝"),
    prompt: str = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """创建新的提示词模板（最多6个）"""
    try:
        # 检查数量限制
        result = await db.execute(
            select(db_models.UserPromptTemplate).filter(
                db_models.UserPromptTemplate.user_id == current_user.id
            )
        )
        existing_count = len(result.scalars().all())
        
        if existing_count >= 6:
            raise_http_error(status_code=400, code=ErrorCode.PROMPT_TEMPLATES_400_MESSAGE, message="最多只能创建6个提示词模板")
        
        # 获取最大排序值
        result = await db.execute(
            select(db_models.UserPromptTemplate.sort_order).filter(
                db_models.UserPromptTemplate.user_id == current_user.id
            ).order_by(db_models.UserPromptTemplate.sort_order.desc()).limit(1)
        )
        max_order = result.scalar_one_or_none() or 0
        
        # 创建新模板
        new_template = db_models.UserPromptTemplate(
            user_id=current_user.id,
            title=title,
            icon=icon,
            prompt=prompt,
            sort_order=max_order + 1,
            is_default=False
        )
        db.add(new_template)
        await db.commit()
        await db.refresh(new_template)
        
        return {
            "success": True,
            "data": {
                "id": new_template.id,
                "title": new_template.title,
                "icon": new_template.icon,
                "prompt": new_template.prompt,
                "sort_order": new_template.sort_order,
                "is_default": new_template.is_default,
                "created_at": new_template.created_at.isoformat() if new_template.created_at else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 创建提示词模板失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.PROMPT_TEMPLATES_500_EXCEPTION, message="创建失败", details=str(e))


@router.put("/api/prompt-templates/{template_id}", tags=["提示词模板"])
async def update_prompt_template(
    template_id: int,
    title: str = Body(None),
    icon: str = Body(None),
    prompt: str = Body(None),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """更新提示词模板"""
    try:
        result = await db.execute(
            select(db_models.UserPromptTemplate).filter(
                db_models.UserPromptTemplate.id == template_id,
                db_models.UserPromptTemplate.user_id == current_user.id
            )
        )
        template = result.scalar_one_or_none()
        
        if not template:
            raise_http_error(status_code=404, code=ErrorCode.PROMPT_TEMPLATES_404_MESSAGE, message="模板不存在")
        
        if title is not None:
            template.title = title
        if icon is not None:
            template.icon = icon
        if prompt is not None:
            template.prompt = prompt
        
        await db.commit()
        await db.refresh(template)
        
        return {
            "success": True,
            "data": {
                "id": template.id,
                "title": template.title,
                "icon": template.icon,
                "prompt": template.prompt,
                "sort_order": template.sort_order,
                "is_default": template.is_default,
                "updated_at": template.updated_at.isoformat() if template.updated_at else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 更新提示词模板失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.PROMPT_TEMPLATES_500_EXCEPTION, message="更新失败", details=str(e))


@router.delete("/api/prompt-templates/{template_id}", tags=["提示词模板"])
async def delete_prompt_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """删除提示词模板"""
    try:
        from sqlalchemy import delete
        
        result = await db.execute(
            delete(db_models.UserPromptTemplate).filter(
                db_models.UserPromptTemplate.id == template_id,
                db_models.UserPromptTemplate.user_id == current_user.id
            )
        )
        
        if result.rowcount == 0:
            raise_http_error(status_code=404, code=ErrorCode.PROMPT_TEMPLATES_404_MESSAGE, message="模板不存在或无权删除")
        
        await db.commit()
        
        return {"success": True, "message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 删除提示词模板失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.PROMPT_TEMPLATES_500_EXCEPTION, message="删除失败", details=str(e))
