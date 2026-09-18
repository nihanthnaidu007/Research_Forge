/**
 * HistoryPanel topic-search tests (W5). The panel queries the server with
 * ?q= (debounced) and lifts the 8-row display cap while searching — hiding
 * server-filtered matches behind the client-side slice would misreport
 * what the search found.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import HistoryPanel from '../HistoryPanel';
import { useStore } from '../../store';

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function sessions(n) {
  return Array.from({ length: n }, (_, i) => ({
    id: `sid-${i}`,
    topic: `Quantum study ${i}`,
    depth: 'quick',
    status: 'complete',
    created_at: '2026-09-17T00:00:00+00:00',
    updated_at: '2026-09-17T00:00:00+00:00',
  }));
}

function resetStore(overrides = {}) {
  useStore.setState({
    history: [],
    historyLoading: false,
    historyError: null,
    ...overrides,
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  resetStore();
});

describe('HistoryPanel topic search', () => {
  it('queries the server with the typed topic after the debounce', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { sessions: [] }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<HistoryPanel />);

    await user.type(screen.getByTestId('history-search-input'), 'quantum');

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => c[0]);
      expect(urls.some((u) => u.includes('/api/history?q=quantum'))).toBe(true);
    });
  });

  it('lifts the 8-row display cap while searching', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { sessions: sessions(10) })
    );
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<HistoryPanel />);

    // No query: capped at 8 rows with an overflow note (after initial load).
    await waitFor(() => {
      expect(screen.getAllByTestId('history-row')).toHaveLength(8);
    });
    expect(screen.getByText('+2 older reports')).toBeInTheDocument();

    await user.type(screen.getByTestId('history-search-input'), 'quantum');

    // Searching: all 10 server-filtered matches render, no cap.
    await waitFor(() => {
      expect(screen.getAllByTestId('history-row')).toHaveLength(10);
    });
    expect(screen.queryByText('+2 older reports')).not.toBeInTheDocument();
  });

  it('shows the no-matching-reports empty state for a fruitless query', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { sessions: [] }));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<HistoryPanel />);
    await waitFor(() => {
      expect(screen.getByText('No reports yet.')).toBeInTheDocument();
    });

    await user.type(screen.getByTestId('history-search-input'), 'zzz');
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => c[0].includes('?q=zzz'))
      ).toBe(true);
    });
    expect(screen.getByText('No matching reports.')).toBeInTheDocument();
  });

  it('refreshes with the active query, not the unfiltered listing', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { sessions: [] }));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<HistoryPanel />);
    await user.type(screen.getByTestId('history-search-input'), 'fusion');
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((c) => c[0].includes('?q=fusion'))
      ).toBe(true);
    });

    fetchMock.mockClear();
    await user.click(screen.getByTestId('history-refresh-btn'));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    expect(
      fetchMock.mock.calls.every((c) => c[0].includes('?q=fusion'))
    ).toBe(true);
  });
});
