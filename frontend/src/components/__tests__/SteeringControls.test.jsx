/**
 * W4 steering tests — mid-run controls in the running branch, the paused
 * view, and the store's sendSteerCommand wiring. fetch is stubbed so no
 * backend is involved; MainPanel is rendered through a harness that pulls
 * steer state from the real store, mirroring App.jsx wiring.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MainPanel from '../MainPanel';
import { useStore } from '../../store';

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
    status: 'running',
    researchRounds: 0,
    coverageGaps: [],
    steerInFlight: null,
    steerAck: null,
    steerError: null,
    ...overrides,
  });
}

function SteerHarness(props) {
  const store = useStore();
  return (
    <MainPanel
      sessionId={store.sessionId}
      status={props.status ?? store.status}
      topic="Quantum computing"
      streamUpdates={['[12:00:01] synthesis: writing Background...']}
      outline={[]}
      outlineApproved
      onUpdateSection={() => {}}
      onApprove={() => {}}
      isLoading={false}
      writtenSections={[]}
      confidenceScores={{}}
      sources={[]}
      overallConfidence={0}
      error={null}
      onReset={() => {}}
      versioningReport={null}
      outlineEdits=""
      onEditsChange={() => {}}
      onSaveOutline={() => {}}
      onExport={() => {}}
      onSteer={store.sendSteerCommand}
      steerInFlight={store.steerInFlight}
      steerAck={store.steerAck}
      steerError={store.steerError}
      researchRounds={props.researchRounds ?? store.researchRounds}
      {...props}
    />
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
  resetStore();
});

describe('steering controls (running branch)', () => {
  it('renders pause and redirect controls after outline approval', () => {
    render(<SteerHarness status="running" />);
    expect(screen.getByTestId('steering-controls')).toBeInTheDocument();
    expect(screen.getByTestId('steer-pause-btn')).toBeInTheDocument();
    expect(screen.getByTestId('steer-focus-input')).toBeInTheDocument();
    expect(screen.getByTestId('steer-redirect-btn')).toBeInTheDocument();
  });

  it('hides steering controls before outline approval', () => {
    render(<SteerHarness status="running" outlineApproved={false} />);
    expect(screen.queryByTestId('steering-controls')).toBeNull();
  });

  it('shows the deep-research round badge while rounds are running', () => {
    render(<SteerHarness status="running" researchRounds={2} />);
    const badge = screen.getByTestId('research-round-badge');
    expect(badge.textContent).toContain('round 2');
  });

  it('sends pause with the session token and shows the poll-cadence ACK', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { status: 'accepted' }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<SteerHarness status="running" />);
    await user.click(screen.getByTestId('steer-pause-btn'));

    await waitFor(() => expect(screen.getByTestId('steer-ack')).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/session/sess-1/steer');
    expect(JSON.parse(init.body)).toEqual({ command: 'pause' });
    expect(init.headers['X-Session-Token']).toBe('tok');
    // Honest latency: the ACK names the poll cadence instead of claiming
    // an instant stop.
    expect(screen.getByTestId('steer-ack').textContent).toContain('2–5s');
  });

  it('sends redirect with trimmed focus text', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { status: 'accepted' }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<SteerHarness status="running" />);
    await user.type(
      screen.getByTestId('steer-focus-input'),
      '  focus on recent benchmarks  '
    );
    await user.click(screen.getByTestId('steer-redirect-btn'));

    await waitFor(() =>
      expect(screen.getByTestId('steer-ack')).toBeInTheDocument()
    );
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      command: 'redirect',
      focus: 'focus on recent benchmarks',
    });
  });

  it('disables redirect until focus text is entered', () => {
    render(<SteerHarness status="running" />);
    expect(screen.getByTestId('steer-redirect-btn')).toBeDisabled();
  });

  it('surfaces validation failures from the endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(400, { detail: 'Unsupported command — pause, resume, or redirect.' })
      );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<SteerHarness status="running" />);
    await user.click(screen.getByTestId('steer-pause-btn'));

    expect(await screen.findByTestId('steer-error')).toBeInTheDocument();
    expect(screen.getByTestId('steer-error').textContent).toContain('Unsupported');
    expect(screen.queryByTestId('steer-ack')).toBeNull();
  });

  it('surfaces slot saturation with the retry hint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(429, {
        detail: {
          message: 'The server is at its concurrent-run limit; try resuming shortly.',
          retry_after_seconds: 12,
        },
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<SteerHarness status="paused" />);
    await user.click(screen.getByTestId('steer-resume-btn'));

    expect(await screen.findByTestId('steer-error')).toBeInTheDocument();
    expect(screen.getByTestId('steer-error').textContent).toContain('12s');
  });
});

describe('paused view', () => {
  it('renders the paused card with resume and redirect affordances', () => {
    render(<SteerHarness status="paused" />);
    expect(screen.getByTestId('paused-state')).toBeInTheDocument();
    expect(screen.getByTestId('steer-resume-btn')).toBeInTheDocument();
    expect(screen.getByTestId('steer-focus-input')).toBeInTheDocument();
    expect(screen.getByTestId('steer-redirect-btn')).toBeInTheDocument();
    expect(screen.getByTestId('paused-state').textContent).toContain(
      'Research paused'
    );
  });

  it('shows completed round count while paused', () => {
    render(<SteerHarness status="paused" researchRounds={1} />);
    expect(screen.getByTestId('paused-round-info').textContent).toContain(
      '1 deep-research round'
    );
  });

  it('resumes optimistically and confirms through the store', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { status: 'accepted' }));
    vi.stubGlobal('fetch', fetchMock);

    const user = userEvent.setup();
    render(<SteerHarness status="paused" />);
    await user.click(screen.getByTestId('steer-resume-btn'));

    await waitFor(() =>
      expect(useStore.getState().status).toBe('running')
    );
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/session/sess-1/steer');
    expect(JSON.parse(init.body)).toEqual({ command: 'resume' });
    expect(init.headers['X-Session-Token']).toBe('tok');
  });

  it('refuses to steer without a session token', async () => {
    resetStore({ sessionToken: null });
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    render(<SteerHarness status="paused" />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId('steer-resume-btn'));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useStore.getState().steerError).toBe('No active session to steer');
  });
});

describe('steer store guards', () => {
  it('refuses redirect without focus before hitting the network', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const ok = await useStore.getState().sendSteerCommand('redirect', '   ');
    expect(ok).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(useStore.getState().steerError).toContain('focus');
  });

  it('refuses unknown commands before hitting the network', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const ok = await useStore.getState().sendSteerCommand('rewind');
    expect(ok).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(useStore.getState().steerError).toContain('Unknown');
  });

  it('clears in-flight state after the command resolves', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(200, { status: 'accepted' }))
    );

    await useStore.getState().sendSteerCommand('pause');
    expect(useStore.getState().steerInFlight).toBeNull();
  });
});
