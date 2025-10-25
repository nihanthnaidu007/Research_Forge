import React from 'react';
import { motion } from 'framer-motion';
import { FileText, ExternalLink, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { cn } from '../lib/utils';

function getConfidenceLevel(score) {
  if (score >= 0.85) return 'high';
  if (score >= 0.65) return 'medium';
  return 'low';
}

function ConfidenceBadge({ score }) {
  const level = getConfidenceLevel(score);
  const percentage = Math.round(score * 100);
  
  const config = {
    high: {
      label: 'HIGH',
      bg: 'bg-emerald-500/10',
      text: 'text-emerald-400',
      border: 'border-emerald-500/25',
      icon: TrendingUp,
    },
    medium: {
      label: 'MEDIUM',
      bg: 'bg-amber-500/10',
      text: 'text-amber-400',
      border: 'border-amber-500/25',
      icon: Minus,
    },
    low: {
      label: 'LOW',
      bg: 'bg-rose-500/10',
      text: 'text-rose-400',
      border: 'border-rose-500/25',
      icon: TrendingDown,
    },
  };
  
  const { label, bg, text, border, icon: Icon } = config[level];
  
  return (
    <div className={cn(
      'flex items-center gap-1.5 px-2 py-1 rounded-sm border font-mono text-xs',
      bg, text, border
    )}>
      <Icon className="w-3 h-3" />
      <span className="uppercase tracking-wider">{label}</span>
      <span className="opacity-70">{percentage}%</span>
    </div>
  );
}

function ConfidenceBar({ score }) {
  const percentage = Math.round(score * 100);
  const level = getConfidenceLevel(score);
  
  const barColors = {
    high: 'bg-emerald-500',
    medium: 'bg-amber-500',
    low: 'bg-rose-500',
  };
  
  return (
    <div className="w-full h-1 bg-zinc-800 rounded-full overflow-hidden">
      <motion.div
        initial={{ width: 0 }}
        animate={{ width: `${percentage}%` }}
        transition={{ duration: 0.8, delay: 0.3, ease: 'easeOut' }}
        className={cn('h-full rounded-full', barColors[level])}
      />
    </div>
  );
}

export function ReportSection({ section, confidence, index, totalSections, versioningReport }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.15 }}
      className="section-card relative group"
    >
      {/* Left accent border */}
      <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-cyan-500/50 group-hover:bg-cyan-500 transition-colors" />
      
      <div className="pl-6 py-4">
        {/* Section header */}
        <div className="flex items-start justify-between gap-4 mb-4">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-mono text-zinc-600 uppercase tracking-wider">
                Section {index + 1} of {totalSections}
              </span>
            </div>
            <h3 className="text-lg font-display font-semibold text-zinc-100">
              {section.title}
              {versioningReport && versioningReport.unchanged_ids?.includes(section.section_id) && (
                <span style={{
                  fontSize: '10px',
                  padding: '2px 6px',
                  borderRadius: '4px',
                  background: 'rgba(99, 102, 241, 0.1)',
                  color: '#6366f1',
                  border: '1px solid rgba(99, 102, 241, 0.2)',
                  marginLeft: '8px',
                  verticalAlign: 'middle',
                  fontWeight: 'normal',
                }}>
                  ♻️ reused
                </span>
              )}
              {versioningReport && versioningReport.changed_ids?.includes(section.section_id) && (
                <span style={{
                  fontSize: '10px',
                  padding: '2px 6px',
                  borderRadius: '4px',
                  background: 'rgba(245, 158, 11, 0.1)',
                  color: '#f59e0b',
                  border: '1px solid rgba(245, 158, 11, 0.2)',
                  marginLeft: '8px',
                  verticalAlign: 'middle',
                  fontWeight: 'normal',
                }}>
                  ✍️ re-written
                </span>
              )}
            </h3>
          </div>
          <ConfidenceBadge score={confidence} />
        </div>
        
        {/* Confidence bar */}
        <div className="mb-4">
          <ConfidenceBar score={confidence} />
        </div>
        
        {/* Content */}
        <div className="prose prose-invert prose-sm max-w-none">
          <p className="text-zinc-300 leading-relaxed whitespace-pre-wrap">
            {section.content}
          </p>
        </div>
        
        {/* Footer */}
        <div className="flex items-center gap-4 mt-4 pt-4 border-t border-zinc-800/50">
          <div className="flex items-center gap-1.5 text-xs text-zinc-500">
            <FileText className="w-3.5 h-3.5" />
            <span>{section.word_count} words</span>
          </div>
          {section.sources_used && section.sources_used.length > 0 && (
            <div className="flex items-center gap-1.5 text-xs text-zinc-500">
              <ExternalLink className="w-3.5 h-3.5" />
              <span>{section.sources_used.length} sources cited</span>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

export function ReportOutput({ 
  writtenSections, 
  confidenceScores, 
  sources, 
  overallConfidence,
  versioningReport 
}) {
  if (!writtenSections || writtenSections.length === 0) return null;
  
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-6"
    >
      {/* Overall stats header */}
      <div className="flex items-center justify-between p-4 rounded-sm bg-zinc-900/30 border border-zinc-800">
        <div>
          <h2 className="text-xl font-display font-bold text-zinc-100">
            Research Report
          </h2>
          <p className="text-sm text-zinc-500 mt-0.5">
            {writtenSections.length} sections • {sources?.length || 0} sources
          </p>
        </div>
        <div className="text-right">
          <div className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-1">
            Overall Confidence
          </div>
          <ConfidenceBadge score={overallConfidence} />
        </div>
      </div>
      
      {/* Sections */}
      <div className="space-y-6">
        {writtenSections.map((section, index) => (
          <ReportSection
            key={section.section_id || index}
            section={section}
            confidence={confidenceScores[section.section_id] || 0.5}
            index={index}
            totalSections={writtenSections.length}
            versioningReport={versioningReport}
          />
        ))}
      </div>
      
      {/* Citations */}
      {sources && sources.length > 0 && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="mt-8 pt-6 border-t border-zinc-800"
        >
          <h3 className="text-sm font-mono text-zinc-500 uppercase tracking-wider mb-4">
            References ({sources.length})
          </h3>
          <div className="space-y-2">
            {sources.map((source, index) => (
              <div 
                key={index}
                className="flex items-start gap-3 text-sm p-2 rounded-sm hover:bg-zinc-900/50 transition-colors"
              >
                <span className="flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-sm bg-zinc-800 text-xs font-mono text-zinc-400">
                  {source.citation_number}
                </span>
                <div className="flex-1 min-w-0">
                  <a 
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-cyan-400 hover:text-cyan-300 hover:underline truncate block"
                  >
                    {source.title}
                  </a>
                  <span className="text-xs text-zinc-600">{source.domain}</span>
                </div>
                <ExternalLink className="w-3.5 h-3.5 text-zinc-600 flex-shrink-0" />
              </div>
            ))}
          </div>
        </motion.div>
      )}
    </motion.div>
  );
}

export default ReportOutput;
