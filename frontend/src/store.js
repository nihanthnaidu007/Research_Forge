import { create } from 'zustand';

import { authHeaders, apiUrl, downloadResponseAsFile } from './api';

export const AGENTS = [
  { key: 'research', label: 'Web Research', icon: 'Search', description: 'Gathering sources from the web' },
  { key: 'document', label: 'Document Ingestion', icon: 'FileText', description: 'Processing uploaded documents' },
  { key: 'factcheck', label: 'Fact Check', icon: 'CheckCircle', description: 'Verifying claims against sources' },
  { key: 'outline', label: 'Outline', icon: 'List', description: 'Structuring the report' },
  { key: 'synthesis', label: 'Synthesis', icon: 'PenTool', description: 'Writing report sections' },
  { key: 'citations', label: 'Citations', icon: 'Quote', description: 'Formatting references' },
];

// Module-level polling handle. Lives outside Zustand to avoid
// storing non-serializable timer IDs in state, which causes
// unnecessary re-renders and breaks DevTools time-travel.
const _polling = { intervalId: null, errorCount: 0 };

// Download filename extension when the response carries none: most
// formats' ids are already their extension; markdown/bibtex/latex are not.
const EXPORT_FALLBACK_EXT = {
  markdown: 'md',
  bibtex: 'bib',
  latex: 'tex',
};

const initialState = {
  sessionId: null,
  status: 'idle',
  topic: '',
  depth: 'quick',
  inputUrls: [],
  uploadedFiles: [],
  currentAgent: '',
  completedAgents: [],
  streamUpdates: [],
  outline: [],
  outlineApproved: false,
  approvedOutline: [],
  writtenSections: [],
  sources: [],
  confidenceScores: {},
  overallConfidence: 0,
  agentStats: { sourcesFound: 0, claimsChecked: 0, sectionsWritten: 0, totalSections: 0 },
  error: null,
  isLoading: false,
  traceUrl: null,
  versioning_report: null,
  outlineEdits: '',
  sessionToken: null,
  history: [],
  historyLoading: false,
  historyError: null,
  // Chat-with-report transcript (W3). Lives in the store, not component
  // state, so the transcript survives re-renders and resets with the session.
  chatMessages: [],
  chatLoading: false,
  chatError: null,
  chatSessionExpired: false,
  // Deep-research loop (W4): round bookkeeping surfaced from the status
  // endpoint, plus mid-run steering state. ACK feedback is poll-bounded —
  // the store never pretends a command took effect before a poll confirms
  // it (or, for redirect, before the next section boundary).
  researchRounds: 0,
  coverageGaps: [],
  steerInFlight: null,
  steerAck: null,
  steerError: null,
};

export const useStore = create((set, get) => ({
  ...initialState,

  setTopic: (topic) => set({ topic }),
  setDepth: (depth) => set({ depth }),

  addUrl: (url) => set((state) => ({
    inputUrls: [...state.inputUrls, url]
  })),

  removeUrl: (index) => set((state) => ({
    inputUrls: state.inputUrls.filter((_, i) => i !== index)
  })),

  addFile: (file) => set((state) => ({
    uploadedFiles: [...state.uploadedFiles, file]
  })),

  removeFile: (index) => set((state) => ({
    uploadedFiles: state.uploadedFiles.filter((_, i) => i !== index)
  })),

  setOutlineEdits: (text) => set({ outlineEdits: text }),

  updateOutlineSection: (index, field, value) => set((state) => {
    const newOutline = [...state.outline];
    newOutline[index] = { ...newOutline[index], [field]: value };
    return { outline: newOutline };
  }),

  startReport: async () => {
    const { topic, depth, inputUrls } = get();

    if (!topic || !topic.trim() || topic.trim().length < 3) {
      set({ error: 'Please enter a research topic (minimum 3 characters)' });
      return;
    }

    // Clear any existing polling BEFORE resetting state
    if (_polling.intervalId) {
      clearTimeout(_polling.intervalId);
      _polling.intervalId = null;
    }

    set({
      status: 'running',
      error: null,
      sessionId: null,
      streamUpdates: [],
      writtenSections: [],
      outline: [],
      approvedOutline: [],
      outlineApproved: false,
      sources: [],
      confidenceScores: {},
      overallConfidence: 0,
      currentAgent: '',
      completedAgents: [],
      traceUrl: null,
      agentStats: { sourcesFound: 0, claimsChecked: 0, sectionsWritten: 0, totalSections: 0 },
      isLoading: true,
    });

    try {
      const parsedUrls = Array.isArray(inputUrls)
        ? inputUrls.filter(u =>
            u && (u.startsWith('http://') || u.startsWith('https://'))
          )
        : [];

      const { uploadedFiles } = get();
      const uploadedPdfPaths = [];

      if (uploadedFiles.length > 0) {
        const tempSessionId = crypto.randomUUID();
        for (const file of uploadedFiles) {
          const formData = new FormData();
          formData.append('session_id', tempSessionId);
          formData.append('file', file);
          try {
            const uploadRes = await fetch(apiUrl('/api/upload-pdf'), {
              method: 'POST',
              headers: authHeaders(),
              body: formData,
            });
            if (uploadRes.ok) {
              const uploadData = await uploadRes.json();
              uploadedPdfPaths.push(uploadData.path);
            } else {
              console.error('PDF upload failed for:', file.name);
            }
          } catch (err) {
            console.error('PDF upload error:', file.name, err);
          }
        }
      }

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000);

      let response;
      try {
        response = await fetch(apiUrl('/api/run'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({
            topic: topic.trim(),
            depth: depth || 'quick',
            input_urls: parsedUrls,
            uploaded_pdfs: uploadedPdfPaths,
          }),
          signal: controller.signal,
        });
      } catch (fetchErr) {
        if (fetchErr.name === 'AbortError') {
          set({
            status: 'error',
            error: 'Request timed out. The server took too long to respond.',
            isLoading: false,
          });
          return;
        }
        throw fetchErr;
      } finally {
        clearTimeout(timeoutId);
      }

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `Server error: ${response.status}`);
      }

      const data = await response.json();
      const sessionId = data.session_id;

      if (!sessionId) {
        throw new Error('No session ID returned from server');
      }

      set({ sessionId, sessionToken: data.session_token || null, isLoading: false });
      get().startPolling(sessionId);

    } catch (err) {
      console.error('Start report error:', err);
      set({ status: 'error', error: err.message || 'Failed to start report', isLoading: false });
    }
  },

  startPolling: (sessionId) => {
    if (_polling.intervalId) {
      clearTimeout(_polling.intervalId);
      _polling.intervalId = null;
    }
    _polling.errorCount = 0;

    const poll = async () => {
      // Guard: abort if the session changed
      if (get().sessionId !== sessionId) return;

      try {
        const response = await fetch(apiUrl(`/api/session/${sessionId}/status`), {
          headers: authHeaders(),
        });

        if (get().sessionId !== sessionId) return;

        if (!response.ok) {
          if (response.status === 404) {
            _polling.intervalId = null;
            set({ status: 'error', error: 'Session not found' });
            return;
          }
          // Non-404 error — schedule next poll with backoff
          _polling.errorCount = (_polling.errorCount || 0) + 1;
          const backoffDelay = Math.min(
            2000 * Math.pow(2, _polling.errorCount - 1),
            30000
          );
          _polling.intervalId = setTimeout(poll, backoffDelay);
          return;
        }

        // Success — reset error count
        _polling.errorCount = 0;

        const data = await response.json();

        const stateUpdate = {
          status: data.status,
          currentAgent: data.current_agent || '',
          completedAgents: data.completed_agents || [],
          streamUpdates: data.stream_updates || [],
          overallConfidence: data.overall_confidence || 0,
          traceUrl: data.trace_url || null,
          error: data.error || null,
          agentStats: {
            sourcesFound: data.sources_found || 0,
            claimsChecked: data.claims_checked || 0,
            sectionsWritten: data.sections_written || 0,
            totalSections: data.total_sections || 0,
          },
          researchRounds: data.research_rounds || 0,
          coverageGaps: data.coverage_gaps || [],
        };

        if (data.status === 'waiting_approval' &&
            data.outline && data.outline.length > 0) {
          stateUpdate.outline = data.outline;
        }

        if (data.versioning_report) {
          stateUpdate.versioning_report = data.versioning_report;
        }

        set(stateUpdate);

        if (data.status === 'waiting_approval') {
          _polling.intervalId = null;
          return;
        }

        if (data.status === 'complete') {
          _polling.intervalId = null;
          get().fetchFullReport(sessionId);
          return;
        }

        if (data.status === 'error') {
          _polling.intervalId = null;
          set({ error: data.error || 'An error occurred' });
          return;
        }

        // Poll-bounded steering ACKs: pause/resume banners disappear once
        // a poll observes their outcome; redirect stays as the current
        // steering intent until the run ends. Latency stays honest — the
        // banner never claims the command applied before this.
        const ack = get().steerAck;
        if (ack && ack.command !== 'redirect') {
          if (
            (ack.command === 'pause' && data.status === 'paused') ||
            (ack.command === 'resume' && data.status === 'running')
          ) {
            set({ steerAck: null });
          }
        }
        if (data.status === 'complete' || data.status === 'error') {
          set({ steerAck: null, steerError: null });
        }

        // Choose next poll delay based on current phase
        // research/document/factcheck: 5s (slow phase, infrequent updates)
        // outline: 3s
        // synthesis: 2s (one section at a time, want responsive updates)
        // running (unknown agent): 3s default
        const agent = data.current_agent || '';
        let delay = 3000;
        if (['research', 'document', 'factcheck'].includes(agent)) {
          delay = 5000;
        } else if (agent === 'synthesis') {
          delay = 2000;
        }

        _polling.intervalId = setTimeout(poll, delay);

      } catch (err) {
        console.error('Polling error:', err);
        _polling.errorCount = (_polling.errorCount || 0) + 1;
        const backoffDelay = Math.min(
          2000 * Math.pow(2, _polling.errorCount - 1),
          30000
        );
        _polling.intervalId = setTimeout(poll, backoffDelay);
      }
    };

    // Start the first poll immediately
    poll();
  },

  fetchFullReport: async (sessionId) => {
    // Guard: abort if the session changed while this was queued.
    // Without this, a stale response from a previous report can
    // overwrite the new session's state after the user starts over.
    if (get().sessionId !== sessionId) return;

    try {
      const response = await fetch(apiUrl(`/api/session/${sessionId}`), {
        headers: authHeaders(),
      });
      if (!response.ok) return;

      const data = await response.json();
      const state = data.state || {};

      set({
        writtenSections: state.written_sections || [],
        sources: state.sources || [],
        confidenceScores: state.confidence_scores || {},
        overallConfidence: state.overall_confidence || 0,
        versioning_report: data.versioning_report || get().versioning_report || null,
      });
    } catch (err) {
      console.error('Failed to fetch full report:', err);
    }
  },

  saveOutline: async () => {
    // Persist outline edits to the server (PUT) so the approval gate
    // resumes synthesis from the edited outline, not the original.
    const { sessionId, sessionToken, outline } = get();
    if (!sessionId || !sessionToken) return false;

    try {
      const response = await fetch(apiUrl(`/api/session/${sessionId}/outline`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders(sessionToken) },
        body: JSON.stringify({ outline }),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `Failed to save outline (${response.status})`);
      }

      const data = await response.json();
      if (data.outline) {
        set({ outline: data.outline });
      }
      return true;
    } catch (err) {
      console.error('Save outline error:', err);
      set({ error: err.message || 'Failed to save outline' });
      return false;
    }
  },

  approveOutline: async (editedOutline) => {
    const { sessionId, sessionToken, outline } = get();
    if (!sessionId) return;

    const outlineToSend = editedOutline || outline;

    // Persist edits server-side first; the approval gate resumes the
    // graph with the edited outline from the checkpoint.
    const saved = await get().saveOutline();
    if (!saved) {
      set({
        status: 'error',
        error: 'Failed to save outline edits — approval aborted',
        isLoading: false,
      });
      return;
    }

    set({
      status: 'running',
      isLoading: true,
      streamUpdates: [
        ...get().streamUpdates,
        `[${new Date().toLocaleTimeString()}] ✓ Outline approved — starting synthesis...`,
      ],
    });

    try {
      const response = await fetch(apiUrl('/api/approve-outline'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(sessionToken) },
        body: JSON.stringify({
          session_id: sessionId,
          outline: outlineToSend,
          edits: get().outlineEdits || null,
        }),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to approve outline');
      }

      set({
        isLoading: false,
        outlineApproved: true,
        approvedOutline: outlineToSend,
      });

      get().startPolling(sessionId);

    } catch (err) {
      console.error('Approve outline error:', err);
      set({ status: 'error', error: err.message, isLoading: false });
    }
  },

  fetchHistory: async () => {
    set({ historyLoading: true, historyError: null });
    try {
      const response = await fetch(apiUrl('/api/history'), {
        headers: authHeaders(),
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `Failed to load history (${response.status})`);
      }
      const data = await response.json();
      set({ history: data.sessions || [], historyLoading: false });
    } catch (err) {
      console.error('History load error:', err);
      set({ historyError: err.message, historyLoading: false });
    }
  },

  exportReport: async (format) => {
    // format: 'pdf' | 'markdown' | 'html' | 'docx' | 'bibtex'
    const { sessionId, sessionToken } = get();
    if (!sessionId || !sessionToken) {
      throw new Error('No active session to export');
    }

    const endpoint = `/api/export-${format}`;
    const response = await fetch(
      apiUrl(`${endpoint}?session_id=${encodeURIComponent(sessionId)}`),
      { method: 'POST', headers: authHeaders(sessionToken) }
    );

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.detail || `Export failed (${response.status})`);
    }

    const fallbackExt = EXPORT_FALLBACK_EXT[format] ?? format;
    return downloadResponseAsFile(
      response,
      `researchforge-report.${fallbackExt}`
    );
  },

  // Chat with the finished report (W3). The backend owns grounding,
  // citation validation, and budgets; the store just carries turns and
  // surfaces failure states. 404 after the 2h TTL means the session (and
  // its transcript) is gone — surfaced as an explicit expired banner.
  sendChatMessage: async (message) => {
    const { sessionId, sessionToken } = get();
    const trimmed = (message || '').trim();
    if (!sessionId || !sessionToken || !trimmed) return false;

    const now = () => new Date().toISOString();
    set((state) => ({
      chatMessages: [...state.chatMessages, { role: 'user', content: trimmed, ts: now() }],
      chatLoading: true,
      chatError: null,
      chatSessionExpired: false,
    }));

    try {
      const response = await fetch(apiUrl(`/api/session/${sessionId}/chat`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(sessionToken) },
        body: JSON.stringify({ message: trimmed }),
      });

      if (response.status === 404) {
        set({
          chatLoading: false,
          chatError: 'This report session has expired — start a new report to chat again.',
          chatSessionExpired: true,
        });
        return false;
      }

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        const detail = errData.detail;
        const message =
          typeof detail === 'string'
            ? detail
            : detail?.message || `Chat failed (${response.status})`;
        throw new Error(message);
      }

      const data = await response.json();
      set((state) => ({
        chatMessages: [
          ...state.chatMessages,
          {
            role: 'assistant',
            content: data.answer,
            resolvedCitations: data.resolved_citations || [],
            unresolvedCitations: data.unresolved_citations || [],
            ts: now(),
          },
        ],
        chatLoading: false,
      }));
      return true;
    } catch (err) {
      console.error('Chat error:', err);
      set({ chatLoading: false, chatError: err.message || 'Chat failed' });
      return false;
    }
  },

  clearChatError: () => set({ chatError: null, chatSessionExpired: false }),

  // Mid-run steering (W4). POSTs a pause/resume/redirect command to the
  // ownership-gated endpoint; the backend queues it under the per-session
  // lock and the run driver consumes it at the next step boundary. The
  // 200 is an ACK, not an effect — components state the 2-5s poll cadence
  // honestly instead of implying an instant stop.
  sendSteerCommand: async (command, focus = null) => {
    const { sessionId, sessionToken } = get();
    if (!sessionId || !sessionToken) {
      set({ steerError: 'No active session to steer' });
      return false;
    }
    if (!['pause', 'resume', 'redirect'].includes(command)) {
      set({ steerError: 'Unknown steering command' });
      return false;
    }
    if (command === 'redirect' && !(focus || '').trim()) {
      set({ steerError: 'Add focus text before redirecting' });
      return false;
    }

    set({ steerInFlight: command, steerError: null, steerAck: null });

    try {
      const response = await fetch(apiUrl(`/api/session/${sessionId}/steer`), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(sessionToken),
        },
        body: JSON.stringify(
          command === 'redirect'
            ? { command, focus: focus.trim() }
            : { command }
        ),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        const detail = errData.detail;
        const message =
          typeof detail === 'string'
            ? detail
            : detail?.message || `Steer command failed (${response.status})`;
        if (response.status === 429 && detail?.retry_after_seconds) {
          throw new Error(
            `${message} Retry in about ${detail.retry_after_seconds}s.`
          );
        }
        throw new Error(message);
      }

      if (command === 'resume') {
        // Optimistic flip: the backend leaves the session paused until the
        // run driver consumes the resume; polling confirms within one
        // cadence, and the poll ACK-clearing above expects this transition.
        set({ steerAck: { command, at: Date.now() }, status: 'running' });
      } else {
        set({ steerAck: { command, at: Date.now() } });
      }
      return true;
    } catch (err) {
      console.error('Steer command error:', err);
      set({ steerError: err.message || 'Steer command failed' });
      return false;
    } finally {
      set({ steerInFlight: null });
    }
  },

  clearSteerFeedback: () => set({ steerAck: null, steerError: null }),

  resetReport: () => {
    if (_polling.intervalId) {
      clearTimeout(_polling.intervalId);
      _polling.intervalId = null;
    }
    _polling.errorCount = 0;

    set({
      ...initialState,
      topic: get().topic,
    });
  },
}));

export default useStore;
