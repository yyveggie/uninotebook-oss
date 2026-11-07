"""
内容生成功能
包含学习路径、闪卡、测验、思维导图、重点提炼等生成功能
"""
from .generators.base import BaseGenerator

from .generators.flashcards import FlashcardsGeneratorMixin
from .generators.quiz import QuizGeneratorMixin
from .generators.mindmap import MindMapGeneratorMixin
from .generators.highlights import HighlightsGeneratorMixin
from .generators.report import ReportGeneratorMixin

class ContentGenerator(
    BaseGenerator,
    FlashcardsGeneratorMixin,
    QuizGeneratorMixin,
    MindMapGeneratorMixin,
    HighlightsGeneratorMixin,
    ReportGeneratorMixin
):
    """内容生成器 - 负责生成学习路径、闪卡、测验、思维导图、重点提炼等"""
    pass

