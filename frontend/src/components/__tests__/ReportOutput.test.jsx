/**
 * ReportOutput citation panel tests (W2) — the repo's first frontend tests.
 *
 * Panel data mirrors the backend's enriched Source shape: integrity_status,
 * retracted, and citation_count ride through state.sources verbatim (zero
 * API changes); the UI renders evidence, never guesses.
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import ReportOutput, { splitCitationContent } from '../ReportOutput';

const SECTIONS = [
  {
    section_id: 'sec_1',
    title: 'Background',
    content: 'Transformers changed NLP [1]. Later work scaled them [1][2].',
    word_count: 12,
  },
];

function makeSource(overrides = {}) {
  return {
    url: 'https://arxiv.org/abs/1706.03762',
    title: 'Attention Is All You Need',
    domain: 'arxiv.org',
    citation_number: 1,
    snippet: 'The dominant sequence transduction models are based on RNNs.',
    integrity_status: 'verified',
    retracted: false,
    citation_count: 120000,
    ...overrides,
  };
}

function renderReport(sources) {
  return render(
    <ReportOutput
      writtenSections={SECTIONS}
      confidenceScores={{ sec_1: 0.9 }}
      sources={sources}
      overallConfidence={0.9}
    />
  );
}

describe('splitCitationContent', () => {
  it('splits content into text and citation parts', () => {
    expect(splitCitationContent('a [1] b')).toEqual([
      { type: 'text', value: 'a ' },
      { type: 'citation', number: 1 },
      { type: 'text', value: ' b' },
    ]);
  });

  it('handles adjacent citations and citations at both ends', () => {
    expect(splitCitationContent('[2][3] x')).toEqual([
      { type: 'citation', number: 2 },
      { type: 'citation', number: 3 },
      { type: 'text', value: ' x' },
    ]);
    expect(splitCitationContent('x [4]')).toEqual([
      { type: 'text', value: 'x ' },
      { type: 'citation', number: 4 },
    ]);
  });

  it('returns one text part when no citation markers exist', () => {
    expect(splitCitationContent('plain text')).toEqual([
      { type: 'text', value: 'plain text' },
    ]);
  });

  it('does not split multi-digit numbers into separate markers', () => {
    expect(splitCitationContent('a [12]')).toEqual([
      { type: 'text', value: 'a ' },
      { type: 'citation', number: 12 },
    ]);
  });
});

describe('ReportOutput citation panel', () => {
  it('renders inline [n] markers as clickable citation buttons', () => {
    renderReport([makeSource(), makeSource({ citation_number: 2, integrity_status: 'unknown', citation_count: null })]);

    expect(screen.getAllByRole('button', { name: 'Show citation 1' })).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Show citation 2' })).toBeInTheDocument();
    // The panel is closed until a citation is clicked.
    expect(screen.queryByTestId('citation-panel')).not.toBeInTheDocument();
  });

  it('opens the panel with title, domain, snippet, and integrity evidence', async () => {
    const user = userEvent.setup();
    renderReport([makeSource()]);

    await user.click(screen.getAllByRole('button', { name: 'Show citation 1' })[0]);

    // Panel content duplicates the references list below — scope to the panel.
    const panel = screen.getByTestId('citation-panel');
    expect(within(panel).getByText('Attention Is All You Need')).toHaveAttribute(
      'href',
      'https://arxiv.org/abs/1706.03762'
    );
    expect(within(panel).getByText('arxiv.org')).toBeInTheDocument();
    expect(within(panel).getByTestId('citation-snippet')).toHaveTextContent(
      'The dominant sequence transduction models are based on RNNs.'
    );
    expect(within(panel).getByTestId('integrity-chip-verified')).toHaveTextContent('VERIFIED');
    expect(within(panel).getByTestId('citation-count')).toHaveTextContent('120,000 citations');
  });

  it('flags retracted sources with a chip and a warning', async () => {
    const user = userEvent.setup();
    renderReport([makeSource({ integrity_status: 'retracted', retracted: true })]);

    await user.click(screen.getAllByRole('button', { name: 'Show citation 1' })[0]);

    const panel = screen.getByTestId('citation-panel');
    expect(within(panel).getByTestId('integrity-chip-retracted')).toHaveTextContent('RETRACTED');
    expect(within(panel).getByTestId('retraction-warning')).toHaveTextContent(
      'This source has been retracted'
    );
  });

  it('renders an UNKNOWN chip for old-shape sources without integrity fields', async () => {
    const user = userEvent.setup();
    // Pre-W2 source record: no integrity keys at all (checkpoint compat).
    renderReport([
      {
        url: 'https://example.com',
        title: 'A Web Page',
        domain: 'example.com',
        citation_number: 1,
        snippet: '',
      },
    ]);

    await user.click(screen.getAllByRole('button', { name: 'Show citation 1' })[0]);

    const panel = screen.getByTestId('citation-panel');
    expect(within(panel).getByTestId('integrity-chip-unknown')).toHaveTextContent('UNKNOWN');
    expect(within(panel).queryByTestId('retraction-warning')).not.toBeInTheDocument();
  });

  it('states when no snippet was recorded instead of showing a blank', async () => {
    const user = userEvent.setup();
    renderReport([makeSource({ snippet: '' })]);

    await user.click(screen.getAllByRole('button', { name: 'Show citation 1' })[0]);

    expect(screen.getByTestId('citation-snippet-empty')).toHaveTextContent(
      'No snippet recorded for this source.'
    );
  });

  it('closes the panel from its close button', async () => {
    const user = userEvent.setup();
    renderReport([makeSource()]);

    await user.click(screen.getAllByRole('button', { name: 'Show citation 1' })[0]);
    await user.click(screen.getByRole('button', { name: 'Close citation panel' }));

    expect(screen.queryByTestId('citation-panel')).not.toBeInTheDocument();
  });

  it('switches panel content when another citation is clicked', async () => {
    const user = userEvent.setup();
    renderReport([
      makeSource(),
      makeSource({
        citation_number: 2,
        url: 'https://example.com/other',
        title: 'Another Source',
        domain: 'example.com',
        integrity_status: 'unknown',
        citation_count: null,
        snippet: 'Different snippet.',
      }),
    ]);

    await user.click(screen.getAllByRole('button', { name: 'Show citation 1' })[0]);
    await user.click(screen.getByRole('button', { name: 'Show citation 2' }));

    const panel = screen.getByTestId('citation-panel');
    expect(within(panel).getByText('Another Source')).toBeInTheDocument();
    expect(within(panel).queryByText('Attention Is All You Need')).not.toBeInTheDocument();
    expect(within(panel).getByTestId('integrity-chip-unknown')).toBeInTheDocument();
  });

  it('shows a fallback when a marker has no matching source record', async () => {
    const user = userEvent.setup();
    renderReport([]); // sections cite [1], but no source survived dedup

    await user.click(screen.getAllByRole('button', { name: 'Show citation 1' })[0]);

    expect(screen.getByText('No source record for citation [1].')).toBeInTheDocument();
  });

  it('lists integrity chips in the references section', () => {
    renderReport([
      makeSource(),
      makeSource({ citation_number: 2, integrity_status: 'unresolved', citation_count: null }),
    ]);

    expect(screen.getByTestId('integrity-chip-verified')).toBeInTheDocument();
    expect(screen.getByTestId('integrity-chip-unresolved')).toBeInTheDocument();
  });
});
