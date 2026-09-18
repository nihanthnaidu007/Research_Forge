import React from 'react';
import {
  ShieldCheck,
  ShieldQuestion,
  ShieldX,
  HelpCircle,
  ExternalLink,
} from 'lucide-react';
import { cn } from '../lib/utils';

/**
 * Fact-check verdict panel (R2). Verdicts are computed by the fact-check
 * agents and persisted on the session (fact_check_results) — this panel
 * only presents them. The backend emits exactly three verdict values
 * (graph/agents/factcheck_parallel.py); anything else renders as UNKNOWN
 * rather than being guessed into a supported/unsupported bucket.
 */
const VERDICT_META = {
  SUPPORTED: {
    label: 'SUPPORTED',
    icon: ShieldCheck,
    bg: 'bg-emerald-500/10',
    text: 'text-emerald-400',
    border: 'border-emerald-500/25',
  },
  PARTIALLY_SUPPORTED: {
    label: 'PARTIAL',
    icon: ShieldQuestion,
    bg: 'bg-amber-500/10',
    text: 'text-amber-400',
    border: 'border-amber-500/25',
  },
  UNSUPPORTED: {
    label: 'UNSUPPORTED',
    icon: ShieldX,
    bg: 'bg-rose-500/10',
    text: 'text-rose-400',
    border: 'border-rose-500/25',
  },
  UNKNOWN: {
    label: 'UNKNOWN',
    icon: HelpCircle,
    bg: 'bg-zinc-500/10',
    text: 'text-zinc-400',
    border: 'border-zinc-500/25',
  },
};

function verdictMeta(verdict) {
  return VERDICT_META[verdict] || VERDICT_META.UNKNOWN;
}

// Pure tally — exported for tests.
export function tallyVerdicts(results) {
  const tally = { SUPPORTED: 0, PARTIALLY_SUPPORTED: 0, UNSUPPORTED: 0, UNKNOWN: 0 };
  (results || []).forEach((r) => {
    const key = r && VERDICT_META[r.verdict] ? r.verdict : 'UNKNOWN';
    tally[key] += 1;
  });
  return tally;
}

function VerdictRow({ result }) {
  const meta = verdictMeta(result.verdict);
  const Icon = meta.icon;
  const hasConfidence = typeof result.confidence === 'number';
  const supportingUrls = Array.isArray(result.supporting_urls)
    ? result.supporting_urls.filter(Boolean)
    : [];

  return (
    <div
      data-testid="verdict-row"
      // Raw backend verdict value (PARTIALLY_SUPPORTED), not the short
      // display label — keeps the data attribute a faithful data hook.
      data-verdict={VERDICT_META[result.verdict] ? result.verdict : 'UNKNOWN'}
      className="p-3 rounded-sm bg-zinc-900/30 border border-zinc-800"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-zinc-300 leading-relaxed flex-1">
          {result.claim}
        </p>
        <div
          className={cn(
            'flex items-center gap-1.5 px-2 py-1 rounded-sm border font-mono text-xs flex-shrink-0',
            meta.bg,
            meta.text,
            meta.border
          )}
        >
          <Icon className="w-3 h-3" />
          <span className="uppercase tracking-wider">{meta.label}</span>
        </div>
      </div>

      {result.reasoning && (
        <p className="mt-2 text-xs text-zinc-500 leading-relaxed">
          {result.reasoning}
        </p>
      )}

      <div className="flex items-center gap-3 mt-2 text-xs text-zinc-600">
        {hasConfidence ? (
          <span data-testid="verdict-confidence">
            {Math.round(result.confidence * 100)}% confidence
          </span>
        ) : (
          <span>Confidence not recorded</span>
        )}
        {supportingUrls.length > 0 && (
          <span className="flex items-center gap-2" data-testid="verdict-sources">
            {supportingUrls.map((url) => (
              <a
                key={url}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-cyan-400/80 hover:text-cyan-300 hover:underline"
              >
                <ExternalLink className="w-3 h-3" />
                source
              </a>
            ))}
          </span>
        )}
      </div>
    </div>
  );
}

export default function VerdictPanel({ results }) {
  const hasResults = Array.isArray(results) && results.length > 0;

  if (!hasResults) {
    return (
      <div
        data-testid="verdict-panel-empty"
        className="p-4 rounded-sm bg-zinc-900/30 border border-zinc-800"
      >
        <h3 className="text-sm font-mono text-zinc-500 uppercase tracking-wider">
          Fact-Check Verdicts
        </h3>
        <p className="mt-2 text-xs text-zinc-600">
          No fact-check verdicts were recorded for this report.
        </p>
      </div>
    );
  }

  const tally = tallyVerdicts(results);

  return (
    <div
      data-testid="verdict-panel"
      className="p-4 rounded-sm bg-zinc-900/30 border border-zinc-800"
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-mono text-zinc-500 uppercase tracking-wider">
          Fact-Check Verdicts
        </h3>
        <div
          className="flex items-center gap-3 text-xs font-mono"
          data-testid="verdict-tally"
        >
          <span className="text-emerald-400">{tally.SUPPORTED} supported</span>
          <span className="text-amber-400">{tally.PARTIALLY_SUPPORTED} partial</span>
          <span className="text-rose-400">{tally.UNSUPPORTED} unsupported</span>
        </div>
      </div>
      <div className="space-y-2">
        {results.map((result, index) => (
          <VerdictRow key={index} result={result} />
        ))}
      </div>
    </div>
  );
}
