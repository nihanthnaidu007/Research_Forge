import React, { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Hexagon, Zap, Download, Loader2, RotateCcw, AlertTriangle } from 'lucide-react';
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

function CompletedState({ sessionId, writtenSections, confidenceScores, sources, overallConfidence, onReset, versioningReport }) {
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState(null);

  const handleDownload = useCallback(async () => {
    if (!sessionId) {
      setPdfError('No session ID available');
      return;
    }

    setPdfLoading(true);
    setPdfError(null);

    try {
      const response = await fetch(`/api/export-pdf?session_id=${encodeURIComponent(sessionId)}`, {
        method: 'POST',
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `Export failed (${response.status})`);
      }

      const blob = await response.blob();
      const contentDisposition = response.headers.get('Content-Disposition');
      let filename = 'researchforge-report.pdf';
      if (contentDisposition) {
        const match = contentDisposition.match(/filename="?([^"]+)"?/);
        if (match) filename = match[1];
      }

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error('PDF download error:', err);
      setPdfError(err.message);
    } finally {
      setPdfLoading(false);
    }
  }, [sessionId]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-6"
    >
      <div className="flex items-center justify-end gap-3">
        {pdfError && (
          <span className="text-xs text-rose-400 mr-2">{pdfError}</span>
        )}
        <Button
          onClick={onReset}
          variant="outline"
          className="border-zinc-800 hover:border-cyan-500/50 hover:bg-cyan-500/10"
        >
          <RotateCcw className="w-4 h-4 mr-2" />
          New Report
        </Button>
        <Button
          onClick={handleDownload}
          disabled={pdfLoading}
          variant="outline"
          className="border-zinc-800 hover:border-cyan-500/50 hover:bg-cyan-500/10"
          data-testid="download-pdf-btn"
        >
          {pdfLoading ? (
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
          ) : (
            <Download className="w-4 h-4 mr-2" />
          )}
          {pdfLoading ? 'Generating...' : 'Download PDF'}
        </Button>
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
  onEditsChange
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
