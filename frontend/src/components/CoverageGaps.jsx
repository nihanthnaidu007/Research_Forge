import React from 'react';
import { AlertTriangle, CircleCheck } from 'lucide-react';

/**
 * Coverage-gap transparency (R3). coverage_gaps is a list of
 * human-readable strings computed by the supervisor's deterministic
 * coverage check (graph/supervisor.py evaluate_coverage_gaps) and
 * persisted on the session. This panel only presents them — it never
 * fabricates a gap or hides one.
 */
export default function CoverageGapsPanel({ gaps }) {
  const gapList = Array.isArray(gaps) ? gaps.filter(Boolean) : [];

  if (gapList.length === 0) {
    return (
      <div
        data-testid="coverage-panel-clear"
        className="flex items-center gap-2 p-3 rounded-sm bg-emerald-500/5 border border-emerald-500/20"
      >
        <CircleCheck className="w-4 h-4 text-emerald-400 flex-shrink-0" />
        <p className="text-xs text-emerald-300/90">
          No coverage gaps were flagged for this report.
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid="coverage-panel"
      className="p-4 rounded-sm bg-amber-500/5 border border-amber-500/25"
    >
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
        <h3 className="text-sm font-mono text-amber-400 uppercase tracking-wider">
          Coverage Gaps ({gapList.length})
        </h3>
      </div>
      <p className="text-xs text-zinc-500 mb-3">
        The coverage check flagged parts of this report with weak sourcing or
        unsupported claims. Treat these areas with extra caution.
      </p>
      <ul className="space-y-1.5">
        {gapList.map((gap, index) => (
          <li
            key={index}
            data-testid="coverage-gap"
            className="text-xs text-zinc-300 leading-relaxed flex gap-2"
          >
            <span className="text-amber-500/70 flex-shrink-0">•</span>
            <span>{gap}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
