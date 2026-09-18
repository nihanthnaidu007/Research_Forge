/**
 * Coverage-gap transparency tests (R3). coverage_gaps is a persisted list
 * of human-readable strings from the supervisor's deterministic coverage
 * check; the panel presents present/absent honestly.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import CoverageGapsPanel from '../CoverageGaps';

describe('CoverageGapsPanel', () => {
  it('renders each persisted gap verbatim with a count', () => {
    render(
      <CoverageGapsPanel
        gaps={[
          "Low confidence (0.42) in section 'Costs' — needs stronger sourcing",
          '3 of 12 claims unsupported — sources contradict the report',
        ]}
      />
    );

    expect(screen.getByTestId('coverage-panel')).toBeInTheDocument();
    expect(screen.getByText('Coverage Gaps (2)')).toBeInTheDocument();

    const gaps = screen.getAllByTestId('coverage-gap');
    expect(gaps).toHaveLength(2);
    expect(gaps[0]).toHaveTextContent(/Low confidence \(0\.42\) in section 'Costs'/);
    expect(gaps[1]).toHaveTextContent(/3 of 12 claims unsupported/);
  });

  it('shows the clear state when the check flagged nothing', () => {
    render(<CoverageGapsPanel gaps={[]} />);

    expect(screen.getByTestId('coverage-panel-clear')).toBeInTheDocument();
    expect(screen.getByText(/No coverage gaps were flagged/)).toBeInTheDocument();
    expect(screen.queryByTestId('coverage-panel')).not.toBeInTheDocument();
  });

  it('treats a missing gaps prop as checked-and-clear, not a crash', () => {
    render(<CoverageGapsPanel />);

    expect(screen.getByTestId('coverage-panel-clear')).toBeInTheDocument();
  });

  it('drops blank entries instead of rendering empty bullets', () => {
    render(<CoverageGapsPanel gaps={['Real gap', '', null]} />);

    expect(screen.getAllByTestId('coverage-gap')).toHaveLength(1);
    expect(screen.getByText('Coverage Gaps (1)')).toBeInTheDocument();
  });
});
