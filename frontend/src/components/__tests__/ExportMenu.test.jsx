/**
 * CSL export menu tests (R5). The completed report's export row gains
 * APA/MLA/IEEE bibliography entries; the store maps every csl-* format
 * onto the single /api/export-csl endpoint with a style query param.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MainPanel from '../MainPanel';
import { useStore } from '../../store';

function resetStore(overrides = {}) {
  useStore.setState({
    sessionId: 'sid-live',
    sessionToken: 'tok',
    status: 'idle',
    writtenSections: [],
    sources: [],
    factCheckResults: [],
    ...overrides,
  });
}

function renderCompletedPanel(onExport = vi.fn()) {
  render(
    <MainPanel
      sessionId="sid-live"
      status="complete"
      topic="Solid-state batteries"
      streamUpdates={[]}
      outline={[]}
      outlineApproved
      onUpdateSection={() => {}}
      onApprove={() => {}}
      isLoading={false}
      writtenSections={[
        {
          section_id: 'sec_1',
          title: 'Background',
          content: 'Background prose [1].',
          word_count: 12,
          sources_used: [],
        },
      ]}
      confidenceScores={{ sec_1: 0.9 }}
      sources={[]}
      overallConfidence={0.88}
      error={null}
      onReset={() => {}}
      versioningReport={null}
      outlineEdits=""
      onEditsChange={() => {}}
      onSaveOutline={() => {}}
      onExport={onExport}
      onSteer={() => {}}
      steerInFlight={null}
      steerAck={null}
      steerError={null}
      researchRounds={0}
      sessionRestored={false}
    />
  );
  return onExport;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  // jsdom has no object-URL store; the download helper needs both ends.
  window.URL.createObjectURL = vi.fn(() => 'blob:test-url');
  window.URL.revokeObjectURL = vi.fn();
  resetStore();
});

describe('CSL export menu', () => {
  it('renders APA, MLA, and IEEE bibliography buttons in a live session', () => {
    renderCompletedPanel();

    expect(screen.getByTestId('download-csl-apa-btn')).toHaveTextContent(
      'Download Bibliography (APA)'
    );
    expect(screen.getByTestId('download-csl-mla-btn')).toBeInTheDocument();
    expect(screen.getByTestId('download-csl-ieee-btn')).toBeInTheDocument();
  });

  it('routes each button to its csl format id', async () => {
    const onExport = renderCompletedPanel();
    const user = userEvent.setup();

    // Each export disables the whole row until it settles, so the clicks
    // must be awaited sequentially.
    await user.click(screen.getByTestId('download-csl-apa-btn'));
    await user.click(screen.getByTestId('download-csl-mla-btn'));
    await user.click(screen.getByTestId('download-csl-ieee-btn'));

    expect(onExport).toHaveBeenCalledWith('csl-apa');
    expect(onExport).toHaveBeenCalledWith('csl-mla');
    expect(onExport).toHaveBeenCalledWith('csl-ieee');
  });
});

describe('exportReport CSL endpoint mapping', () => {
  it('maps csl-apa onto /api/export-csl with the style query param', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers({
          'Content-Disposition': 'attachment; filename="report.apa.txt"',
        }),
        blob: async () => new Blob(['bibliography']),
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await useStore.getState().exportReport('csl-apa');

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/export-csl');
    expect(String(url)).toContain('session_id=sid-live');
    expect(String(url)).toContain('style=apa');
    expect(init.method).toBe('POST');
  });

  it('keeps non-CSL formats on their dedicated endpoints', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers({
          'Content-Disposition': 'attachment; filename="report.pdf"',
        }),
        blob: async () => new Blob(['pdf']),
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await useStore.getState().exportReport('pdf');

    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/export-pdf');
    expect(String(url)).not.toContain('style=');
  });
});
