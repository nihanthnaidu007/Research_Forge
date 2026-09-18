import React, { useEffect, useState } from 'react';
import { History, RefreshCw, AlertTriangle, FileText, Search, RotateCcw } from 'lucide-react';
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

function HistoryRow({ entry, onOpen, disabled }) {
  const created = entry.created_at ? new Date(entry.created_at) : null;
  const when = created
    ? created.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      ' ' +
      created.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : '—';
  const reopenable = entry.status === 'complete';

  return (
    <button
      type="button"
      className="w-full text-left p-3 rounded-sm bg-zinc-900/30 border border-zinc-800 hover:border-cyan-500/40 hover:bg-zinc-900/60 transition-colors cursor-pointer disabled:cursor-wait disabled:opacity-60"
      data-testid="history-row"
      data-testid-status={entry.status}
      onClick={() => onOpen(entry.id)}
      disabled={disabled}
      aria-label={`Reopen report: ${entry.topic || 'Untitled report'}`}
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
        {reopenable && (
          <span className="flex items-center gap-1 ml-auto text-cyan-500/70">
            <RotateCcw className="w-3 h-3" strokeWidth={1.5} />
            Reopen
          </span>
        )}
      </div>
    </button>
  );
}

export function HistoryPanel() {
  const history = useStore((s) => s.history);
  const historyLoading = useStore((s) => s.historyLoading);
  const historyError = useStore((s) => s.historyError);
  const fetchHistory = useStore((s) => s.fetchHistory);
  const openSession = useStore((s) => s.openSession);
  const reopenLoading = useStore((s) => s.reopenLoading);
  const reopenError = useStore((s) => s.reopenError);

  // Topic search (W5): debounced server-side query. While searching, the
  // 8-row display cap is lifted — hiding server-filtered matches behind a
  // client-side slice would make the panel lie about what it found.
  const [search, setSearch] = useState('');
  const [searchDebounced, setSearchDebounced] = useState('');

  useEffect(() => {
    const t = setTimeout(() => setSearchDebounced(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    fetchHistory(searchDebounced);
  }, [fetchHistory, searchDebounced]);

  const searching = searchDebounced.trim().length > 0;
  const visible = searching ? history : history.slice(0, 8);

  return (
    <div className="space-y-3" data-testid="history-panel">
      <div className="flex items-center justify-between px-1">
        <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-500 flex items-center gap-2">
          <History className="w-3.5 h-3.5" strokeWidth={1.5} />
          Report History
        </h3>
        <button
          onClick={() => fetchHistory(searchDebounced)}
          className="text-zinc-600 hover:text-cyan-400 transition-colors"
          data-testid="history-refresh-btn"
          aria-label="Refresh history"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${historyLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="relative">
        <Search className="w-3 h-3 text-zinc-600 absolute left-2 top-1/2 -translate-y-1/2" strokeWidth={1.5} />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search topics..."
          className="w-full pl-7 pr-2 py-1.5 text-xs rounded-sm bg-zinc-950 border border-zinc-800 focus:border-cyan-500/50 focus:outline-none text-zinc-300 placeholder:text-zinc-600"
          data-testid="history-search-input"
        />
      </div>

      {historyError && (
        <div className="flex items-center gap-2 p-2 rounded-sm bg-rose-500/5 border border-rose-500/20">
          <AlertTriangle className="w-3.5 h-3.5 text-rose-400 flex-shrink-0" />
          <span className="text-xs text-rose-400">{historyError}</span>
        </div>
      )}

      {reopenError && (
        <div
          className="flex items-center gap-2 p-2 rounded-sm bg-amber-500/5 border border-amber-500/20"
          data-testid="reopen-error"
          role="status"
        >
          <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
          <span className="text-xs text-amber-400">{reopenError}</span>
        </div>
      )}

      {!historyLoading && !historyError && history.length === 0 && (
        <p className="text-xs text-zinc-600 px-1">
          {searching ? 'No matching reports.' : 'No reports yet.'}
        </p>
      )}

      <div className="space-y-2">
        {visible.map((entry) => (
          <HistoryRow
            key={entry.id}
            entry={entry}
            onOpen={openSession}
            disabled={reopenLoading}
          />
        ))}
      </div>

      {!searching && history.length > 8 && (
        <p className="text-[10px] font-mono text-zinc-600 px-1">
          +{history.length - 8} older reports
        </p>
      )}
    </div>
  );
}

export default HistoryPanel;
