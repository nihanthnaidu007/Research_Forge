/**
 * Sidebar report-template picker tests (W5). The picker mirrors the depth
 * Tabs pattern; the presets shape outline structure only, and the picker
 * copy must say so (spec: the two couplings must not drift per preset).
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import Sidebar from '../Sidebar';

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function renderSidebar(overrides = {}) {
  const props = {
    topic: 'Quantum computing advances',
    setTopic: vi.fn(),
    depth: 'quick',
    setDepth: vi.fn(),
    reportTemplate: 'standard',
    setReportTemplate: vi.fn(),
    inputUrls: [],
    addUrl: vi.fn(),
    removeUrl: vi.fn(),
    uploadedFiles: [],
    addFile: vi.fn(),
    removeFile: vi.fn(),
    onStartReport: vi.fn(),
    isLoading: false,
    status: 'idle',
    currentAgent: '',
    completedAgents: [],
    agentStats: { sourcesFound: 0, claimsChecked: 0 },
    overallConfidence: 0,
    hasDocuments: false,
    ...overrides,
  };
  render(<Sidebar {...props} />);
  return props;
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(jsonResponse(200, { sessions: [] }))
  );
});

describe('Sidebar template picker', () => {
  it('renders all five preset triggers', () => {
    renderSidebar();
    for (const key of [
      'standard',
      'exec-brief',
      'journal-club',
      'deep-dive',
      'systematic-lite',
    ]) {
      expect(screen.getByTestId(`template-${key}`)).toBeInTheDocument();
    }
  });

  it('selecting a preset reports it through the store setter', async () => {
    const user = userEvent.setup();
    const props = renderSidebar();

    await user.click(screen.getByTestId('template-exec-brief'));
    expect(props.setReportTemplate).toHaveBeenCalledWith('exec-brief');

    await user.click(screen.getByTestId('template-systematic-lite'));
    expect(props.setReportTemplate).toHaveBeenCalledWith('systematic-lite');
  });

  it('shows the selected preset description and the structure-only note', () => {
    renderSidebar({ reportTemplate: 'deep-dive' });

    const description = screen.getByTestId('template-description');
    expect(description.textContent).toContain('Mechanism-oriented');
    // The coupling-honesty copy is always present.
    expect(description.textContent).toContain('structure only');
  });

  it('disables the picker while a run is in progress', () => {
    renderSidebar({ status: 'running' });
    expect(screen.getByTestId('template-exec-brief')).toBeDisabled();
    expect(screen.getByTestId('template-standard')).toBeDisabled();
  });
});
