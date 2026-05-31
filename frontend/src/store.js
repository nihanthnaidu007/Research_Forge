import { create } from 'zustand';

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
            const uploadRes = await fetch('/api/upload-pdf', {
              method: 'POST',
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
        response = await fetch('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
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

      set({ sessionId, isLoading: false });
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
        const response = await fetch(`/api/session/${sessionId}/status`);

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
      const response = await fetch(`/api/session/${sessionId}`);
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

  approveOutline: async (editedOutline) => {
    const { sessionId, outline } = get();
    if (!sessionId) return;

    const outlineToSend = editedOutline || outline;

    set({
      status: 'running',
      isLoading: true,
      streamUpdates: [
        ...get().streamUpdates,
        `[${new Date().toLocaleTimeString()}] ✓ Outline approved — starting synthesis...`,
      ],
    });

    try {
      const response = await fetch('/api/approve-outline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
