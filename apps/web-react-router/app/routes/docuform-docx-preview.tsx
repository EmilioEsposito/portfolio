import { useEffect, useRef, useState } from 'react';

/**
 * Standalone example of using docx-preview (docxjs) to render DOCX files in the browser.
 *
 * This spike tests:
 * 1. Basic DOCX rendering with docx-preview
 * 2. Fetching DOCX from our FastAPI backend
 * 3. Custom styling injection
 * 4. Content control visibility (for future field highlighting)
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

  // Render selected document
  useEffect(() => {
    if (!selectedDocument || !containerRef.current || !docxPreview) return;

    async function loadAndRenderDocx() {
      setLoading(true);
      setError(null);

      try {
        // Fetch the DOCX file from our API
        const response = await fetch(`/api/docuform/documents/${encodeURIComponent(selectedDocument!)}`);
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
        await docxPreview.renderAsync(blob, containerRef.current!, undefined, options);

        // After rendering, let's inspect what elements are created
        // This helps us understand if content controls are preserved
        if (containerRef.current) {
          const allElements = containerRef.current.querySelectorAll('*');
          console.log('Total rendered elements:', allElements.length);

          // Look for potential content control markers
          const sdtElements = containerRef.current.querySelectorAll('[class*="sdt"]');
          console.log('SDT (Structured Document Tag) elements:', sdtElements.length);

          // Log some element classes to understand the structure
          const uniqueClasses = new Set<string>();
          allElements.forEach((el) => {
            el.classList.forEach((cls) => uniqueClasses.add(cls));
          });
          console.log('Unique CSS classes in rendered document:', Array.from(uniqueClasses).sort());
        }

        setLoading(false);
      } catch (err) {
        console.error('Error rendering DOCX:', err);
        setError(err instanceof Error ? err.message : 'Failed to render document');
        setLoading(false);
      }
    }

    loadAndRenderDocx();
  }, [selectedDocument, docxPreview]);

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
            /* Highlight SDT (Structured Document Tag) elements if they exist */
            .docx-preview-container [class*="sdt"] {
              background-color: rgba(139, 92, 246, 0.2);
              border-bottom: 2px solid #8b5cf6;
              padding: 2px 4px;
              border-radius: 2px;
            }
            /* Style for content controls */
            .docx-preview-container .sdt-content {
              background-color: rgba(16, 185, 129, 0.2);
              border-bottom: 2px solid #10b981;
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
        <h2 className="text-sm font-semibold text-foreground mb-2">Debug Info</h2>
        <p className="text-xs text-muted-foreground">
          Open browser DevTools console to see rendered element analysis.
          <br />
          We're looking for SDT (Structured Document Tag) elements which represent Word content controls.
        </p>
      </div>
    </div>
  );
}
