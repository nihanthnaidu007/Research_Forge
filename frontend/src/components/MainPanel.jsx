import React, { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Hexagon, Zap, Download, Loader2, RotateCcw, AlertTriangle, FileCode, FileDown, FileText, BookMarked } from 'lucide-react';
import { Button } from './ui/button';
import OutlineApprovalZone from './OutlineApproval';
import ReportOutput from './ReportOutput';

function WelcomeState() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="h-full flex flex-col items-center justify-center text-center p-8"
    >
      <div className="relative mb-6">
        <Hexagon className="w-20 h-20 text-cyan-500/30" strokeWidth={1} fill="rgba(6,182,212,0.05)" />
        <Zap className="w-8 h-8 text-cyan-500 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" strokeWidth={1.5} />
      </div>
      <h2 className="text-2xl font-display font-bold text-zinc-200 mb-3">
        Ready to Research
      </h2>
      <p className="text-zinc-500 max-w-md mb-8">
        Enter a topic in the sidebar to begin. Our multi-agent system will research, 
        fact-check, and synthesize a comprehensive report with citations.
      </p>
      <div className="flex gap-6 text-sm text-zinc-600">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-cyan-500" />
          <span>AI Research</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-amber-500" />
          <span>Fact Checking</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-500" />
          <span>Auto Citations</span>
        </div>
      </div>
    </motion.div>
  );
}

function ProcessingState({ topic, streamUpdates }) {
  const latestUpdate = streamUpdates && streamUpdates.length > 0
    ? streamUpdates[streamUpdates.length - 1]
    : 'Initializing agents...';

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="h-full flex flex-col items-center justify-center text-center p-8"
    >
      <div className="relative mb-6">
        <Loader2 className="w-16 h-16 text-cyan-500 animate-spin" strokeWidth={1} />
      </div>
      <h2 className="text-xl font-display font-semibold text-zinc-200 mb-2">
        Researching: {topic}
      </h2>
      <p className="text-zinc-500 max-w-lg text-sm font-mono mt-2">
        {latestUpdate}
      </p>
    </motion.div>
  );
}

function ErrorState({ error, onReset }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="flex flex-col items-center justify-center h-full text-center p-8"
    >
      <div className="p-4 rounded-sm bg-rose-500/10 border border-rose-500/20 mb-4 flex items-center gap-3">
        <AlertTriangle className="w-5 h-5 text-rose-400 flex-shrink-0" />
        <p className="text-rose-400 text-sm">{error}</p>
      </div>
      <p className="text-zinc-500 text-sm mb-4">Please try again or check the trace log for details.</p>
      <Button
        onClick={onReset}
        variant="outline"
        className="border-zinc-800 hover:border-cyan-500/50 hover:bg-cyan-500/10"
      >
        <RotateCcw className="w-4 h-4 mr-2" />
        Try Again
      </Button>
    </motion.div>
  );
}

const EXPORT_FORMATS = [
  { format: 'pdf', label: 'PDF', icon: Download, testid: 'download-pdf-btn' },
  { format: 'markdown', label: 'Markdown', icon: FileText, testid: 'download-markdown-btn' },
  { format: 'html', label: 'HTML', icon: FileCode, testid: 'download-html-btn' },
  { format: 'docx', label: 'DOCX', icon: FileDown, testid: 'download-docx-btn' },
  { format: 'bibtex', label: 'BibTeX', icon: BookMarked, testid: 'download-bibtex-btn' },
];

function CompletedState({ sessionId, writtenSections, confidenceScores, sources, overallConfidence, onReset, versioningReport, onExport }) {
  const [exportLoading, setExportLoading] = useState(null); // format id
  const [exportError, setExportError] = useState(null);

  const handleExport = useCallback(async (format) => {
    if (!sessionId) {
      setExportError('No session ID available');
      return;
    }

    setExportLoading(format);
    setExportError(null);

    try {
      await onExport(format);
    } catch (err) {
      console.error(`${format} export error:`, err);
      setExportError(err.message);
    } finally {
      setExportLoading(null);
    }
  }, [sessionId, onExport]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-6"
    >
      <div className="flex items-center justify-end gap-3">
        {exportError && (
          <span className="text-xs text-rose-400 mr-2">{exportError}</span>
        )}
        <Button
          onClick={onReset}
          variant="outline"
          className="border-zinc-800 hover:border-cyan-500/50 hover:bg-cyan-500/10"
        >
          <RotateCcw className="w-4 h-4 mr-2" />
          New Report
        </Button>
        {EXPORT_FORMATS.map(({ format, label, icon: Icon, testid }) => (
          <Button
            key={format}
            onClick={() => handleExport(format)}
            disabled={exportLoading !== null}
            variant="outline"
            className="border-zinc-800 hover:border-cyan-500/50 hover:bg-cyan-500/10"
            data-testid={testid}
          >
            {exportLoading === format ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <Icon className="w-4 h-4 mr-2" />
            )}
            {exportLoading === format ? 'Generating...' : `Download ${label}`}
          </Button>
        ))}
      </div>
      
      <ReportOutput 
        writtenSections={writtenSections}
        confidenceScores={confidenceScores}
        sources={sources}
        overallConfidence={overallConfidence}
        versioningReport={versioningReport}
      />
    </motion.div>
  );
}

export function MainPanel({
  sessionId,
  status,
  topic,
  currentAgent,
  streamUpdates,
  outline,
  outlineApproved,
  onUpdateSection,
  onApprove,
  isLoading,
  writtenSections,
  confidenceScores,
  sources,
  overallConfidence,
  error,
  onReset,
  versioningReport,
  outlineEdits,
  onEditsChange,
  onSaveOutline,
  onExport
}) {
  const renderContent = () => {
    // Error state — check first so errors always show
    if (status === 'error' && error) {
      return <ErrorState error={error} onReset={onReset} />;
    }
    
    // Idle state
    if (status === 'idle') {
      return <WelcomeState />;
    }
    
    // Waiting for outline approval
    if (status === 'waiting_approval' && outline.length > 0) {
      return (
        <OutlineApprovalZone
          outline={outline}
          onUpdateSection={onUpdateSection}
          onApprove={onApprove}
          onSave={onSaveOutline}
          isLoading={isLoading}
          outlineEdits={outlineEdits}
          onEditsChange={onEditsChange}
        />
      );
    }
    
    // Running (before outline or after approval)
    if (status === 'running') {
      return <ProcessingState topic={topic} streamUpdates={streamUpdates} />;
    }
    
    // Complete state
    if (status === 'complete') {
      return (
        <CompletedState
          sessionId={sessionId}
          writtenSections={writtenSections}
          confidenceScores={confidenceScores}
          sources={sources}
          overallConfidence={overallConfidence}
          onReset={onReset}
          versioningReport={versioningReport}
          onExport={onExport}
        />
      );
    }
    
    return <WelcomeState />;
  };
  
  return (
    <div className="h-full p-6 lg:p-8 overflow-y-auto">
      <AnimatePresence mode="wait">
        {renderContent()}
      </AnimatePresence>
    </div>
  );
}

export default MainPanel;
