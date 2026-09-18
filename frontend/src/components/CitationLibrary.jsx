/**
 * Citation library (R6): the cross-report Source record collection.
 *
 * Sources are imported offline from Zotero/RIS or BibTeX bibliographies
 * and deduped on persistent-id keys. The library renders integrity,
 * never fabricates it: imported records were not retrieved by the
 * research pipeline, so they always show the unknown-degradation badge.
 */
import React, { useEffect, useState } from 'react';
import { Library, Loader2, AlertTriangle, Upload, RefreshCw } from 'lucide-react';
import { useStore } from '../store';

const INTEGRITY_STYLES = {
  unknown: { bg: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: 'rgba(245,158,11,0.3)' },
  verified: { bg: 'rgba(34,197,94,0.1)', color: '#22c55e', border: 'rgba(34,197,94,0.3)' },
  retracted: { bg: 'rgba(244,63,94,0.1)', color: '#fb7185', border: 'rgba(244,63,94,0.3)' },
};

function IntegrityBadge({ status }) {
  const style = INTEGRITY_STYLES[status] || INTEGRITY_STYLES.unknown;
  const label = status === 'unknown' ? 'unverified' : status;
  return (
    <span
      data-testid={`library-integrity-${status}`}
      title={
        status === 'unknown'
          ? 'Imported source — not retrieved by the research pipeline, so its integrity is unverified.'
          : `Integrity: ${status}`
      }
      style={{
        fontSize: '10px',
        padding: '2px 8px',
        borderRadius: '4px',
        background: style.bg,
        color: style.color,
        border: `1px solid ${style.border}`,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        fontFamily: 'monospace',
        flexShrink: 0,
      }}
    >
      {label}
    </span>
  );
}

function SourceRow({ source }) {
  return (
    <div
      className="p-3 rounded-sm bg-zinc-900/30 border border-zinc-800"
      data-testid="library-source-row"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm text-zinc-200 leading-snug">
          {source.title || 'Untitled source'}
        </p>
        <IntegrityBadge status={source.integrity_status || 'unknown'} />
      </div>
      <p className="text-xs text-zinc-500 mt-1 font-mono">
        {source.authors?.length > 0 ? source.authors.slice(0, 3).join(', ') : 'No authors listed'}
        {source.authors?.length > 3 ? ' et al.' : ''}
        {source.year ? ` · ${source.year}` : ''}
        {source.venue ? ` · ${source.venue}` : ''}
        {source.occurrences > 1 ? ` · seen ${source.occurrences}×` : ''}
      </p>
      {source.persistent_id_key && (
        <p className="text-[10px] text-zinc-600 mt-0.5 font-mono truncate">
          {source.persistent_id_key}
        </p>
      )}
    </div>
  );
}

const IMPORT_FORMATS = [
  { value: 'ris', label: 'RIS (Zotero)' },
  { value: 'bibtex', label: 'BibTeX' },
];

function CitationLibrary() {
  const {
    librarySources,
    libraryLoading,
    libraryImporting,
    libraryError,
    loadLibrary,
    importCitations,
  } = useStore();
  const [format, setFormat] = useState('ris');
  const [content, setContent] = useState('');
  const [importSummary, setImportSummary] = useState(null);

  useEffect(() => {
    loadLibrary();
  }, [loadLibrary]);

  const handleImport = async () => {
    if (!content.trim() || libraryImporting) return;
    setImportSummary(null);
    const result = await importCitations(format, content);
    if (result) {
      setImportSummary(result);
      setContent('');
    }
  };

  return (
    <div className="px-4 py-3 border-t border-zinc-800" data-testid="citation-library">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-mono uppercase tracking-wider text-zinc-500 px-1 flex items-center gap-2">
          <Library className="w-3.5 h-3.5" />
          Citation Library
        </h3>
        <button
          type="button"
          onClick={() => loadLibrary()}
          disabled={libraryLoading}
          data-testid="library-refresh-btn"
          aria-label="Refresh citation library"
          className="text-zinc-500 hover:text-cyan-400 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${libraryLoading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Import: offline paste of a Zotero/RIS or BibTeX bibliography. */}
      <div className="mb-3 space-y-2">
        <div className="flex gap-2">
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value)}
            data-testid="library-import-format"
            className="bg-zinc-900 border border-zinc-800 rounded-sm text-xs text-zinc-300 px-2 py-1.5 flex-1 focus:outline-none focus:border-cyan-500/50"
          >
            {IMPORT_FORMATS.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={handleImport}
            disabled={libraryImporting || !content.trim()}
            data-testid="library-import-btn"
            className="flex items-center gap-1.5 text-xs font-mono uppercase tracking-wider px-3 py-1.5 rounded-sm border border-zinc-800 text-zinc-300 hover:border-cyan-500/50 hover:bg-cyan-500/10 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {libraryImporting ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Upload className="w-3.5 h-3.5" />
            )}
            Import
          </button>
        </div>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Paste a Zotero/RIS or BibTeX export here…"
          rows={3}
          data-testid="library-import-content"
          className="w-full bg-zinc-900/50 border border-zinc-800 rounded-sm text-xs text-zinc-300 p-2 font-mono focus:outline-none focus:border-cyan-500/50 placeholder:text-zinc-600"
        />
        {importSummary && (
          <p
            data-testid="library-import-summary"
            className="text-xs text-cyan-400/90 font-mono px-1"
          >
            Imported {importSummary.imported} source{importSummary.imported === 1 ? '' : 's'}
            {importSummary.duplicates > 0
              ? ` · ${importSummary.duplicates} duplicate${importSummary.duplicates === 1 ? '' : 's'} skipped`
              : ''}
          </p>
        )}
        {libraryError && (
          <p
            data-testid="library-error"
            className="text-xs text-rose-400/90 font-mono px-1 flex items-center gap-1.5"
          >
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
            {libraryError}
          </p>
        )}
      </div>

      {libraryLoading && librarySources.length === 0 ? (
        <p data-testid="library-loading" className="text-xs text-zinc-500 font-mono px-1 py-2 flex items-center gap-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Loading library…
        </p>
      ) : librarySources.length === 0 ? (
        <p data-testid="library-empty" className="text-xs text-zinc-600 font-mono px-1 py-2">
          No imported sources yet. Import a Zotero/RIS or BibTeX export to
          build a cross-report library.
        </p>
      ) : (
        <div className="space-y-2" data-testid="library-source-list">
          {librarySources.map((source) => (
            <SourceRow key={source.id} source={source} />
          ))}
        </div>
      )}
    </div>
  );
}

export default CitationLibrary;
