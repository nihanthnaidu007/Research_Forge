import { useMemo, useRef, useState } from 'react';
import { AlertTriangle, Loader2, MessageSquare, Send } from 'lucide-react';

import { Button } from './ui/button';
import { CitationPanel, splitCitationContent } from './ReportOutput';
import { useStore } from '../store';
import { cn } from '../lib/utils';

/**
 * Chat-with-report panel (W3). Rendered only inside CompletedState — the
 * one state where the report and its sources exist to ground answers on.
 *
 * State split (pre-flight findings §0.2): the transcript lives in the
 * Zustand store (`chatMessages`), the draft box is component-local — a
 * draft is ephemeral UI state, a transcript is session state.
 */
export function ChatPanel({ sources }) {
  const chatMessages = useStore((s) => s.chatMessages);
  const chatLoading = useStore((s) => s.chatLoading);
  const chatError = useStore((s) => s.chatError);
  const chatSessionExpired = useStore((s) => s.chatSessionExpired);
  const sendChatMessage = useStore((s) => s.sendChatMessage);
  const clearChatError = useStore((s) => s.clearChatError);

  const [draft, setDraft] = useState('');
  // The open citation as { number, source, turnIndex } — keyed to its turn so
  // a panel never bleeds onto the same citation number in another message.
  const [openCitation, setOpenCitation] = useState(null);
  const scrollRef = useRef(null);

  // citation_number -> Source map, same shape ReportSection builds; only
  // well-formed numbers resolve, so hallucinated [n] markers stay flagged.
  const sourceByNumber = useMemo(() => {
    const map = new Map();
    (sources || []).forEach((source) => {
      if (typeof source?.citation_number === 'number') {
        map.set(source.citation_number, source);
      }
    });
    return map;
  }, [sources]);

  const handleSend = async () => {
    const message = draft.trim();
    if (!message || chatLoading) return;
    setDraft('');
    setOpenCitation(null);
    await sendChatMessage(message);
    // Keep the newest turn visible without a scroll-to-top surprise.
    requestAnimationFrame(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    });
  };

  const handleCitationClick = (number, turnIndex) => {
    const source = sourceByNumber.get(number) || null;
    setOpenCitation((prev) =>
      prev && prev.number === number
        ? null
        : { number, source, turnIndex }
    );
  };

  const renderAssistantContent = (turn, turnIndex) => {
    const parts = splitCitationContent(turn.content || '');
    const resolved = new Set(turn.resolvedCitations || []);
    const unresolved = new Set(turn.unresolvedCitations || []);

    return (
      <>
        <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
          {parts.map((part, i) => {
            if (part.type !== 'citation') return part.value;
            const isUnresolved = unresolved.has(part.number);
            return (
              <button
                key={`cite-${turnIndex}-${i}`}
                type="button"
                onClick={() => handleCitationClick(part.number, turnIndex)}
                data-testid={
                  isUnresolved
                    ? `unresolved-citation-${part.number}`
                    : `chat-citation-${part.number}`
                }
                aria-label={
                  isUnresolved
                    ? `Citation [${part.number}] has no source record`
                    : `Open citation ${part.number}`
                }
                className={cn(
                  'inline-flex items-center mx-0.5 px-1.5 py-0 rounded-sm border font-mono text-[10px] align-super transition-colors',
                  isUnresolved
                    ? 'border-rose-500/40 bg-rose-500/10 text-rose-400 hover:bg-rose-500/20'
                    : 'border-cyan-500/25 bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20'
                )}
              >
                {part.number}
              </button>
            );
          })}
        </p>

        {openCitation && openCitation.turnIndex === turnIndex && (
          <CitationPanel
            source={openCitation.source}
            number={openCitation.number}
            onClose={() => setOpenCitation(null)}
          />
        )}

        {unresolved.size > 0 && (
          <p
            data-testid="unresolved-citations-warning"
            className="mt-2 flex items-center gap-2 text-xs text-rose-400"
          >
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
            {[...unresolved].sort((a, b) => a - b).map((n) => `[${n}]`).join(' ')}{' '}
            {unresolved.size === 1 ? 'has' : 'have'} no source record in this
            report — treat the claim as unverified.
          </p>
        )}
      </>
    );
  };

  return (
    <div
      data-testid="chat-panel"
      className="rounded-sm border border-zinc-800 bg-zinc-950/40 flex flex-col"
    >
      <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800">
        <MessageSquare className="w-4 h-4 text-cyan-500" />
        <h3 className="text-sm font-display font-semibold text-zinc-200">
          Ask this report
        </h3>
      </div>

      {chatSessionExpired && (
        <div
          data-testid="chat-session-expired"
          className="mx-4 mt-3 flex items-center gap-2 rounded-sm border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-400"
        >
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
          This session has expired. Reports are stored for 2 hours — start a new
          report to chat again.
        </div>
      )}

      {chatError && !chatSessionExpired && (
        <div className="mx-4 mt-3 flex items-center justify-between gap-2 rounded-sm border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-400">
          <span data-testid="chat-error">{chatError}</span>
          <button
            type="button"
            onClick={clearChatError}
            aria-label="Dismiss chat error"
            className="text-rose-400/70 hover:text-rose-300"
          >
            ✕
          </button>
        </div>
      )}

      <div
        ref={scrollRef}
        className="max-h-96 overflow-y-auto px-4 py-4 space-y-3"
      >
        {chatMessages.length === 0 && !chatLoading && (
          <p data-testid="chat-empty" className="text-sm text-zinc-600 italic">
            Ask a question about the report — answers come only from its
            sections and sources.
          </p>
        )}

        {chatMessages.map((turn, i) => (
          <div
            key={`turn-${i}`}
            data-testid={`chat-message-${turn.role}`}
            className={cn(
              'rounded-sm px-3 py-2',
              turn.role === 'user'
                ? 'bg-zinc-900/80 border border-zinc-800'
                : 'bg-cyan-500/5 border border-cyan-500/15'
            )}
          >
            <span className="block text-[10px] font-mono uppercase tracking-wider text-zinc-500 mb-1">
              {turn.role === 'user' ? 'You' : 'Assistant'}
            </span>
            {turn.role === 'user' ? (
              <p className="text-sm text-zinc-300">{turn.content}</p>
            ) : (
              renderAssistantContent(turn, i)
            )}
          </div>
        ))}

        {chatLoading && (
          <div
            data-testid="chat-loading"
            className="flex items-center gap-2 text-xs text-zinc-500"
          >
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            Reading the report...
          </div>
        )}
      </div>

      <div className="flex items-end gap-2 p-3 border-t border-zinc-800">
        <textarea
          data-testid="chat-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder={
            chatSessionExpired ? 'Session expired' : 'Ask about the report...'
          }
          disabled={chatLoading || chatSessionExpired}
          rows={1}
          className="flex-1 resize-none rounded-sm border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-cyan-500/50 disabled:opacity-50"
        />
        <Button
          data-testid="chat-send-btn"
          onClick={handleSend}
          disabled={chatLoading || chatSessionExpired || !draft.trim()}
          className="bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/20 hover:border-cyan-500/50"
        >
          {chatLoading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Send className="w-4 h-4" />
          )}
        </Button>
      </div>
    </div>
  );
}

export default ChatPanel;
