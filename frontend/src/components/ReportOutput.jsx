import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  FileText,
  ExternalLink,
  TrendingUp,
  TrendingDown,
  Minus,
  ShieldCheck,
  AlertTriangle,
  HelpCircle,
  SearchX,
  X,
} from 'lucide-react';
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

/*
 * Citation-integrity presentation (W2). Statuses come from the backend's
 * S2/Crossref enrichment and are shown as-is — the UI never guesses.
 */
const INTEGRITY_META = {
  verified: {
    label: 'VERIFIED',
    icon: ShieldCheck,
    bg: 'bg-emerald-500/10',
    text: 'text-emerald-400',
    border: 'border-emerald-500/25',
  },
  unresolved: {
    label: 'UNRESOLVED',
    icon: SearchX,
    bg: 'bg-amber-500/10',
    text: 'text-amber-400',
    border: 'border-amber-500/25',
  },
  unknown: {
    label: 'UNKNOWN',
    icon: HelpCircle,
    bg: 'bg-zinc-500/10',
    text: 'text-zinc-400',
    border: 'border-zinc-500/25',
  },
  retracted: {
    label: 'RETRACTED',
    icon: AlertTriangle,
    bg: 'bg-rose-500/10',
    text: 'text-rose-400',
    border: 'border-rose-500/25',
  },
};

function getIntegrityStatus(source) {
  if (!source || source.retracted) return 'retracted';
  const status = source.integrity_status;
  return status && INTEGRITY_META[status] ? status : 'unknown';
}

function IntegrityChip({ source }) {
  const status = getIntegrityStatus(source);
  const { label, icon: Icon, bg, text, border } = INTEGRITY_META[status];

  return (
    <div
      className={cn(
        'flex items-center gap-1.5 px-2 py-0.5 rounded-sm border font-mono text-[10px]',
        bg,
        text,
        border
      )}
      data-testid={`integrity-chip-${status}`}
    >
      <Icon className="w-3 h-3" />
      <span className="uppercase tracking-wider">{label}</span>
    </div>
  );
}

/**
 * Split section content into text runs and inline citation markers.
 * The backend has already rewritten [source: url] into bare [n] markers,
 * so the frontend only needs to find them — pure function, unit-tested.
 */
export function splitCitationContent(content) {
  const parts = [];
  const regex = /\[(\d+)\]/g;
  let last = 0;
  let match;
  while ((match = regex.exec(content)) !== null) {
    if (match.index > last) {
      parts.push({ type: 'text', value: content.slice(last, match.index) });
    }
    parts.push({ type: 'citation', number: parseInt(match[1], 10) });
    last = match.index + match[0].length;
  }
  if (last < content.length) {
    parts.push({ type: 'text', value: content.slice(last) });
  }
  return parts;
}

function CitationPanel({ source, number, onClose }) {
  const status = getIntegrityStatus(source);
  const hasSnippet = source && source.snippet && source.snippet.trim() !== '';

  return (
    <div
      data-testid="citation-panel"
      className="mt-4 p-4 rounded-sm bg-zinc-900/60 border border-zinc-800"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-sm bg-cyan-500/10 text-xs font-mono text-cyan-400 border border-cyan-500/25">
            {number}
          </span>
          <IntegrityChip source={source} />
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close citation panel"
          className="text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {source ? (
        <>
          <a
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="block mt-3 text-sm font-medium text-cyan-400 hover:text-cyan-300 hover:underline"
          >
            {source.title}
          </a>
          <span className="text-xs font-mono text-zinc-600">{source.domain}</span>

          {hasSnippet ? (
            <p
              data-testid="citation-snippet"
              className="mt-3 text-sm text-zinc-400 italic leading-relaxed border-l-2 border-zinc-700 pl-3"
            >
              {source.snippet}
            </p>
          ) : (
            <p
              data-testid="citation-snippet-empty"
              className="mt-3 text-xs text-zinc-600 italic"
            >
              No snippet recorded for this source.
            </p>
          )}

          <div className="flex items-center gap-3 mt-3 text-xs text-zinc-500">
            {typeof source.citation_count === 'number' ? (
              <span data-testid="citation-count">
                {source.citation_count.toLocaleString()} citations
              </span>
            ) : (
              <span>Citation count unavailable</span>
            )}
          </div>

          {status === 'retracted' && (
            <p
              data-testid="retraction-warning"
              className="mt-3 flex items-center gap-2 text-xs text-rose-400"
            >
              <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
              This source has been retracted — do not rely on its claims.
            </p>
          )}
        </>
      ) : (
        <p className="mt-3 text-sm text-zinc-500">
          No source record for citation [{number}].
        </p>
      )}
    </div>
  );
}

export function ReportSection({
  section,
  confidence,
  index,
  totalSections,
  versioningReport,
  sources,
}) {
  // Component-local state — no state library; only one panel open at a time.
  const [activeCitation, setActiveCitation] = useState(null);

  const sourceByNumber = useMemo(() => {
    const map = new Map();
    (sources || []).forEach((s) => {
      if (typeof s.citation_number === 'number') map.set(s.citation_number, s);
    });
    return map;
  }, [sources]);

  const contentParts = splitCitationContent(section.content || '');

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
            {contentParts.map((part, partIndex) =>
              part.type === 'text' ? (
                <React.Fragment key={partIndex}>{part.value}</React.Fragment>
              ) : (
                <button
                  key={partIndex}
                  type="button"
                  onClick={() => setActiveCitation(part.number)}
                  aria-label={`Show citation ${part.number}`}
                  className={cn(
                    'inline-flex items-baseline justify-center min-w-[1.6rem] px-1 mx-0.5 rounded-sm border font-mono text-xs align-super transition-colors',
                    activeCitation === part.number
                      ? 'bg-cyan-500/15 text-cyan-300 border-cyan-500/40'
                      : 'bg-cyan-500/10 text-cyan-400 border-cyan-500/25 hover:bg-cyan-500/20 hover:text-cyan-300'
                  )}
                >
                  {part.number}
                </button>
              )
            )}
          </p>
        </div>

        {/* Interactive citation panel (W2) */}
        {activeCitation !== null && (
          <CitationPanel
            source={sourceByNumber.get(activeCitation)}
            number={activeCitation}
            onClose={() => setActiveCitation(null)}
          />
        )}
        
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
            sources={sources}
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
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-xs text-zinc-600">{source.domain}</span>
                    <IntegrityChip source={source} />
                  </div>
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
