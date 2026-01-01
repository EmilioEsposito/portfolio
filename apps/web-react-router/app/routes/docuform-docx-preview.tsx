import { useEffect, useRef, useState, useCallback } from 'react';

interface ContentControl {
  tag: string;
  alias: string;
  value: string;
  id: string | null;
}

/**
 * Standalone example of using docx-preview (docxjs) to render DOCX files in the browser.
 *
 * This spike tests:
 * 1. Basic DOCX rendering with docx-preview
 * 2. Fetching DOCX from our FastAPI backend
 * 3. Custom styling injection
 * 4. Content control visibility with post-render highlighting
 */
export default function DocuformDocxPreview() {
  // Dynamic import to avoid Vite bundling issues
  const [docxPreview, setDocxPreview] = useState<typeof import('docx-preview') | null>(null);

  useEffect(() => {
    import('docx-preview').then(setDocxPreview).catch(console.error);
  }, []);
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [documents, setDocuments] = useState<{ name: string; filename: string }[]>([]);
  const [selectedDocument, setSelectedDocument] = useState<string | null>(null);
  const [contentControls, setContentControls] = useState<ContentControl[]>([]);
  const [highlightedCount, setHighlightedCount] = useState(0);

  // Fetch available documents on mount
  useEffect(() => {
    async function fetchDocuments() {
      try {
        const response = await fetch('/api/docuform/documents');
        if (!response.ok) throw new Error('Failed to fetch documents');
        const data = await response.json();
        setDocuments(data.documents);
        // Auto-select first document
        if (data.documents.length > 0) {
          setSelectedDocument(data.documents[0].filename);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch documents');
      }
    }
    fetchDocuments();
  }, []);

  // Highlight content controls in the rendered HTML
  const highlightContentControls = useCallback((controls: ContentControl[]) => {
    if (!containerRef.current || controls.length === 0) return 0;

    let highlighted = 0;

    // Walk through text nodes and find matches
    const walker = document.createTreeWalker(
      containerRef.current,
      NodeFilter.SHOW_TEXT,
      null
    );

    const nodesToWrap: { node: Text; control: ContentControl; start: number; end: number }[] = [];

    let node: Text | null;
    while ((node = walker.nextNode() as Text | null)) {
      const text = node.textContent || '';

      for (const control of controls) {
        if (!control.value || control.value.trim() === '') continue;

        const index = text.indexOf(control.value);
        if (index !== -1) {
          nodesToWrap.push({
            node,
            control,
            start: index,
            end: index + control.value.length,
          });
          break; // Only one match per text node to avoid complexity
        }
      }
    }

    // Wrap matches in highlight spans (process in reverse to maintain offsets)
    for (const { node, control, start, end } of nodesToWrap.reverse()) {
      const parent = node.parentNode;
      if (!parent) continue;

      const before = node.textContent?.substring(0, start) || '';
      const match = node.textContent?.substring(start, end) || '';
      const after = node.textContent?.substring(end) || '';

      const wrapper = document.createElement('span');
      wrapper.className = 'content-control-highlight';
      wrapper.dataset.tag = control.tag;
      wrapper.dataset.alias = control.alias;
      wrapper.title = `${control.alias} (${control.tag})`;
      wrapper.textContent = match;

      const frag = document.createDocumentFragment();
      if (before) frag.appendChild(document.createTextNode(before));
      frag.appendChild(wrapper);
      if (after) frag.appendChild(document.createTextNode(after));

      parent.replaceChild(frag, node);
      highlighted++;
    }

    return highlighted;
  }, []);

  // Render selected document
  useEffect(() => {
    if (!selectedDocument || !containerRef.current || !docxPreview) return;

    async function loadAndRenderDocx() {
      setLoading(true);
      setError(null);
      setContentControls([]);
      setHighlightedCount(0);

      try {
        // Fetch content controls and document in parallel
        const [controlsResponse, docResponse] = await Promise.all([
          fetch(`/api/docuform/documents/${encodeURIComponent(selectedDocument!)}/content-controls`),
          fetch(`/api/docuform/documents/${encodeURIComponent(selectedDocument!)}`),
        ]);

        // Parse content controls
        let controls: ContentControl[] = [];
        if (controlsResponse.ok) {
          const data = await controlsResponse.json();
          controls = data.content_controls || [];
          setContentControls(controls);
          console.log('Content controls:', controls);
        } else {
          console.warn('Failed to fetch content controls:', controlsResponse.statusText);
        }

        const response = docResponse;
        if (!response.ok) {
          throw new Error(`Failed to fetch document: ${response.statusText}`);
        }

        const blob = await response.blob();

        // docx-preview options
        const options = {
          // Container element class
          className: 'docx-preview-container',
          // Enable inline styles
          inWrapper: true,
          // Ignore width from document (use container width)
          ignoreWidth: false,
          // Ignore height from document
          ignoreHeight: true,
          // Ignore fonts (use system fonts)
          ignoreFonts: false,
          // Enable debug mode to see content control elements
          debug: true,
          // Render headers/footers
          renderHeaders: true,
          renderFooters: true,
          // Custom CSS to inject
          // This will be added to the rendered document
        };

        // Clear container
        if (containerRef.current) {
          containerRef.current.innerHTML = '';
        }

        // Render the DOCX using dynamically imported module
        await docxPreview!.renderAsync(blob, containerRef.current!, undefined, options);

        // After rendering, highlight content controls by matching text
        if (containerRef.current && controls.length > 0) {
          const count = highlightContentControls(controls);
          setHighlightedCount(count);
          console.log(`Highlighted ${count} content controls out of ${controls.length}`);
        }

        // Debug: inspect rendered elements
        if (containerRef.current) {
          const allElements = containerRef.current.querySelectorAll('*');
          console.log('Total rendered elements:', allElements.length);

          // Look for our highlighted elements
          const highlightedElements = containerRef.current.querySelectorAll('.content-control-highlight');
          console.log('Highlighted content control elements:', highlightedElements.length);
        }

        setLoading(false);
      } catch (err) {
        console.error('Error rendering DOCX:', err);
        setError(err instanceof Error ? err.message : 'Failed to render document');
        setLoading(false);
      }
    }

    loadAndRenderDocx();
  }, [selectedDocument, docxPreview, highlightContentControls]);

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b border-border bg-card px-6 py-4">
        <h1 className="text-xl font-semibold text-foreground">DOCX Preview Spike</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Testing docx-preview library for rendering Word documents in the browser
        </p>
      </div>

      {/* Document selector */}
      <div className="px-6 py-4 border-b border-border bg-muted/30">
        <div className="flex items-center gap-4">
          <label htmlFor="document-select" className="text-sm font-medium text-foreground">
            Select Document:
          </label>
          <select
            id="document-select"
            value={selectedDocument || ''}
            onChange={(e) => setSelectedDocument(e.target.value)}
            className="px-3 py-2 rounded-md border border-border bg-background text-foreground text-sm"
            disabled={documents.length === 0}
          >
            {documents.length === 0 && <option value="">Loading...</option>}
            {documents.map((d) => (
              <option key={d.filename} value={d.filename}>
                {d.name}
              </option>
            ))}
          </select>
          {loading && <span className="text-sm text-muted-foreground">Rendering...</span>}
        </div>
      </div>

      {/* Error display */}
      {error && (
        <div className="mx-6 mt-4 p-4 bg-destructive/10 border border-destructive/30 rounded-md">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {/* Document preview container */}
      <div className="p-6">
        <div
          className="mx-auto bg-white shadow-lg rounded-lg overflow-hidden"
          style={{ maxWidth: '850px' }}
        >
          {/* Custom styles for the rendered document */}
          <style>{`
            .docx-preview-container {
              padding: 40px;
            }
            .docx-preview-container .docx-wrapper {
              background: white;
              padding: 0;
            }
            .docx-preview-container section.docx {
              box-shadow: none;
              padding: 0;
              margin: 0 auto;
            }
            /* Content control highlights (applied via JavaScript) */
            .content-control-highlight {
              background-color: rgba(139, 92, 246, 0.25);
              border-bottom: 2px solid #8b5cf6;
              padding: 1px 2px;
              border-radius: 2px;
              cursor: pointer;
              transition: background-color 0.15s ease;
            }
            .content-control-highlight:hover {
              background-color: rgba(139, 92, 246, 0.4);
            }
          `}</style>

          {/* Loading state - rendered outside the docx container */}
          {loading && !error && (
            <div className="flex items-center justify-center h-96">
              <div className="text-center">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto mb-4"></div>
                <p className="text-muted-foreground">Loading document...</p>
              </div>
            </div>
          )}
          {/* docx-preview container - React doesn't manage children here */}
          <div
            ref={containerRef}
            className="docx-preview-wrapper"
            style={{ minHeight: loading ? '0' : '500px', display: loading ? 'none' : 'block' }}
          />
        </div>
      </div>

      {/* Debug info */}
      <div className="px-6 py-4 border-t border-border bg-muted/30 mt-8">
        <h2 className="text-sm font-semibold text-foreground mb-2">Content Controls</h2>
        <div className="text-xs text-muted-foreground space-y-2">
          <p>
            Found <span className="font-mono text-foreground">{contentControls.length}</span> content controls,{' '}
            <span className="font-mono text-foreground">{highlightedCount}</span> highlighted in preview.
          </p>
          {contentControls.length > 0 && (
            <div className="mt-3">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-border">
                    <th className="py-1 pr-4 font-medium">Tag</th>
                    <th className="py-1 pr-4 font-medium">Alias</th>
                    <th className="py-1 font-medium">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {contentControls.map((cc, i) => (
                    <tr key={cc.id || i} className="border-b border-border/50">
                      <td className="py-1 pr-4 font-mono">{cc.tag}</td>
                      <td className="py-1 pr-4">{cc.alias}</td>
                      <td className="py-1 font-mono truncate max-w-xs">{cc.value || '(empty)'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
