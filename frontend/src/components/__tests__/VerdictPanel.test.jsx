/**
 * Fact-check verdict panel tests (R2). The backend emits exactly three
 * verdict values; the panel presents them with tallies and honest
 * degradation for anything it does not recognize.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import VerdictPanel, { tallyVerdicts } from '../VerdictPanel';

const RESULTS = [
  {
    claim: 'Solid-state cells reach 500 Wh/kg in labs.',
    verdict: 'SUPPORTED',
    confidence: 0.92,
    reasoning: 'Two peer-reviewed sources agree.',
    supporting_urls: ['https://example.com/a'],
  },
  {
    claim: 'Commercial flights run on solid-state batteries.',
    verdict: 'UNSUPPORTED',
    confidence: 0.1,
    reasoning: 'No source supports this.',
    supporting_urls: [],
  },
  {
    claim: 'Costs fall below lithium-ion by 2030.',
    verdict: 'PARTIALLY_SUPPORTED',
    confidence: 0.55,
    reasoning: 'One source projects it; none confirm.',
    supporting_urls: ['https://example.com/b', 'https://example.com/c'],
  },
];

describe('VerdictPanel', () => {
  it('renders every persisted claim with its verdict badge and tally', () => {
    render(<VerdictPanel results={RESULTS} />);

    expect(screen.getByTestId('verdict-panel')).toBeInTheDocument();

    const rows = screen.getAllByTestId('verdict-row');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute('data-verdict', 'SUPPORTED');
    expect(rows[1]).toHaveAttribute('data-verdict', 'UNSUPPORTED');
    expect(rows[2]).toHaveAttribute('data-verdict', 'PARTIALLY_SUPPORTED');

    // Claim text and reasoning render so the verdict is reviewable.
    expect(screen.getByText(/Solid-state cells reach 500 Wh\/kg/)).toBeInTheDocument();
    expect(screen.getByText(/Two peer-reviewed sources agree/)).toBeInTheDocument();

    const tally = screen.getByTestId('verdict-tally');
    expect(tally).toHaveTextContent('1 supported');
    expect(tally).toHaveTextContent('1 partial');
    expect(tally).toHaveTextContent('1 unsupported');
  });

  it('renders confidence percentages and supporting source links', () => {
    render(<VerdictPanel results={RESULTS} />);

    const confidenceSpans = screen.getAllByTestId('verdict-confidence');
    expect(confidenceSpans).toHaveLength(3);

    const sourceGroups = screen.getAllByTestId('verdict-sources');
    expect(sourceGroups).toHaveLength(2); // rows 1 and 3 only

    const links = sourceGroups[1].querySelectorAll('a');
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute('href', 'https://example.com/b');
  });

  it('degrades honestly: unknown verdicts render UNKNOWN, missing data never fabricates', () => {
    render(
      <VerdictPanel
        results={[
          { claim: 'Claim with unrecorded confidence.', verdict: 'MYSTERY_VERDICT', supporting_urls: [null, ''] },
        ]}
      />
    );

    const row = screen.getByTestId('verdict-row');
    expect(row).toHaveAttribute('data-verdict', 'UNKNOWN');
    expect(screen.getByText('Confidence not recorded')).toBeInTheDocument();
    // Empty/blank supporting URLs are dropped, not rendered as broken links.
    expect(screen.queryByTestId('verdict-sources')).not.toBeInTheDocument();
  });

  it('shows an honest empty state when no verdicts exist', () => {
    render(<VerdictPanel results={[]} />);

    expect(screen.getByTestId('verdict-panel-empty')).toBeInTheDocument();
    expect(screen.getByText(/No fact-check verdicts were recorded/)).toBeInTheDocument();
  });

  it('treats a missing results prop as empty, not a crash', () => {
    render(<VerdictPanel />);

    expect(screen.getByTestId('verdict-panel-empty')).toBeInTheDocument();
  });
});

describe('tallyVerdicts', () => {
  it('counts known verdicts and routes unrecognized ones to UNKNOWN', () => {
    const tally = tallyVerdicts([
      { verdict: 'SUPPORTED' },
      { verdict: 'SUPPORTED' },
      { verdict: 'PARTIALLY_SUPPORTED' },
      { verdict: 'UNSUPPORTED' },
      { verdict: 'WEIRD' },
      {},
      null,
    ]);
    expect(tally).toEqual({
      SUPPORTED: 2,
      PARTIALLY_SUPPORTED: 1,
      UNSUPPORTED: 1,
      UNKNOWN: 3,
    });
  });

  it('handles missing input', () => {
    expect(tallyVerdicts(null)).toEqual({
      SUPPORTED: 0,
      PARTIALLY_SUPPORTED: 0,
      UNSUPPORTED: 0,
      UNKNOWN: 0,
    });
  });
});
