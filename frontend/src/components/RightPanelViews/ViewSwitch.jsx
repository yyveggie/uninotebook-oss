import React from 'react'
import { VIEW_FADE } from './viewFade'
import { motion as Motion } from 'framer-motion'
import StudioView from './StudioView'
import FlowchartView from './FlowchartView'
import FlashcardsView from './FlashcardsView'
import QuizView from './QuizView'
import MindmapView from './MindmapView'
import HighlightsView from './HighlightsView'
import NoteView from './NoteView'
import ReportView from './ReportView'

const ViewSwitch = ({
  view,
  studioProps,
  viewState,
  actions,
}) => {
  const {
    flowchart,
    tempFlowchart,
    showHistory,
    loadingHistory,
    flowchartHistory,
    selectedFlowchartId,
    expandedNodes,
    flashcards,
    tempFlashcards,
    flashcardsHistory,
    selectedFlashcardsId,
    quiz,
    tempQuiz,
    quizHistory,
    selectedQuizId,
    isGeneratingQuiz,
    mindmap,
    tempMindmap,
    isGeneratingMindmap,
    expandingNodeId,
    highlights,
    tempHighlights,
    isGeneratingHighlights,
    report,
    tempReport,
    isGeneratingReport,
    tempNote,
  } = viewState

  const {
    onViewChange,
    setShowHistory,
    loadFlowchartVersion,
    toggleNodeExpand,
    onExplainPathNode,
    loadFlashcardsVersion,
    loadQuizVersion,
    onSendQuestionToChat,
    setTempMindmap,
    setSelectedMindmapId,
    handleMindmapNodeClick,
    setTempHighlights,
    setSelectedHighlightsId,
    onExplainHighlight,
    setTempReport,
    setEditingNoteId,
    setNoteTitle,
    setNoteContent,
    setShowNoteDialog,
  } = actions

  if (view === 'studio') {
    return (
      <Motion.div
        key="studio"
        {...VIEW_FADE}
        style={{ height: '100%', width: '100%' }}
      >
        <StudioView {...studioProps} />
      </Motion.div>
    )
  }

  if (view === 'flowchart') {
    return (
      <Motion.div
        key="flowchart"
        {...VIEW_FADE}
        style={{ height: '100%', width: '100%' }}
      >
        <FlowchartView
          flowchart={flowchart}
          tempFlowchart={tempFlowchart}
          onViewChange={onViewChange}
          showHistory={showHistory}
          setShowHistory={setShowHistory}
          loadingHistory={loadingHistory}
          flowchartHistory={flowchartHistory}
          selectedFlowchartId={selectedFlowchartId}
          loadFlowchartVersion={loadFlowchartVersion}
          expandedNodes={expandedNodes}
          toggleNodeExpand={toggleNodeExpand}
          onExplainPathNode={onExplainPathNode}
        />
      </Motion.div>
    )
  }

  if (view === 'flashcards') {
    return (
      <div
        key="flashcards"
        style={{ height: '100%', width: '100%', position: 'relative' }}
      >
        <FlashcardsView
          flashcards={flashcards}
          tempFlashcards={tempFlashcards}
          onViewChange={onViewChange}
          showHistory={showHistory}
          setShowHistory={setShowHistory}
          loadingHistory={loadingHistory}
          flashcardsHistory={flashcardsHistory}
          selectedFlashcardsId={selectedFlashcardsId}
          loadFlashcardsVersion={loadFlashcardsVersion}
        />
      </div>
    )
  }

  if (view === 'quiz') {
    return (
      <Motion.div
        key="quiz"
        {...VIEW_FADE}
        style={{ height: '100%', width: '100%' }}
      >
        <QuizView
          quiz={quiz}
          tempQuiz={tempQuiz}
          isGeneratingQuiz={isGeneratingQuiz}
          onViewChange={onViewChange}
          showHistory={showHistory}
          setShowHistory={setShowHistory}
          loadingHistory={loadingHistory}
          quizHistory={quizHistory}
          selectedQuizId={selectedQuizId}
          loadQuizVersion={loadQuizVersion}
          onSendQuestionToChat={onSendQuestionToChat}
        />
      </Motion.div>
    )
  }

  if (view === 'mindmap') {
    return (
      <Motion.div
        key="mindmap"
        {...VIEW_FADE}
        style={{ height: '100%', width: '100%' }}
      >
        <MindmapView
          mindmap={mindmap}
          tempMindmap={tempMindmap}
          isGeneratingMindmap={isGeneratingMindmap}
          onBack={() => {
            setTempMindmap(null)
            setSelectedMindmapId(null)
            onViewChange('studio')
          }}
          onNodeClick={handleMindmapNodeClick}
          expandingNodeId={expandingNodeId}
        />
      </Motion.div>
    )
  }

  if (view === 'highlights') {
    return (
      <Motion.div
        key="highlights"
        {...VIEW_FADE}
        style={{ height: '100%', width: '100%' }}
      >
        <HighlightsView
          highlights={highlights}
          tempHighlights={tempHighlights}
          isGeneratingHighlights={isGeneratingHighlights}
          onBack={() => {
            setTempHighlights(null)
            setSelectedHighlightsId(null)
            onViewChange('studio')
          }}
          onExplainHighlight={onExplainHighlight}
        />
      </Motion.div>
    )
  }

  if (view === 'note') {
    return (
      <Motion.div
        key="note"
        {...VIEW_FADE}
        style={{ height: '100%', width: '100%' }}
      >
        <NoteView
          tempNote={tempNote}
          onBack={() => onViewChange('studio')}
          canEdit={true}
          onEdit={(displayNote) => {
            setEditingNoteId(displayNote.id)
            setNoteTitle(displayNote.title)
            setNoteContent(displayNote.content || '')
            setShowNoteDialog(true)
          }}
        />
      </Motion.div>
    )
  }

  if (view === 'report') {
    return (
      <Motion.div
        key="report"
        {...VIEW_FADE}
        style={{ height: '100%', width: '100%' }}
      >
        <ReportView
          report={report}
          tempReport={tempReport}
          isGeneratingReport={isGeneratingReport}
          onBack={() => {
            setTempReport(null)
            onViewChange('studio')
          }}
        />
      </Motion.div>
    )
  }

  return null
}

export default ViewSwitch