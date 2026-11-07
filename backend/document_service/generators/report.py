# pyright: reportAttributeAccessIssue=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false

import re
from typing import Optional, Dict, Any, Callable
from db_models import Document
from config import settings


REPORT_TYPE_CONFIG = {
    "deep_report": {
        "label": "深度报告",
        "system_prompt": "你是严谨的中文研究分析师，强调论证完整性、证据链和边界条件。",
        "style_hint": "写作风格偏研究分析，强调问题-方法-证据-结论。",
        "sections": [
            {"heading": "执行摘要", "objective": "概括核心问题、方法、关键结论。"},
            {"heading": "背景与问题定义", "objective": "澄清背景、目标、约束与边界。"},
            {"heading": "方法与技术路线", "objective": "说明方法设计、流程与关键机制。"},
            {"heading": "结果与证据", "objective": "总结关键结果、证据与对比观察。"},
            {"heading": "局限性与风险", "objective": "识别假设前提、适用边界与风险。"},
            {"heading": "建议与下一步", "objective": "提出可执行建议与后续工作方向。"},
        ],
    },
    "briefing": {
        "label": "简报文档",
        "system_prompt": "你是高层决策简报撰写者，强调结论先行、信息压缩和行动建议。",
        "style_hint": "写作风格偏结论先行，强调要点、优先级与决策建议。",
        "sections": [
            {"heading": "核心结论", "objective": "先给出关键结论与判断。"},
            {"heading": "关键事实与依据", "objective": "列出支持结论的核心证据。"},
            {"heading": "影响评估", "objective": "说明对业务/学习/项目的影响。"},
            {"heading": "风险与注意事项", "objective": "提示主要风险与约束条件。"},
            {"heading": "行动建议", "objective": "给出优先级清晰的执行建议。"},
        ],
    },
    "study_guide": {
        "label": "学习指南",
        "system_prompt": "你是学习教练，强调学习路径、步骤拆解、练习与复盘。",
        "style_hint": "写作风格偏教学导向，强调循序渐进、可执行练习与复盘。",
        "sections": [
            {"heading": "学习目标与预期产出", "objective": "定义学习目标、能力指标和产出。"},
            {"heading": "先修知识与准备", "objective": "列出前置知识、资料和环境准备。"},
            {"heading": "分阶段学习路径", "objective": "按阶段拆解学习步骤与重点。"},
            {"heading": "练习与自测", "objective": "给出练习方法、检查点与常见错误。"},
            {"heading": "复盘与进阶", "objective": "给出复盘方式与下一步进阶建议。"},
        ],
    },
    "blog_post": {
        "label": "博文",
        "system_prompt": "你是技术博主，强调可读性、叙事结构和对读者友好的解释。",
        "style_hint": "写作风格偏叙事与可读性，兼顾严谨与阅读体验。",
        "sections": [
            {"heading": "开场：为什么这件事值得关注", "objective": "用问题或场景建立读者兴趣。"},
            {"heading": "核心观点", "objective": "提炼文章想传达的核心观点。"},
            {"heading": "关键机制与案例", "objective": "解释关键机制并结合示例说明。"},
            {"heading": "常见误区与边界", "objective": "指出误区、限制与适用范围。"},
            {"heading": "总结与行动建议", "objective": "收束观点并给出可执行建议。"},
        ],
    },
}


class ReportGeneratorMixin:
    """长报告生成功能混入类"""

    @staticmethod
    def _normalize_heading_text(text: str) -> str:
        normalized = (text or "").strip().lstrip("\ufeff")
        normalized = re.sub(r"^#{1,6}\s*", "", normalized)
        normalized = re.sub(r"^\*\*(.+?)\*\*$", r"\1", normalized)
        normalized = re.sub(r"^__(.+?)__$", r"\1", normalized)
        normalized = re.sub(r"[:：]\s*$", "", normalized)
        return normalized.strip()

    @classmethod
    def _dedupe_section_heading_line(cls, heading: str, section_body: str) -> str:
        body = (section_body or "").strip()
        if not body:
            return body

        lines = body.splitlines()
        first_non_empty_index = next((i for i, line in enumerate(lines) if line.strip()), None)
        if first_non_empty_index is None:
            return body

        normalized_heading = cls._normalize_heading_text(heading)
        normalized_first_line = cls._normalize_heading_text(lines[first_non_empty_index])
        if not normalized_heading or normalized_first_line != normalized_heading:
            return body

        remaining_lines = lines[first_non_empty_index + 1:]
        while remaining_lines and not remaining_lines[0].strip():
            remaining_lines.pop(0)

        return "\n".join(remaining_lines).strip()

    @staticmethod
    def _count_report_words(text: str) -> int:
        if not text:
            return 0
        stripped = text.strip()
        if not stripped:
            return 0
        return len(stripped)

    @staticmethod
    def _fallback_report_outline(document_title: str, report_type: str = "deep_report") -> Dict[str, Any]:
        config = REPORT_TYPE_CONFIG.get(report_type, REPORT_TYPE_CONFIG["deep_report"])
        return {
            "title": f"{document_title} {config['label']}",
            "sections": config["sections"],
        }

    @staticmethod
    def _resolve_report_type(report_type: Optional[str]) -> str:
        safe_type = (report_type or "deep_report").strip()
        if safe_type not in REPORT_TYPE_CONFIG:
            return "deep_report"
        return safe_type

    @staticmethod
    def _normalize_context_ratios(head_ratio: float, mid_ratio: float, tail_ratio: float) -> Dict[str, float]:
        head = max(float(head_ratio or 0.0), 0.0)
        mid = max(float(mid_ratio or 0.0), 0.0)
        tail = max(float(tail_ratio or 0.0), 0.0)
        total = head + mid + tail
        if total <= 0:
            return {"head": 0.34, "mid": 0.33, "tail": 0.33}
        return {
            "head": head / total,
            "mid": mid / total,
            "tail": tail / total,
        }

    @classmethod
    def _select_context_segments(
        cls,
        full_text: str,
        max_chars: int,
        head_ratio: float,
        mid_ratio: float,
        tail_ratio: float,
    ) -> Dict[str, Any]:
        content = (full_text or "").strip()
        total_chars = len(content)
        if total_chars <= max_chars:
            return {
                "selected_text": content,
                "strategy": "full",
                "total_chars": total_chars,
                "selected_chars": total_chars,
                "ratio": cls._normalize_context_ratios(head_ratio, mid_ratio, tail_ratio),
            }

        marker_overhead = 120
        available_budget = max(max_chars - marker_overhead, 1200)
        ratios = cls._normalize_context_ratios(head_ratio, mid_ratio, tail_ratio)

        head_budget = max(int(available_budget * ratios["head"]), 300)
        mid_budget = max(int(available_budget * ratios["mid"]), 300)
        tail_budget = max(int(available_budget * ratios["tail"]), 300)

        total_budget = head_budget + mid_budget + tail_budget
        if total_budget > available_budget:
            scale = available_budget / total_budget
            head_budget = max(int(head_budget * scale), 200)
            mid_budget = max(int(mid_budget * scale), 200)
            tail_budget = max(int(tail_budget * scale), 200)

        head_range = (0, min(head_budget, total_chars))
        mid_start = max((total_chars // 2) - (mid_budget // 2), 0)
        mid_range = (mid_start, min(mid_start + mid_budget, total_chars))
        tail_start = max(total_chars - tail_budget, 0)
        tail_range = (tail_start, total_chars)

        ranges = sorted([head_range, mid_range, tail_range], key=lambda x: x[0])

        merged = []
        for start, end in ranges:
            if not merged:
                merged.append([start, end])
                continue
            last = merged[-1]
            if start <= last[1]:
                last[1] = max(last[1], end)
            else:
                merged.append([start, end])

        parts = []
        labels = ["前段", "中段", "后段"]
        for idx, (start, end) in enumerate(merged):
            label = labels[idx] if idx < len(labels) else f"片段{idx + 1}"
            parts.append(f"[{label} {start}-{end}]\n{content[start:end]}")

        selected_text = "\n\n".join(parts)
        if len(selected_text) > max_chars:
            selected_text = selected_text[:max_chars]

        return {
            "selected_text": selected_text,
            "strategy": "head_mid_tail",
            "total_chars": total_chars,
            "selected_chars": len(selected_text),
            "ratio": ratios,
        }

    async def _generate_report_outline(
        self,
        document_title: str,
        summary_text: str,
        report_type: str,
        custom_style: str,
        focus_points: str,
    ) -> Dict[str, Any]:
        config = REPORT_TYPE_CONFIG.get(report_type, REPORT_TYPE_CONFIG["deep_report"])
        template_sections = "\n".join([
            f"- {s['heading']}：{s['objective']}" for s in config["sections"]
        ])
        outline_prompt = f"""你是一位资深研究分析师。请根据文档内容输出“长报告大纲”。

文档标题：{document_title}
文档摘要：{summary_text[:3000] if summary_text else '（无摘要）'}
报告类型：{config['label']}
风格要求：{custom_style or config['style_hint']}
重点关注：{focus_points or '无'}

参考章节模板（优先遵循，可微调）：
{template_sections}

请严格返回 JSON（不要 markdown 代码块）：
{{
  "title": "报告标题",
  "sections": [
    {{"heading": "一级章节标题", "objective": "本章写作目标（1句话）"}}
  ]
}}

要求：
1) sections 数量与模板接近（允许 ±1）；
2) 章节目标必须贴合报告类型；
3) 标题清晰专业，避免空泛；
4) 严禁虚构文档中不存在的事实、数据、机构、实验或结论。"""

        try:
            ai_response = await self.lc_client.ainvoke([
                {"role": "system", "content": config["system_prompt"]},
                {"role": "user", "content": outline_prompt},
            ])
            from ..utils import parse_json_safely
            outline = parse_json_safely(ai_response.content)
            if not isinstance(outline, dict) or not isinstance(outline.get("sections"), list) or not outline.get("sections"):
                return self._fallback_report_outline(document_title, report_type=report_type)
            return outline
        except Exception as e:
            self._logger.warning("【长报告】大纲生成失败，改用模板大纲: %s: %s", type(e).__name__, e)
            return self._fallback_report_outline(document_title, report_type=report_type)

    async def generate_report(
        self,
        document: Document,
        user_id: Optional[int] = None,
        report_type: Optional[str] = None,
        custom_style: Optional[str] = None,
        focus_points: Optional[str] = None,
        requirements: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """基于文档生成长报告"""
        try:
            resolved_type = self._resolve_report_type(report_type)
            config = REPORT_TYPE_CONFIG.get(resolved_type, REPORT_TYPE_CONFIG["deep_report"])
            document_title = str(document.display_name or document.filename or f"文档 {getattr(document, 'id', '')}")
            summary_text = (document.summary or "").strip()
            full_text = (document.original_text or "").strip()
            context_meta = self._select_context_segments(
                full_text,
                max_chars=int(getattr(settings, "report_context_max_chars", 20000)),
                head_ratio=float(getattr(settings, "report_context_ratio_head", 0.34)),
                mid_ratio=float(getattr(settings, "report_context_ratio_mid", 0.33)),
                tail_ratio=float(getattr(settings, "report_context_ratio_tail", 0.33)),
            )
            selected_text = context_meta.get("selected_text", "")
            context_text = (summary_text + "\n\n" + selected_text).strip()
            if not context_text:
                raise ValueError("文档内容为空，无法生成报告")

            safe_custom_style = (custom_style or "").strip()
            safe_focus_points = (focus_points or "").strip()
            safe_requirements = (requirements or "").strip()

            outline = await self._generate_report_outline(
                document_title=document_title,
                summary_text=summary_text,
                report_type=resolved_type,
                custom_style=safe_custom_style,
                focus_points=safe_focus_points or safe_requirements,
            )

            report_title = outline.get("title") or f"{document_title} {config['label']}"
            sections = [s for s in outline.get("sections", []) if isinstance(s, dict) and s.get("heading")]
            if not sections:
                sections = self._fallback_report_outline(document_title, report_type=resolved_type)["sections"]

            total_sections = len(sections)
            section_markdowns = []

            user_focus_text = safe_focus_points or safe_requirements or "无"
            style_text = safe_custom_style or config["style_hint"]

            for index, section in enumerate(sections, start=1):
                if progress_callback:
                    progress_callback(index, total_sections)

                heading = str(section.get("heading", f"章节 {index}")).strip()
                objective = str(section.get("objective", "")).strip()

                section_prompt = f"""请撰写报告章节。

报告标题：{report_title}
报告类型：{config['label']}
当前章节：{heading}
章节目标：{objective if objective else '请围绕章节标题展开'}
风格要求：{style_text}
重点关注：{user_focus_text}

参考文档内容：
{context_text}

写作要求：
1) 仅输出本章节正文（Markdown）；
2) 语言为中文，逻辑清晰、层次分明；
3) 严格基于参考文档事实，不得使用外部常识补写，不得编造任何事实、数据、实验结果、时间、机构、引用与结论；
4) 适当使用二级/三级小节、列表、表格；
5) 篇幅尽量充实（约 500-900 字）；
6) 若文档未提供支撑信息，必须明确写“文档未提及/证据不足/无法确认”，禁止猜测性表述；
7) LaTeX 公式：行内用 $...$，独立用 $$...$$；禁止伪公式文本（如 x^2 直接裸写为公式结论）。"""

                section_response = await self.lc_client.ainvoke([
                    {"role": "system", "content": config["system_prompt"]},
                    {"role": "user", "content": section_prompt},
                ])
                section_body = (section_response.content or "").strip()
                section_body = self._dedupe_section_heading_line(heading, section_body)
                if not section_body:
                    section_body = "（该章节生成失败，建议重试）"

                section_markdowns.append(f"## {heading}\n\n{section_body}")

            final_markdown = f"# {report_title}\n\n" + "\n\n".join(section_markdowns)
            word_count = self._count_report_words(final_markdown)

            return {
                "title": report_title,
                "report_markdown": final_markdown,
                "report_data": {
                    "report_type": resolved_type,
                    "report_type_label": config["label"],
                    "outline": {
                        "title": report_title,
                        "sections": sections,
                    },
                    "custom_style": safe_custom_style or None,
                    "focus_points": safe_focus_points or None,
                    "requirements": requirements,
                    "context_strategy": context_meta.get("strategy"),
                    "context_ratio": context_meta.get("ratio"),
                    "source_char_count": context_meta.get("total_chars"),
                    "selected_char_count": context_meta.get("selected_chars"),
                },
                "word_count": word_count,
            }

        except Exception as e:
            context = self._build_context(document, {
                "user_id": user_id,
                "report_type": report_type,
                "custom_style": custom_style,
                "focus_points": focus_points,
                "requirements": requirements,
            })
            self._log_error("长报告", f"生成失败: {e}", error=e, context=context)
            raise
