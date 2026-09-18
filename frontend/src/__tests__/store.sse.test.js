/**
 * Live SSE report view tests (R4). The stream endpoint is the primary
 * transport with polling as fallback, and the regression at the heart of
 * this feature: a stale status poll that arrives after completion can
 * never downgrade the finished report.
 */
import { describe, expect, it, beforeEach, vi } from 'vitest';

import { useStore, isStaleTransportUpdate } from '../store';

const SESSION_ID = 'sid-sse';

const COMPLETE_SESSION = {
  id: SESSION_ID,
  status: 'complete',
  state: {
    written_sections: [
      { section_id: 'sec_1', title: 'Findings', content: 'Body [1].', word_count: 2, sources_used: [] },
    ],
    sources: [{ url: 'https://example.com/a', title: 'A', domain: 'example.com', citation_number: 1 }],
  },
  versioning_report: null,
};

const STALE_RUNNING_PAYLOAD = {
  status: 'running',
  current_agent: 'research',
  completed_agents: [],
  stream_updates: ['Early update'],
  overall_confidence: 0,
  sources_found: 0,
  claims_checked: 0,
  sections_written: 0,
  total_sections: 0,
  research_rounds: 0,
  coverage_gaps: [],
};

const encoder = new TextEncoder();

function sseResponse(events) {
  const chunks = events.map((e) => encoder.encode(e));
  let i = 0;
  return {
    ok: true,
    status: 200,
    body: {
      getReader() {
        return {
          read() {
            return Promise.resolve(
              i < chunks.length
                ? { done: false, value: chunks[i++] }
                : { done: true, value: undefined }
            );
          },
        };
      },
    },
  };
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function seedStore(overrides = {}) {
  useStore.setState({
    sessionId: SESSION_ID,
    sessionToken: 'tok',
    status: 'running',
    streamUpdates: [],
    writtenSections: [],
    sources: [],
    isLoading: false,
    ...overrides,
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  seedStore();
});

describe('isStaleTransportUpdate', () => {
  it('treats non-terminal payloads as stale only against a terminal view', () => {
    expect(isStaleTransportUpdate('complete', 'running')).toBe(true);
    expect(isStaleTransportUpdate('error', 'running')).toBe(true);
    expect(isStaleTransportUpdate('running', 'running')).toBe(false);
    expect(isStaleTransportUpdate('running', 'complete')).toBe(false);
    expect(isStaleTransportUpdate('complete', 'complete')).toBe(false);
  });
});

describe('SSE live view', () => {
  it('appends update events in order and tracks the current agent', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => {
        if (String(url).includes('/stream')) {
          return Promise.resolve(
            sseResponse([
              'data: {"type":"update","message":"Researching sources","status":"running","current_agent":"research","completed_agents":[]}\n\n',
              'data: {"type":"update","message":"Writing sections","status":"running","current_agent":"synthesis","completed_agents":["research"]}\n\n',
            ])
          );
        }
        return Promise.resolve(jsonResponse(404, { detail: 'unexpected' }));
      })
    );

    await useStore.getState().subscribeToStream(SESSION_ID);

    const state = useStore.getState();
    expect(state.streamUpdates).toEqual(['Researching sources', 'Writing sections']);
    expect(state.currentAgent).toBe('synthesis');
    expect(state.completedAgents).toEqual(['research']);
  });

  it('applies the terminal state event and fetches the full report', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => {
        if (String(url).includes('/stream')) {
          return Promise.resolve(
            sseResponse([
              `data: ${JSON.stringify({ type: 'state', session: COMPLETE_SESSION })}\n\n`,
            ])
          );
        }
        if (String(url).endsWith(`/api/session/${SESSION_ID}`)) {
          return Promise.resolve(jsonResponse(200, COMPLETE_SESSION));
        }
        return Promise.resolve(jsonResponse(404, { detail: 'unexpected' }));
      })
    );

    await useStore.getState().subscribeToStream(SESSION_ID);

    const state = useStore.getState();
    expect(state.status).toBe('complete');
    expect(state.writtenSections).toHaveLength(1);
    expect(state.writtenSections[0].title).toBe('Findings');
    // The stream ended on a terminal state — no polling fallback started.
    // (The next status fetch would 404 "unexpected"; nothing threw.)
  });

  it('regression: a stale poll after completion cannot downgrade the view', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => {
        if (String(url).includes('/stream')) {
          return Promise.resolve(
            sseResponse([
              `data: ${JSON.stringify({ type: 'state', session: COMPLETE_SESSION })}\n\n`,
            ])
          );
        }
        if (String(url).endsWith(`/api/session/${SESSION_ID}`)) {
          return Promise.resolve(jsonResponse(200, COMPLETE_SESSION));
        }
        if (String(url).includes('/status')) {
          // Response captured before completion, arriving after it.
          return Promise.resolve(jsonResponse(200, STALE_RUNNING_PAYLOAD));
        }
        return Promise.resolve(jsonResponse(404, { detail: 'unexpected' }));
      })
    );

    await useStore.getState().subscribeToStream(SESSION_ID);
    expect(useStore.getState().status).toBe('complete');
    expect(useStore.getState().writtenSections).toHaveLength(1);

    // The stale poll now fires (startPolling runs its first poll
    // immediately). The view must not downgrade.
    useStore.getState().startPolling(SESSION_ID);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));

    const state = useStore.getState();
    expect(state.status).toBe('complete');
    expect(state.writtenSections).toHaveLength(1);
    expect(state.writtenSections[0].title).toBe('Findings');
    useStore.getState().resetReport();
  });

  it('falls back to polling when the stream connection fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => {
        if (String(url).includes('/stream')) {
          return Promise.reject(new TypeError('network down'));
        }
        if (String(url).includes('/status')) {
          return Promise.resolve(jsonResponse(200, STALE_RUNNING_PAYLOAD));
        }
        return Promise.resolve(jsonResponse(404, { detail: 'unexpected' }));
      })
    );

    await useStore.getState().subscribeToStream(SESSION_ID);
    // The polling fallback starts asynchronously — let its first poll land.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));

    const state = useStore.getState();
    expect(state.status).toBe('running');
    expect(state.streamUpdates).toEqual(['Early update']);
    useStore.getState().resetReport();
  });

  it('stops writing when the session is swapped mid-stream', async () => {
    let releaseSecond;
    const gate = new Promise((resolve) => { releaseSecond = resolve; });
    const chunks = [
      encoder.encode(
        'data: {"type":"update","message":"first","status":"running","current_agent":"research","completed_agents":[]}\n\n'
      ),
    ];
    let readCount = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((url) => {
        if (String(url).includes('/stream')) {
          return Promise.resolve({
            ok: true,
            status: 200,
            body: {
              getReader() {
                return {
                  read() {
                    readCount += 1;
                    if (readCount === 1) {
                      return Promise.resolve({ done: false, value: chunks[0] });
                    }
                    return gate.then(() => ({ done: true, value: undefined }));
                  },
                };
              },
            },
          });
        }
        return Promise.resolve(jsonResponse(404, { detail: 'unexpected' }));
      })
    );

    const streamDone = useStore.getState().subscribeToStream(SESSION_ID);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(useStore.getState().streamUpdates).toEqual(['first']);

    // The user starts a different report — the old stream must go silent.
    seedStore({ sessionId: 'sid-other', streamUpdates: [] });
    releaseSecond();
    await streamDone;

    expect(useStore.getState().sessionId).toBe('sid-other');
    expect(useStore.getState().streamUpdates).toEqual([]);
  });
});
