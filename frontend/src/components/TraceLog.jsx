import React, { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Terminal, ExternalLink } from 'lucide-react';
import { cn } from '../lib/utils';
import { ScrollArea } from './ui/scroll-area';

function parseLogEntry(message) {
  // Extract timestamp and content
  const timestampMatch = message.match(/^\[(\d{2}:\d{2}:\d{2})\]/);
  const timestamp = timestampMatch ? timestampMatch[1] : '';
  const content = timestampMatch ? message.slice(timestampMatch[0].length).trim() : message;
  
  // Determine log type
  let type = 'info';
  if (content.includes('✓') || content.includes('Complete')) type = 'success';
  if (content.includes('✗') || content.includes('Error')) type = 'error';
  if (content.includes('⏸') || content.includes('WAITING')) type = 'warning';
  if (content.includes('Supervisor')) type = 'supervisor';
  
  return { timestamp, content, type };
}

function LogEntry({ message, index }) {
  const { timestamp, content, type } = parseLogEntry(message);
  
  const typeColors = {
    info: 'text-zinc-400',
    success: 'text-emerald-400',
    error: 'text-rose-400',
    warning: 'text-amber-400',
    supervisor: 'text-cyan-400',
  };
  
  return (
    <motion.div
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.02, duration: 0.2 }}
      className="log-entry flex gap-2 py-1.5 border-b border-zinc-900/50 last:border-0"
    >
      <span className="text-zinc-600 font-mono text-xs flex-shrink-0">
        {timestamp}
      </span>
      <span className={cn('font-mono text-xs leading-relaxed', typeColors[type])}>
        {content}
      </span>
    </motion.div>
  );
}

export function TraceLogPanel({ streamUpdates, traceUrl }) {
  const logEndRef = useRef(null);
  
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [streamUpdates]);
  
  return (
    <div className="h-full flex flex-col bg-zinc-950">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800">
        <Terminal className="w-4 h-4 text-cyan-500" strokeWidth={1.5} />
        <h3 className="text-sm font-medium text-zinc-300">Execution Trace</h3>
        <div className="ml-auto flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-xs text-zinc-500">Live</span>
        </div>
      </div>
      
      {/* Log entries */}
      <ScrollArea className="flex-1 p-4">
        <AnimatePresence mode="popLayout">
          {streamUpdates.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-zinc-600">
              <Terminal className="w-8 h-8 mb-2 opacity-50" />
              <p className="text-xs font-mono">Waiting for execution...</p>
            </div>
          ) : (
            streamUpdates.map((update, index) => (
              <LogEntry key={index} message={update} index={index} />
            ))
          )}
        </AnimatePresence>
        <div ref={logEndRef} />
      </ScrollArea>
      
      {/* Footer with LangSmith link */}
      <div className="px-4 py-3 border-t border-zinc-800">
        {traceUrl ? (
          <a
            href={traceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            <ExternalLink className="w-3 h-3" />
            <span className="font-mono">View in LangSmith ↗</span>
          </a>
        ) : (
          <span className="flex items-center gap-1.5 text-xs text-zinc-600 font-mono">
            <ExternalLink className="w-3 h-3" />
            LangSmith trace: not configured
          </span>
        )}
      </div>
    </div>
  );
}

export default TraceLogPanel;
