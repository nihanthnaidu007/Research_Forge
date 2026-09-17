/**
 * ChatPanel tests (W3) — the chat-with-report UI over the shared Zustand
 * store. fetch is stubbed, so no backend is involved; the store drives
 * rendering exactly as in the app (transcript lives in the store per
 * pre-flight findings §0.2, the draft box is component-local).
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ChatPanel from '../ChatPanel';
import { useStore } from '../../store';

const SOURCES = [
  {
    url: 'https://arxiv.org/abs/1706.03762',
    title: 'Attention Is All You Need',
    domain: 'arxiv.org',
    citation_number: 1,
    snippet: 'The dominant sequence transduction models are based on RNNs.',
    integrity_status: 'verified',
    retracted: false,
    citation_count: 120000,
  },
];

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function resetStore(overrides = {}) {
  useStore.setState({
    sessionId: 'sess-1',
    sessionToken: 'tok',
    chatMessages: [],
    chatLoading: false,
    chatError: null,
    chatSessionExpired: false,
    ...overrides,
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  resetStore();
});

describe('ChatPanel', () => {
  it('renders the empty state before any turn', () => {
    render(<ChatPanel sources={SOURCES} />);
    expect(screen.getByTestId('chat-empty')).toBeInTheDocument();
  });

  it('sends the draft and shows both turns with the grounded answer', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        answer: 'The report says transformers scaled data [1].',
        resolved_citations: [1],
        unresolved_citations: [],
        usage: { total_tokens: 500 },
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<ChatPanel sources={SOURCES} />);

    await user.type(screen.getByTestId('chat-input'), 'What scaled data?');
    await user.click(screen.getByTestId('chat-send-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('chat-message-user')).toBeInTheDocument();
      expect(screen.getByTestId('chat-message-assistant')).toBeInTheDocument();
    });
    expect(
      screen.getByTestId('chat-message-assistant').textContent
    ).toContain('transformers scaled data');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/session/sess-1/chat');
    expect(JSON.parse(init.body)).toEqual({ message: 'What scaled data?' });
    expect(init.headers['X-Session-Token']).toBe('tok');
    // The draft box clears after sending.
    expect(screen.getByTestId('chat-input').value).toBe('');
  });

  it('opens the citation panel for a resolved citation chip', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        answer: 'Answer with a marker [1].',
        resolved_citations: [1],
        unresolved_citations: [],
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<ChatPanel sources={SOURCES} />);
    await user.type(screen.getByTestId('chat-input'), 'q');
    await user.click(screen.getByTestId('chat-send-btn'));

    const chip = await screen.findByTestId('chat-citation-1');
    await user.click(chip);

    expect(screen.getByTestId('citation-panel')).toBeInTheDocument();
    expect(screen.getByTestId('citation-panel').textContent).toContain(
      'Attention Is All You Need'
    );
  });

  it('flags unresolved citations with a visible no-source-record warning', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        answer: 'Claim one [1]; claim two [7].',
        resolved_citations: [1],
        unresolved_citations: [7],
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<ChatPanel sources={SOURCES} />);
    await user.type(screen.getByTestId('chat-input'), 'q');
    await user.click(screen.getByTestId('chat-send-btn'));

    expect(
      await screen.findByTestId('unresolved-citation-7')
    ).toBeInTheDocument();
    expect(screen.getByTestId('unresolved-citations-warning')).toBeInTheDocument();
    expect(
      screen.getByTestId('unresolved-citations-warning').textContent
    ).toContain('no source record');
    // The resolved chip is NOT flagged.
    expect(screen.getByTestId('chat-citation-1')).toBeInTheDocument();
  });

  it('surfaces a 404 after TTL as an explicit session-expired banner', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(404, { detail: 'Session not found' }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<ChatPanel sources={SOURCES} />);
    await user.type(screen.getByTestId('chat-input'), 'q');
    await user.click(screen.getByTestId('chat-send-btn'));

    expect(await screen.findByTestId('chat-session-expired')).toBeInTheDocument();
    // The user turn stays visible but no assistant turn is fabricated.
    expect(screen.getByTestId('chat-message-user')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-message-assistant')).toBeNull();
  });

  it('shows sanitized budget/error messages from non-404 failures', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(429, {
        detail: {
          message: 'This chat turn exceeded the token budget. Try again shortly.',
          retry_after_seconds: 60,
        },
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<ChatPanel sources={SOURCES} />);
    await user.type(screen.getByTestId('chat-input'), 'q');
    await user.click(screen.getByTestId('chat-send-btn'));

    expect(await screen.findByTestId('chat-error')).toBeInTheDocument();
    expect(screen.getByTestId('chat-error').textContent).toContain('budget');
  });

  it('disables the input and send button while a turn is in flight', async () => {
    let resolveTurn;
    vi.stubGlobal(
      'fetch',
      vi.fn().mockReturnValue(new Promise((resolve) => (resolveTurn = resolve)))
    );

    const user = userEvent.setup();
    render(<ChatPanel sources={SOURCES} />);
    await user.type(screen.getByTestId('chat-input'), 'q');
    await user.click(screen.getByTestId('chat-send-btn'));

    expect(await screen.findByTestId('chat-loading')).toBeInTheDocument();
    expect(screen.getByTestId('chat-input')).toBeDisabled();
    expect(screen.getByTestId('chat-send-btn')).toBeDisabled();

    // Unblock the turn so the store settles before the next test.
    resolveTurn(
      jsonResponse(200, {
        answer: 'a [1]',
        resolved_citations: [1],
        unresolved_citations: [],
      })
    );
    await waitFor(() =>
      expect(screen.queryByTestId('chat-loading')).toBeNull()
    );
  });

  it('does not send an empty draft', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<ChatPanel sources={SOURCES} />);
    await user.click(screen.getByTestId('chat-send-btn'));

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
