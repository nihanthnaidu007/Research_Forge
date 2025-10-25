import { create } from 'zustand';

export const AGENTS = [
  { key: 'research', label: 'Web Research', icon: 'Search', description: 'Gathering sources from the web' },
  { key: 'document', label: 'Document Ingestion', icon: 'FileText', description: 'Processing uploaded documents' },
  { key: 'factcheck', label: 'Fact Check', icon: 'CheckCircle', description: 'Verifying claims against sources' },
  { key: 'outline', label: 'Outline', icon: 'List', description: 'Structuring the report' },
  { key: 'synthesis', label: 'Synthesis', icon: 'PenTool', description: 'Writing report sections' },
  { key: 'citations', label: 'Citations', icon: 'Quote', description: 'Formatting references' },
];

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
  pollingInterval: null,
  versioning_report: null,
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

  updateOutlineSection: (index, field, value) => set((state) => {
    const newOutline = [...state.outline];
    newOutline[index] = { ...newOutline[index], [field]: value };
    return { outline: newOutline };
  }),

  startReport: async () => {
    const { topic, depth, inputUrls, pollingInterval } = get();

    if (!topic || !topic.trim() || topic.trim().length < 3) {
      set({ error: 'Please enter a research topic (minimum 3 characters)' });
      return;
    }

    // Clear any existing polling BEFORE resetting state
    if (pollingInterval) {
      clearInterval(pollingInterval);
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
      pollingInterval: null,
      agentStats: { sourcesFound: 0, claimsChecked: 0, sectionsWritten: 0, totalSections: 0 },
      isLoading: true,
    });

    try {
      const parsedUrls = Array.isArray(inputUrls)
        ? inputUrls.filter(u => u && u.startsWith('http'))
        : [];

      const response = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic: topic.trim(),
          depth: depth || 'quick',
          input_urls: parsedUrls,
          uploaded_pdfs: [],
        }),
      });

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
    const existing = get().pollingInterval;
    if (existing) clearInterval(existing);

    const interval = setInterval(async () => {
      // Guard: abort if the session changed while this callback was queued
      if (get().sessionId !== sessionId) {
        clearInterval(interval);
        return;
      }

      try {
        const response = await fetch(`/api/session/${sessionId}/status`);

        // Double-check session hasn't changed while fetch was in flight
        if (get().sessionId !== sessionId) {
          clearInterval(interval);
          return;
        }

        if (!response.ok) {
          if (response.status === 404) {
            clearInterval(interval);
            set({ pollingInterval: null, status: 'error', error: 'Session not found' });
            return;
          }
          return;
        }

        const data = await response.json();

        // Build a single atomic state update to prevent intermediate renders
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

        // Include outline in the SAME set() call to avoid split renders
        if (data.status === 'waiting_approval' && data.outline && data.outline.length > 0) {
          stateUpdate.outline = data.outline;
        }

        if (data.versioning_report) {
          stateUpdate.versioning_report = data.versioning_report;
        }

        set(stateUpdate);

        if (data.status === 'waiting_approval') {
          clearInterval(interval);
          set({ pollingInterval: null });
          return;
        }

        if (data.status === 'complete') {
          clearInterval(interval);
          set({ pollingInterval: null });
          get().fetchFullReport(sessionId);
          return;
        }

        if (data.status === 'error') {
          clearInterval(interval);
          set({ pollingInterval: null, error: data.error || 'An error occurred' });
          return;
        }

      } catch (err) {
        console.error('Polling error:', err);
      }
    }, 2000);

    set({ pollingInterval: interval });
  },

  fetchFullReport: async (sessionId) => {
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
          edits: null,
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
    const { pollingInterval } = get();
    if (pollingInterval) clearInterval(pollingInterval);

    set({
      ...initialState,
      topic: get().topic,
    });
  },
}));

export default useStore;
