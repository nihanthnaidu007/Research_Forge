import React, { useState, useRef, useCallback } from 'react';
import { motion } from 'framer-motion';
import { FileEdit, CheckCircle, ArrowRight } from 'lucide-react';
import { cn } from '../lib/utils';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';

export function OutlineApprovalZone({
  outline,
  onUpdateSection,
  onApprove,
  isLoading,
  outlineEdits,
  onEditsChange
}) {
  // Track which sections the user actually edited
  const [editedSections, setEditedSections] = useState(new Set());
  // Snapshot the original outline on first render for local diff
  const originalRef = useRef(outline.map(s => ({ ...s })));

  const handleFieldChange = useCallback((index, field, value) => {
    // Forward to parent handler
    onUpdateSection(index, field, value);

    // Detect local edit vs original
    const original = originalRef.current[index];
    if (original) {
      const sectionId = original.section_id;
      const originalVal = original[field] || '';
      if (value.trim().toLowerCase() !== originalVal.trim().toLowerCase()) {
        setEditedSections(prev => new Set([...prev, sectionId]));
      } else {
        // Check if the OTHER field is still edited
        const otherField = field === 'title' ? 'description' : 'title';
        const currentOutlineSection = { ...outline[index], [field]: value };
        const otherOriginal = (original[otherField] || '').trim().toLowerCase();
        const otherCurrent = (currentOutlineSection[otherField] || '').trim().toLowerCase();
        if (otherOriginal === otherCurrent) {
          setEditedSections(prev => {
            const next = new Set(prev);
            next.delete(sectionId);
            return next;
          });
        }
      }
    }
  }, [onUpdateSection, outline]);

  if (!outline || outline.length === 0) return null;
  
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex items-start gap-4 p-4 rounded-sm bg-amber-500/5 border border-amber-500/20">
        <div className="p-2 rounded-sm bg-amber-500/10">
          <FileEdit className="w-5 h-5 text-amber-400" strokeWidth={1.5} />
        </div>
        <div className="flex-1">
          <h2 className="text-lg font-display font-semibold text-zinc-100">
            Review Your Report Outline
          </h2>
          <p className="text-sm text-zinc-400 mt-1">
            The agents have drafted this structure. Edit any section title or description before writing begins.
          </p>
        </div>
      </div>
      
      {/* Sections */}
      <div className="space-y-4">
        {outline.map((section, index) => (
          <motion.div
            key={section.section_id || index}
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: index * 0.1 }}
            className="group relative p-4 rounded-sm bg-zinc-900/50 border border-zinc-800 hover:border-zinc-700 transition-colors"
          >
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-cyan-500/50 rounded-l-sm" />
            
            <div className="flex items-center gap-3 mb-3">
              <span className="flex items-center justify-center w-6 h-6 rounded-sm bg-cyan-500/10 text-cyan-400 text-xs font-mono font-bold">
                {index + 1}
              </span>
              <span className="text-xs font-mono text-zinc-500 uppercase tracking-wider">
                Section {section.order || index + 1}
              </span>
              {/* Versioning badge */}
              {editedSections.has(section.section_id) ? (
                <span style={{
                  fontSize: '11px',
                  padding: '2px 8px',
                  borderRadius: '4px',
                  background: 'rgba(245, 158, 11, 0.15)',
                  color: '#f59e0b',
                  border: '1px solid rgba(245, 158, 11, 0.3)',
                }}>
                  ✏️ edited — will re-synthesize
                </span>
              ) : (
                <span style={{
                  fontSize: '11px',
                  padding: '2px 8px',
                  borderRadius: '4px',
                  background: 'rgba(34, 197, 94, 0.1)',
                  color: '#22c55e',
                  border: '1px solid rgba(34, 197, 94, 0.3)',
                }}>
                  ✓ unchanged — will reuse
                </span>
              )}
            </div>
            
            <div className="space-y-3 pl-9">
              <div>
                <label className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-1.5 block">
                  Title
                </label>
                <Input
                  value={section.title}
                  onChange={(e) => handleFieldChange(index, 'title', e.target.value)}
                  className="bg-zinc-950 border-zinc-800 focus:border-cyan-500/50 text-zinc-100"
                  data-testid={`outline-title-${index}`}
                />
              </div>
              
              <div>
                <label className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-1.5 block">
                  Description
                </label>
                <Textarea
                  value={section.description}
                  onChange={(e) => handleFieldChange(index, 'description', e.target.value)}
                  rows={2}
                  className="bg-zinc-950 border-zinc-800 focus:border-cyan-500/50 text-zinc-100 resize-none"
                  data-testid={`outline-description-${index}`}
                />
              </div>
            </div>
          </motion.div>
        ))}
      </div>
      
      {/* Versioning summary banner */}
      {editedSections.size > 0 ? (
        <div style={{
          padding: '10px 14px',
          borderRadius: '8px',
          background: 'rgba(245, 158, 11, 0.08)',
          border: '1px solid rgba(245, 158, 11, 0.2)',
          fontSize: '13px',
          color: '#f59e0b',
        }}>
          {editedSections.size} section(s) edited — only these will be re-written.{' '}
          {outline.length - editedSections.size} section(s) will reuse existing content.
        </div>
      ) : (
        <div style={{
          padding: '10px 14px',
          borderRadius: '8px',
          background: 'rgba(34, 197, 94, 0.08)',
          border: '1px solid rgba(34, 197, 94, 0.2)',
          fontSize: '13px',
          color: '#22c55e',
        }}>
          ✓ No changes detected — approving will reuse all existing content instantly.
        </div>
      )}

      <div className="mt-4">
        <label className="block text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">
          Additional guidance for synthesis (optional)
        </label>
        <textarea
          className="w-full bg-zinc-900 border border-zinc-700 rounded-sm text-zinc-300 text-sm p-3 resize-none focus:outline-none focus:border-cyan-500/50 placeholder-zinc-600"
          rows={3}
          placeholder="e.g. Focus more on recent data. Avoid speculative claims."
          value={outlineEdits}
          onChange={(e) => onEditsChange(e.target.value)}
        />
      </div>

      {/* Approve Button */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.5 }}
        className="flex justify-end pt-4"
      >
        <Button
          onClick={onApprove}
          disabled={isLoading}
          className="btn-primary px-6 py-2.5 rounded-sm font-medium"
          data-testid="approve-outline-btn"
        >
          {isLoading ? (
            <>Processing...</>
          ) : (
            <>
              <CheckCircle className="w-4 h-4 mr-2" />
              Approve & Start Writing
              <ArrowRight className="w-4 h-4 ml-2" />
            </>
          )}
        </Button>
      </motion.div>
    </motion.div>
  );
}

export default OutlineApprovalZone;
