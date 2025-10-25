import React from 'react';
import { motion } from 'framer-motion';
import { useStore } from './store';
import Sidebar from './components/Sidebar';
import MainPanel from './components/MainPanel';
import TraceLogPanel from './components/TraceLog';
import './App.css';

function App() {
  const {
    // State
    sessionId,
    topic, setTopic,
    depth, setDepth,
    inputUrls, addUrl, removeUrl,
    uploadedFiles, addFile, removeFile,
    status,
    isLoading,
    currentAgent,
    completedAgents,
    streamUpdates,
    outline,
    outlineApproved,
    updateOutlineSection,
    approveOutline,
    writtenSections,
    confidenceScores,
    sources,
    overallConfidence,
    agentStats,
    error,
    traceUrl,
    versioning_report,
    // Actions
    startReport,
    resetReport,
  } = useStore();
  
  const hasDocuments = uploadedFiles.length > 0 || inputUrls.length > 0;
  
  return (
    <div className="flex h-screen bg-zinc-950 grid-pattern overflow-hidden">
      {/* Scanline overlay effect */}
      <div className="scanline-overlay" />
      
      {/* Left Sidebar */}
      <motion.aside
        initial={{ x: -100, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        className="w-72 flex-shrink-0"
      >
        <Sidebar
          topic={topic}
          setTopic={setTopic}
          depth={depth}
          setDepth={setDepth}
          inputUrls={inputUrls}
          addUrl={addUrl}
          removeUrl={removeUrl}
          uploadedFiles={uploadedFiles}
          addFile={addFile}
          removeFile={removeFile}
          onStartReport={startReport}
          isLoading={isLoading}
          status={status}
          currentAgent={currentAgent}
          completedAgents={completedAgents}
          agentStats={agentStats}
          overallConfidence={overallConfidence}
          hasDocuments={hasDocuments}
        />
      </motion.aside>
      
      {/* Main Content */}
      <motion.main
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.2 }}
        className="flex-1 overflow-hidden"
      >
        <MainPanel
          sessionId={sessionId}
          status={status}
          topic={topic}
          currentAgent={currentAgent}
          streamUpdates={streamUpdates}
          outline={outline}
          outlineApproved={outlineApproved}
          onUpdateSection={updateOutlineSection}
          onApprove={() => approveOutline(outline)}
          isLoading={isLoading}
          writtenSections={writtenSections}
          confidenceScores={confidenceScores}
          sources={sources}
          overallConfidence={overallConfidence}
          error={error}
          onReset={resetReport}
          versioningReport={versioning_report}
        />
      </motion.main>
      
      {/* Right Trace Panel */}
      <motion.aside
        initial={{ x: 100, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        transition={{ delay: 0.3 }}
        className="w-80 flex-shrink-0 hidden xl:block border-l border-zinc-800"
      >
        <TraceLogPanel streamUpdates={streamUpdates} traceUrl={traceUrl} />
      </motion.aside>
    </div>
  );
}

export default App;
