import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { 
  Hexagon, Play, Upload, Link, X, Trash2, 
  Gauge, FileText, Target, Zap 
} from 'lucide-react';
import { cn } from '../lib/utils';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import { Label } from './ui/label';
import { Tabs, TabsList, TabsTrigger } from './ui/tabs';
import AgentStatusPanel from './AgentStatus';

function StatCard({ icon: Icon, label, value, subtext }) {
  return (
    <div className="p-3 rounded-sm bg-zinc-900/30 border border-zinc-800">
      <div className="flex items-center gap-2 mb-1">
        <Icon className="w-3.5 h-3.5 text-cyan-500" strokeWidth={1.5} />
        <span className="text-xs font-mono text-zinc-500 uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-xl font-display font-bold text-zinc-100">{value}</div>
      {subtext && <div className="text-xs text-zinc-600 mt-0.5">{subtext}</div>}
    </div>
  );
}

export function Sidebar({ 
  topic, setTopic,
  depth, setDepth,
  inputUrls, addUrl, removeUrl,
  uploadedFiles, addFile, removeFile,
  onStartReport,
  isLoading,
  status,
  currentAgent,
  completedAgents,
  agentStats,
  overallConfidence,
  hasDocuments
}) {
  const [urlInput, setUrlInput] = useState('');
  
  const handleAddUrl = () => {
    if (urlInput.trim() && urlInput.startsWith('http')) {
      addUrl(urlInput.trim());
      setUrlInput('');
    }
  };
  
  const handleFileUpload = (e) => {
    const files = Array.from(e.target.files);
    files.forEach(file => {
      if (file.type === 'application/pdf') {
        addFile(file);
      }
    });
  };
  
  const isRunning = ['running', 'waiting_approval'].includes(status);
  
  return (
    <div className="h-screen flex flex-col bg-zinc-950/80 border-r border-zinc-800">
      {/* Logo */}
      <div className="p-6 border-b border-zinc-800">
        <div className="flex items-center gap-3">
          <div className="relative">
            <Hexagon className="w-10 h-10 text-cyan-500" strokeWidth={1.5} fill="rgba(6,182,212,0.1)" />
            <Zap className="w-4 h-4 text-cyan-400 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" strokeWidth={2} />
          </div>
          <div>
            <h1 className="text-lg font-display font-bold text-zinc-100">ResearchForge</h1>
            <p className="text-xs text-zinc-500">Multi-Agent Research Intelligence</p>
          </div>
        </div>
      </div>
      
      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        {/* Input Section */}
        <div className="space-y-4">
          <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-500 px-1">
            Research Input
          </h3>
          
          {/* Topic Input */}
          <div className="space-y-2">
            <Label className="text-xs text-zinc-400">Topic</Label>
            <Textarea
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="Enter your research topic..."
              rows={3}
              disabled={isRunning}
              className="bg-zinc-950 border-zinc-800 focus:border-cyan-500/50 resize-none"
              data-testid="topic-input"
            />
          </div>
          
          {/* Depth Selector */}
          <div className="space-y-2">
            <Label className="text-xs text-zinc-400">Report Depth</Label>
            <Tabs value={depth} onValueChange={setDepth} className="w-full">
              <TabsList className="w-full bg-zinc-900 border border-zinc-800">
                <TabsTrigger 
                  value="quick" 
                  className="flex-1 data-[state=active]:bg-cyan-500/10 data-[state=active]:text-cyan-400"
                  disabled={isRunning}
                  data-testid="depth-quick"
                >
                  <Gauge className="w-3.5 h-3.5 mr-1.5" />
                  Quick (3)
                </TabsTrigger>
                <TabsTrigger 
                  value="deep" 
                  className="flex-1 data-[state=active]:bg-cyan-500/10 data-[state=active]:text-cyan-400"
                  disabled={isRunning}
                  data-testid="depth-deep"
                >
                  <Target className="w-3.5 h-3.5 mr-1.5" />
                  Deep (6)
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </div>
        
        {/* Documents Section */}
        <div className="space-y-4">
          <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-500 px-1">
            Documents (Optional)
          </h3>
          
          {/* PDF Upload */}
          <div className="space-y-2">
            <Label className="text-xs text-zinc-400">Upload PDFs</Label>
            <label 
              className={cn(
                "flex flex-col items-center gap-2 p-4 rounded-sm border border-dashed border-zinc-800 cursor-pointer hover:border-cyan-500/50 hover:bg-cyan-500/5 transition-colors",
                isRunning && "opacity-50 pointer-events-none"
              )}
            >
              <Upload className="w-5 h-5 text-zinc-500" />
              <span className="text-xs text-zinc-500">Click to upload PDFs</span>
              <input
                type="file"
                accept=".pdf"
                multiple
                onChange={handleFileUpload}
                className="hidden"
                disabled={isRunning}
                data-testid="pdf-upload"
              />
            </label>
            
            {/* Uploaded files list */}
            {uploadedFiles.length > 0 && (
              <div className="space-y-1.5">
                {uploadedFiles.map((file, index) => (
                  <div key={index} className="flex items-center gap-2 px-2 py-1.5 rounded-sm bg-zinc-900/50 text-xs">
                    <FileText className="w-3.5 h-3.5 text-zinc-500" />
                    <span className="flex-1 truncate text-zinc-300">{file.name}</span>
                    <button
                      onClick={() => removeFile(index)}
                      className="text-zinc-600 hover:text-rose-400"
                      disabled={isRunning}
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          
          {/* URL Input */}
          <div className="space-y-2">
            <Label className="text-xs text-zinc-400">Paste URLs</Label>
            <div className="flex gap-2">
              <Input
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                placeholder="https://..."
                disabled={isRunning}
                className="bg-zinc-950 border-zinc-800 focus:border-cyan-500/50 text-sm"
                onKeyDown={(e) => e.key === 'Enter' && handleAddUrl()}
                data-testid="url-input"
              />
              <Button
                onClick={handleAddUrl}
                variant="outline"
                size="sm"
                disabled={isRunning}
                className="border-zinc-800 hover:bg-cyan-500/10 hover:border-cyan-500/50"
              >
                <Link className="w-4 h-4" />
              </Button>
            </div>
            
            {/* URL list */}
            {inputUrls.length > 0 && (
              <div className="space-y-1.5">
                {inputUrls.map((url, index) => (
                  <div key={index} className="flex items-center gap-2 px-2 py-1.5 rounded-sm bg-zinc-900/50 text-xs">
                    <Link className="w-3.5 h-3.5 text-zinc-500 flex-shrink-0" />
                    <span className="flex-1 truncate text-cyan-400">{url}</span>
                    <button
                      onClick={() => removeUrl(index)}
                      className="text-zinc-600 hover:text-rose-400"
                      disabled={isRunning}
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        
        {/* Run Button */}
        <Button
          onClick={onStartReport}
          disabled={isLoading || isRunning || !topic.trim()}
          className="w-full btn-primary py-3 rounded-sm font-medium"
          data-testid="run-report-btn"
        >
          {isLoading ? (
            <>Processing...</>
          ) : isRunning ? (
            <>Running...</>
          ) : (
            <>
              <Play className="w-4 h-4 mr-2" />
              Generate Report
            </>
          )}
        </Button>
        
        {/* Agent Status */}
        {status !== 'idle' && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <AgentStatusPanel 
              currentAgent={currentAgent}
              completedAgents={completedAgents}
              hasDocuments={hasDocuments}
            />
          </motion.div>
        )}
        
        {/* Stats */}
        {status !== 'idle' && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
            className="space-y-3"
          >
            <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-500 px-1">
              Statistics
            </h3>
            <div className="grid grid-cols-2 gap-2">
              <StatCard 
                icon={FileText}
                label="Sources"
                value={agentStats?.sourcesFound || 0}
              />
              <StatCard 
                icon={Target}
                label="Claims"
                value={agentStats?.claimsChecked || 0}
              />
            </div>
            {overallConfidence > 0 && (
              <StatCard 
                icon={Gauge}
                label="Confidence"
                value={`${Math.round(overallConfidence * 100)}%`}
                subtext={overallConfidence >= 0.85 ? 'High' : overallConfidence >= 0.65 ? 'Medium' : 'Low'}
              />
            )}
          </motion.div>
        )}
      </div>
    </div>
  );
}

export default Sidebar;
