import React, { useEffect } from 'react';
import { History, RefreshCw, AlertTriangle, FileText } from 'lucide-react';
import { useStore } from '../store';

const STATUS_STYLES = {
  running: { bg: 'rgba(6,182,212,0.12)', color: '#22d3ee', border: 'rgba(6,182,212,0.3)' },
  waiting_approval: { bg: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: 'rgba(245,158,11,0.3)' },
  complete: { bg: 'rgba(34,197,94,0.1)', color: '#22c55e', border: 'rgba(34,197,94,0.3)' },
  error: { bg: 'rgba(244,63,94,0.1)', color: '#fb7185', border: 'rgba(244,63,94,0.3)' },
  expired: { bg: 'rgba(113,113,122,0.15)', color: '#a1a1aa', border: 'rgba(113,113,122,0.3)' },
};

function StatusChip({ status }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.expired;
  return (
    <span
      style={{
        fontSize: '10px',
        padding: '2px 8px',
        borderRadius: '4px',
        background: style.bg,
        color: style.color,
        border: `1px solid ${style.border}`,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        fontFamily: 'monospace',
        flexShrink: 0,
      }}
    >
      {status.replace('_', ' ')}
    </span>
  );
}

function HistoryRow({ entry }) {
  const created = entry.created_at ? new Date(entry.created_at) : null;
  const when = created
    ? created.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      ' ' +
      created.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : '—';

  return (
    <div
      className="p-3 rounded-sm bg-zinc-900/30 border border-zinc-800 hover:border-zinc-700 transition-colors"
      data-testid="history-row"
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <FileText className="w-3.5 h-3.5 text-cyan-500 flex-shrink-0" strokeWidth={1.5} />
          <span className="text-xs text-zinc-300 truncate" title={entry.topic}>
            {entry.topic || 'Untitled report'}
          </span>
        </div>
        <StatusChip status={entry.status} />
      </div>
      <div className="flex items-center gap-2 pl-5 text-[10px] font-mono text-zinc-600">
        <span>{when}</span>
        <span>·</span>
        <span>{entry.depth || 'quick'}</span>
      </div>
    </div>
  );
}

export function HistoryPanel() {
  const history = useStore((s) => s.history);
  const historyLoading = useStore((s) => s.historyLoading);
  const historyError = useStore((s) => s.historyError);
  const fetchHistory = useStore((s) => s.fetchHistory);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  return (
    <div className="space-y-3" data-testid="history-panel">
      <div className="flex items-center justify-between px-1">
        <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-500 flex items-center gap-2">
          <History className="w-3.5 h-3.5" strokeWidth={1.5} />
          Report History
        </h3>
        <button
          onClick={() => fetchHistory()}
          className="text-zinc-600 hover:text-cyan-400 transition-colors"
          data-testid="history-refresh-btn"
          aria-label="Refresh history"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${historyLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {historyError && (
        <div className="flex items-center gap-2 p-2 rounded-sm bg-rose-500/5 border border-rose-500/20">
          <AlertTriangle className="w-3.5 h-3.5 text-rose-400 flex-shrink-0" />
          <span className="text-xs text-rose-400">{historyError}</span>
        </div>
      )}

      {!historyLoading && !historyError && history.length === 0 && (
        <p className="text-xs text-zinc-600 px-1">No reports yet.</p>
      )}

      <div className="space-y-2">
        {history.slice(0, 8).map((entry) => (
          <HistoryRow key={entry.id} entry={entry} />
        ))}
      </div>

      {history.length > 8 && (
        <p className="text-[10px] font-mono text-zinc-600 px-1">
          +{history.length - 8} older reports
        </p>
      )}
    </div>
  );
}

export default HistoryPanel;
