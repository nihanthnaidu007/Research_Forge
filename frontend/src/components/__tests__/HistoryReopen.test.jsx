/**
 * Report reopen tests (R1). Clicking a history row restores the persisted
 * session as the live view — sections, verdicts, citations, transcript —
 * and the restored view is read-only: the session token was returned once
 * at run time, so token-gated controls (exports, chat) hide behind an
 * honest notice instead of letting the user click into a 401.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import HistoryPanel from '../HistoryPanel';
import MainPanel from '../MainPanel';
import { useStore } from '../../store';

const SESSION_RESPONSE = {
  id: 'sid-42',
  topic: 'Solid-state batteries',
  depth: 'quick',
  status: 'complete',
  trace_url: null,
  versioning_report: null,
  state: {
    stream_updates: ['Research complete'],
    written_sections: [
      {
        section_id: 'sec_1',
        title: 'Background',
        content: 'Background prose [1].',
        word_count: 12,
        sources_used: ['https://example.com/a'],
      },
    ],
    sources: [
      {
        url: 'https://example.com/a',
        title: 'Example source',
        domain: 'example.com',
        citation_number: 1,
        integrity_status: 'verified',
      },
    ],
    fact_check_results: [
      {
        claim: 'Batteries improved 10x.',
        verdict: 'SUPPORTED',
        confidence: 0.9,
        reasoning: 'Sources agree.',
        supporting_urls: ['https://example.com/a'],
      },
    ],
    coverage_gaps: ['Costs beyond 2030'],
    confidence_scores: { sec_1: 0.9 },
    overall_confidence: 0.88,
    approved_outline: [
      { section_id: 'sec_1', title: 'Background', description: '', order: 1 },
    ],
    chat_messages: [
      { role: 'user', content: 'Summarize.', ts: '2026-09-18T00:00:00Z' },
    ],
  },
};

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function resetStore(overrides = {}) {
  useStore.setState({
    // The panel refetches history on mount, so the /api/history mock (not
    // the store) is what keeps rows mounted in these tests.
    history: [],
    historyLoading: false,
    historyError: null,
    reopenLoading: false,
    reopenError: null,
    sessionRestored: false,
    // Complete-view defaults the panel renders against.
    status: 'idle',
    writtenSections: [],
    sources: [],
    factCheckResults: [],
    ...overrides,
  });
}

const HISTORY_ENTRY = {
  id: 'sid-42',
  topic: 'Solid-state batteries',
  depth: 'quick',
  status: 'complete',
  created_at: '2026-09-18T00:00:00+00:00',
  updated_at: '2026-09-18T00:00:00+00:00',
};

beforeEach(() => {
  vi.unstubAllGlobals();
  resetStore();
});

describe('history reopen restore', () => {
  it('restores a past report on row click with sections, verdicts, and citations intact', async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('/api/history')) {
        return Promise.resolve(jsonResponse(200, { sessions: [HISTORY_ENTRY] }));
      }
      if (String(url).includes('/api/session/sid-42')) {
        return Promise.resolve(jsonResponse(200, SESSION_RESPONSE));
      }
      return Promise.resolve(jsonResponse(404, { detail: 'Not found' }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<HistoryPanel />);

    await user.click(await screen.findByTestId('history-row'));

    await waitFor(() => {
      expect(useStore.getState().sessionRestored).toBe(true);
    });

    const state = useStore.getState();
    expect(state.status).toBe('complete');
    expect(state.sessionId).toBe('sid-42');
    // The one-time token is never restorable — restored views are read-only.
    expect(state.sessionToken).toBeNull();
    expect(state.writtenSections).toHaveLength(1);
    expect(state.writtenSections[0].title).toBe('Background');
    expect(state.factCheckResults).toHaveLength(1);
    expect(state.factCheckResults[0].verdict).toBe('SUPPORTED');
    expect(state.sources).toHaveLength(1);
    expect(state.sources[0].integrity_status).toBe('verified');
    expect(state.coverageGaps).toEqual(['Costs beyond 2030']);
    expect(state.chatMessages).toHaveLength(1);
  });

  it('answers a non-completed session with an honest message, not a broken view', async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('/api/history')) {
        return Promise.resolve(jsonResponse(200, { sessions: [HISTORY_ENTRY] }));
      }
      if (String(url).includes('/api/session/sid-42')) {
        return Promise.resolve(jsonResponse(200, { ...SESSION_RESPONSE, status: 'running' }));
      }
      return Promise.resolve(jsonResponse(404, { detail: 'Not found' }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<HistoryPanel />);

    await user.click(await screen.findByTestId('history-row'));

    await waitFor(() => {
      expect(screen.getByTestId('reopen-error')).toHaveTextContent(
        /Only completed reports can be reopened/i
      );
    });
    expect(useStore.getState().sessionRestored).toBe(false);
    expect(useStore.getState().status).not.toBe('complete');
  });

  it('reports an expired session instead of an opaque failure', async () => {
    const fetchMock = vi.fn((url) => {
      if (String(url).includes('/api/history')) {
        return Promise.resolve(jsonResponse(200, { sessions: [HISTORY_ENTRY] }));
      }
      return Promise.resolve(jsonResponse(404, { detail: 'Session not found' }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<HistoryPanel />);

    await user.click(await screen.findByTestId('history-row'));

    await waitFor(() => {
      expect(screen.getByTestId('reopen-error')).toHaveTextContent(
        /no longer available/i
      );
    });
  });
});

describe('restored report view', () => {
  it('renders the report read-only: notice shown, exports and chat hidden', () => {
    render(
      <MainPanel
        sessionId="sid-42"
        status="complete"
        topic="Solid-state batteries"
        streamUpdates={[]}
        outline={[]}
        outlineApproved
        onUpdateSection={() => {}}
        onApprove={() => {}}
        isLoading={false}
        writtenSections={SESSION_RESPONSE.state.written_sections}
        confidenceScores={{ sec_1: 0.9 }}
        sources={SESSION_RESPONSE.state.sources}
        overallConfidence={0.88}
        error={null}
        onReset={() => {}}
        versioningReport={null}
        outlineEdits=""
        onEditsChange={() => {}}
        onSaveOutline={() => {}}
        onExport={() => {}}
        onSteer={() => {}}
        steerInFlight={null}
        steerAck={null}
        steerError={null}
        researchRounds={0}
        sessionRestored
      />
    );

    expect(screen.getByTestId('restored-notice')).toBeInTheDocument();
    expect(screen.queryByTestId('download-pdf-btn')).not.toBeInTheDocument();
    expect(screen.queryByTestId('download-bibtex-btn')).not.toBeInTheDocument();
    // The report itself renders fully.
    expect(screen.getByText('Background')).toBeInTheDocument();
  });

  it('keeps exports and chat available in the report’s own session', () => {
    render(
      <MainPanel
        sessionId="sid-live"
        status="complete"
        topic="Live report"
        streamUpdates={[]}
        outline={[]}
        outlineApproved
        onUpdateSection={() => {}}
        onApprove={() => {}}
        isLoading={false}
        writtenSections={SESSION_RESPONSE.state.written_sections}
        confidenceScores={{ sec_1: 0.9 }}
        sources={SESSION_RESPONSE.state.sources}
        overallConfidence={0.88}
        error={null}
        onReset={() => {}}
        versioningReport={null}
        outlineEdits=""
        onEditsChange={() => {}}
        onSaveOutline={() => {}}
        onExport={() => {}}
        onSteer={() => {}}
        steerInFlight={null}
        steerAck={null}
        steerError={null}
        researchRounds={0}
        sessionRestored={false}
      />
    );

    expect(screen.queryByTestId('restored-notice')).not.toBeInTheDocument();
    expect(screen.getByTestId('download-pdf-btn')).toBeInTheDocument();
  });
});
