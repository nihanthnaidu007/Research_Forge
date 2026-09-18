/**
 * Citation library tests (R6). The sidebar library loads cross-report
 * sources, imports pasted Zotero/RIS or BibTeX content, reports dedupe
 * results honestly, and renders imported sources with the
 * unknown-integrity degradation — never a fabricated verified badge.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import CitationLibrary from '../CitationLibrary';
import { useStore } from '../../store';

const SOURCES = [
  {
    id: 'lib-1',
    title: 'Attention Is All You Need',
    authors: ['Ashish Vaswani', 'Noam Shazeer'],
    year: 2017,
    venue: 'NeurIPS',
    persistent_id_key: 'doi:10.5555/3295222.3295349',
    integrity_status: 'unknown',
    occurrences: 1,
  },
  {
    id: 'lib-2',
    title: 'Example Blog Post',
    authors: [],
    year: 2024,
    persistent_id_key: 'url:example.com/blog',
    integrity_status: 'unknown',
    occurrences: 2,
  },
];

function resetStore(overrides = {}) {
  useStore.setState({
    librarySources: [],
    libraryLoading: false,
    libraryImporting: false,
    libraryError: null,
    loadLibrary: async () => {},
    importCitations: async () => null,
    ...overrides,
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
  resetStore();
});

describe('citation library rendering', () => {
  it('renders imported sources with the unknown-integrity badge', async () => {
    resetStore({ librarySources: SOURCES });
    render(<CitationLibrary />);

    await waitFor(() => {
      expect(screen.getAllByTestId('library-source-row')).toHaveLength(2);
    });
    expect(screen.getAllByTestId('library-integrity-unknown')).toHaveLength(2);
    expect(screen.getByText('Attention Is All You Need')).toBeInTheDocument();
    // Occurrence counts surface dedupe history instead of hiding it.
    expect(screen.getByText(/seen 2×/)).toBeInTheDocument();
    expect(screen.getByText('doi:10.5555/3295222.3295349')).toBeInTheDocument();
  });

  it('shows the empty state before any import', () => {
    render(<CitationLibrary />);

    expect(screen.getByTestId('library-empty')).toHaveTextContent(
      /No imported sources yet/
    );
  });

  it('renders load failures honestly with a retry path', async () => {
    resetStore({
      loadLibrary: async () => {
        useStore.setState({ libraryError: 'Failed to load library (500)' });
      },
    });
    render(<CitationLibrary />);

    await waitFor(() => {
      expect(screen.getByTestId('library-error')).toHaveTextContent(
        'Failed to load library (500)'
      );
    });
    expect(screen.getByTestId('library-refresh-btn')).toBeInTheDocument();
  });
});

describe('citation library import', () => {
  it('sends the pasted content to the import endpoint and reports counts', async () => {
    const user = userEvent.setup();
    const importCitations = vi.fn(async () => ({
      imported: 2,
      duplicates: 1,
      parsed: 3,
    }));
    resetStore({
      importCitations,
      librarySources: SOURCES,
      loadLibrary: async () => {},
    });
    render(<CitationLibrary />);

    await user.type(
      screen.getByTestId('library-import-content'),
      'TY  - JOUR'
    );
    await user.selectOptions(screen.getByTestId('library-import-format'), 'bibtex');
    await user.click(screen.getByTestId('library-import-btn'));

    expect(importCitations).toHaveBeenCalledWith('bibtex', 'TY  - JOUR');
    await waitFor(() => {
      expect(screen.getByTestId('library-import-summary')).toHaveTextContent(
        'Imported 2 sources · 1 duplicate skipped'
      );
    });
  });

  it('surfaces import failures instead of clearing the textarea', async () => {
    const user = userEvent.setup();
    resetStore({
      importCitations: async () => {
        useStore.setState({ libraryError: 'No importable records found in the provided content' });
        return null;
      },
    });
    render(<CitationLibrary />);

    await user.type(screen.getByTestId('library-import-content'), 'not a bibliography');
    await user.click(screen.getByTestId('library-import-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('library-error')).toHaveTextContent(
        'No importable records found'
      );
    });
    // The user's paste survives a failed import — nothing is silently lost.
    expect(screen.getByTestId('library-import-content')).toHaveValue(
      'not a bibliography'
    );
    expect(screen.queryByTestId('library-import-summary')).not.toBeInTheDocument();
  });

  it('disables the import button while a paste is empty', () => {
    render(<CitationLibrary />);

    expect(screen.getByTestId('library-import-btn')).toBeDisabled();
  });
});
