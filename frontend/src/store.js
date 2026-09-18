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
// R4 live view: the SSE transport's AbortController. The stream replaces
// interval polling while it is healthy; polling stays as the fallback.
const _stream = { controller: null };

/**
 * Pure staleness rule for transport-driven writes (the stale-poll reset
 * race). A terminal view state absorbs any later non-terminal payload for
 * the same session: a status poll captured before completion can still
 * arrive after the full report was fetched, and applying it would
 * "downgrade" the finished report back to a running spinner. Terminal
 * payloads never race each other here — completion and error are the only
 * terminal states and they arrive once.
 */
export function isStaleTransportUpdate(currentStatus, incomingStatus) {
  const terminal = (s) => s === 'complete' || s === 'error';
  return terminal(currentStatus) && !terminal(incomingStatus);
}

// Download filename extension when the response carries none: most
// formats' ids are already their extension; markdown/bibtex/latex are not.
const EXPORT_FALLBACK_EXT = {
  markdown: 'md',
  bibtex: 'bib',
  latex: 'tex',
  'csl-apa': 'apa.txt',
  'csl-mla': 'mla.txt',
  'csl-ieee': 'ieee.txt',
};

const initialState = {
  sessionId: null,
  status: 'idle',
  topic: '',
  depth: 'quick',
  // Report template preset (W5): shapes outline structure only — section
  // count follows depth and synthesis length stays fixed.
  reportTemplate: 'standard',
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
  librarySources: [],
  libraryLoading: false,
  libraryImporting: false,
  libraryError: null,
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
  // Report reopen (R1): clicking a history row restores the persisted
  // report as the live view. Restores are read-only — the session token
  // was returned exactly once at run time and is never stored, so
  // token-gated actions (outline approval, steering, chat, exports) stay
  // unavailable and the UI says so instead of letting a click 401.
  sessionRestored: false,
  reopenLoading: false,
  reopenError: null,
};

export const useStore = create((set, get) => ({
  ...initialState,

  setTopic: (topic) => set({ topic }),
  setDepth: (depth) => set({ depth }),
  setReportTemplate: (template) => set({ reportTemplate: template }),

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
    const { topic, depth, reportTemplate, inputUrls } = get();

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
      researchRounds: 0,
      coverageGaps: [],
      factCheckResults: [],
      sessionRestored: false,
      reopenError: null,
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
            template: reportTemplate || 'standard',
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
      get().subscribeToStream(sessionId);

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

        // Stale-poll guard (R4): a response captured before completion can
        // land after the full report did. A terminal view never downgrades.
        if (isStaleTransportUpdate(get().status, data.status)) return;

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

        // Progress never rewinds: a poll response ordered before newer
        // updates (or carrying fewer of them) must not shrink the log.
        if (
          Array.isArray(stateUpdate.streamUpdates) &&
          stateUpdate.streamUpdates.length < (get().streamUpdates || []).length
        ) {
          delete stateUpdate.streamUpdates;
        }

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

  // R4 live view: subscribe to the server's SSE stream for real-time
  // updates instead of interval polling. Fetch-based transport because the
  // stream endpoint requires API-key headers (EventSource cannot set
  // them) — fail-closed auth beats transport simplicity. The server ends
  // the stream after a terminal `state` event, so this resolves by design
  // when the run finishes; on network failure polling takes over.
  subscribeToStream: async (sessionId) => {
    if (!sessionId) return;

    // Exactly one live transport: a new subscription supersedes any
    // running stream and any poll loop.
    if (_stream.controller) {
      _stream.controller.abort();
      _stream.controller = null;
    }
    if (_polling.intervalId) {
      clearTimeout(_polling.intervalId);
      _polling.intervalId = null;
    }

    const controller = new AbortController();
    _stream.controller = controller;

    const applyUpdateEvent = (event) => {
      if (get().sessionId !== sessionId) return;
      // Same staleness rule as the poll path: a terminal view never
      // downgrades, and the update log never shrinks.
      if (isStaleTransportUpdate(get().status, event.status)) return;

      const current = get().streamUpdates || [];
      const updates = event.message != null ? [...current, event.message] : current;
      set({
        status: event.status || get().status,
        currentAgent: event.current_agent || '',
        completedAgents: event.completed_agents || [],
        streamUpdates: updates,
      });
    };

    const applyStateEvent = (session) => {
      if (get().sessionId !== sessionId) return;
      const status = session.status;
      if (isStaleTransportUpdate(get().status, status)) return;

      const persisted = session.state || {};
      if (status === 'waiting_approval') {
        const outline = persisted.outline || [];
        set({
          status: 'waiting_approval',
          isLoading: false,
          ...(outline.length > 0 ? { outline } : {}),
        });
        return;
      }
      if (status === 'paused') {
        set({ status: 'paused', isLoading: false });
        return;
      }
      if (status === 'error') {
        set({ status: 'error', error: persisted.error || 'An error occurred', isLoading: false });
        return;
      }
      if (status === 'complete') {
        set({ status: 'complete', isLoading: false, error: null });
        get().fetchFullReport(sessionId);
      }
    };

    try {
      const response = await fetch(apiUrl(`/api/session/${sessionId}/stream`), {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`Stream unavailable (${response.status})`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      const handleDataLine = (line) => {
        if (!line.startsWith('data: ')) return; // keepalive comments etc.
        let event;
        try {
          event = JSON.parse(line.slice(6));
        } catch {
          console.error('SSE: malformed event payload');
          return;
        }
        if (event.type === 'update') {
          applyUpdateEvent(event);
        } else if (event.type === 'state') {
          applyStateEvent(event.session || {});
        }
      };

      while (true) {
        if (controller.signal.aborted || get().sessionId !== sessionId) return;
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep;
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          rawEvent.split('\n').forEach(handleDataLine);
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return; // superseded or session swapped
      // The failure is surfaced, not swallowed: logged here and acted on
      // below by falling back to polling, which carries the same guards.
      console.error('SSE stream error, falling back to polling:', err);
    } finally {
      if (_stream.controller === controller) _stream.controller = null;
    }

    // The stream ended without a terminal state event (disconnect). If the
    // session is still the live one and still running, take over with
    // polling so the view keeps moving.
    const status = get().status;
    if (
      get().sessionId === sessionId &&
      !['complete', 'error', 'waiting_approval', 'paused'].includes(status)
    ) {
      get().startPolling(sessionId);
    }
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

      get().subscribeToStream(sessionId);

    } catch (err) {
      console.error('Approve outline error:', err);
      set({ status: 'error', error: err.message, isLoading: false });
    }
  },

  fetchHistory: async (query = '') => {
    // Optional topic search (W5): server-side filter over session topics.
    set({ historyLoading: true, historyError: null });
    try {
      const response = await fetch(
        apiUrl(`/api/history${query ? `?q=${encodeURIComponent(query)}` : ''}`),
        {
          headers: authHeaders(),
        }
      );
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

  // Citation library (R6): cross-report Source records imported from
  // Zotero/RIS/BibTeX bibliographies. API-key scope only — library calls
  // carry no session token, by design: the library deliberately spans
  // reports. Imported sources render integrity_status 'unknown'; the
  // library displays integrity, it never fabricates it.
  loadLibrary: async () => {
    set({ libraryLoading: true, libraryError: null });
    try {
      const response = await fetch(apiUrl('/api/library/sources'), {
        headers: authHeaders(),
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(
          errData.detail || `Failed to load library (${response.status})`
        );
      }
      const data = await response.json();
      set({ librarySources: data.sources || [], libraryLoading: false });
    } catch (err) {
      console.error('Library load error:', err);
      set({ libraryError: err.message, libraryLoading: false });
    }
  },

  importCitations: async (format, content) => {
    set({ libraryImporting: true, libraryError: null });
    try {
      const response = await fetch(apiUrl('/api/library/import'), {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ format, content }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || `Import failed (${response.status})`);
      }
      // Refresh so imported and deduped rows show immediately.
      await get().loadLibrary();
      set({ libraryImporting: false });
      return data; // { imported, duplicates, parsed }
    } catch (err) {
      console.error('Citation import error:', err);
      set({ libraryError: err.message, libraryImporting: false });
      return null;
    }
  },

  // Report reopen (R1): a history row click restores the persisted
  // session as the live view — sections, verdicts, citations, transcript.
  // GET /api/session/{id} needs only the operator API key, so the restore
  // is token-free by design; token-gated actions stay unavailable and the
  // UI states that instead of letting the user hit a 401. Completed
  // reports restore fully; other statuses answer with an honest message —
  // a live run cannot be re-attached without the one-time session token.
  openSession: async (sessionId) => {
    if (!sessionId || get().reopenLoading) return;

    // Stop any live transport BEFORE swapping state: a poll or stream for
    // the previous session must never write into the restored view.
    if (_polling.intervalId) {
      clearTimeout(_polling.intervalId);
      _polling.intervalId = null;
    }
    if (_stream.controller) {
      _stream.controller.abort();
      _stream.controller = null;
    }

    set({ reopenLoading: true, reopenError: null });

    try {
      const response = await fetch(apiUrl(`/api/session/${sessionId}`), {
        headers: authHeaders(),
      });

      if (response.status === 404) {
        set({
          reopenLoading: false,
          reopenError: 'That report is no longer available — it may have expired.',
        });
        return;
      }
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `Failed to reopen report (${response.status})`);
      }

      const data = await response.json();
      const persisted = data.state || {};

      if (data.status !== 'complete') {
        set({
          reopenLoading: false,
          reopenError:
            data.status === 'error'
              ? 'That report failed during generation — nothing to reopen.'
              : 'Only completed reports can be reopened. This one is still ' +
                `${(data.status || 'unknown').replace('_', ' ')}.`,
        });
        return;
      }

      set({
        sessionId: data.id,
        sessionToken: null, // returned exactly once at run time — never restorable
        sessionRestored: true,
        status: 'complete',
        topic: data.topic || get().topic,
        streamUpdates: persisted.stream_updates || [],
        writtenSections: persisted.written_sections || [],
        sources: persisted.sources || [],
        confidenceScores: persisted.confidence_scores || {},
        overallConfidence: persisted.overall_confidence || 0,
        factCheckResults: persisted.fact_check_results || [],
        coverageGaps: persisted.coverage_gaps || [],
        researchRounds: persisted.research_rounds || 0,
        outline: persisted.approved_outline || persisted.outline || [],
        approvedOutline: persisted.approved_outline || [],
        outlineApproved: true,
        versioning_report: data.versioning_report || null,
        chatMessages: (persisted.chat_messages || []).map((m) => ({
          role: m.role,
          content: m.content,
          ts: m.ts,
        })),
        // Chat transcript renders read-only on a restored report.
        chatSessionExpired: false,
        chatError: null,
        chatLoading: false,
        currentAgent: '',
        completedAgents: persisted.completed_agents || [],
        error: null,
        isLoading: false,
        traceUrl: data.trace_url || null,
        reopenLoading: false,
      });
    } catch (err) {
      console.error('Reopen report error:', err);
      set({ reopenLoading: false, reopenError: err.message || 'Failed to reopen report' });
    }
  },

  exportReport: async (format) => {
    // format: 'pdf' | 'markdown' | 'html' | 'docx' | 'bibtex' | 'latex'
    //         | 'csl-apa' | 'csl-mla' | 'csl-ieee'
    const { sessionId, sessionToken } = get();
    if (!sessionId || !sessionToken) {
      throw new Error('No active session to export');
    }

    // CSL styles share one backend endpoint that switches on a style param.
    const isCsl = format.startsWith('csl-');
    const endpoint = isCsl ? '/api/export-csl' : `/api/export-${format}`;
    const styleParam = isCsl
      ? `&style=${encodeURIComponent(format.slice(4))}`
      : '';
    const response = await fetch(
      apiUrl(
        `${endpoint}?session_id=${encodeURIComponent(sessionId)}${styleParam}`
      ),
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
        // The SSE stream ended when the run paused — re-subscribe so the
        // live view continues streaming instead of falling back to polls.
        get().subscribeToStream(sessionId);
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
    // R4: the live transport must die with the session it belongs to.
    if (_stream.controller) {
      _stream.controller.abort();
      _stream.controller = null;
    }

    set({
      ...initialState,
      topic: get().topic,
    });
  },
}));

export default useStore;
