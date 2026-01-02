import { useState, useEffect, useRef } from 'react';
import { FileText, Users, LayoutTemplate, Home, Settings, Bell, Search, ChevronRight, ChevronDown, Check, Sparkles, RotateCcw, Eye, Download, Zap, Shield, FileCheck, Undo2, Scale, Upload, Plus, Trash2, type LucideIcon } from 'lucide-react';
import { useAuth } from '@clerk/react-router';
import { SerniaAuthGuard } from '~/components/sernia-auth-guard';

type FieldSource = 'ai' | 'client' | 'attorney';
type FieldStatus = 'pending' | 'reviewed';


interface AIContext {
  goal: string;
  jurisdiction: string;
  inputs: string[];
}

interface Field {
  id: string;
  title: string;
  section: string;
  source: FieldSource;
  originalSource?: FieldSource; // Track original source before attorney override
  status: FieldStatus;
  value: string;
  originalValue: string | null;
  edited?: boolean;
  aiContext?: AIContext;
  derivedFrom?: string;
}

interface NavItem {
  id: string;
  icon: LucideIcon;
  label: string;
  badge?: number;
}

interface SourcePillProps {
  source: FieldSource;
  originalSource?: FieldSource;
}

interface FieldRowProps {
  field: Field;
  isExpanded: boolean;
  onToggle: () => void;
  onApprove: () => void;
  onMarkNeedsReview: () => void;
  onUpdateValue: (value: string) => void;
  onRegenerate: () => void;
}

interface Template {
  name: string;
  filename: string;
}

export default function DocgenPage() {
  return (
    <SerniaAuthGuard>
      <DocgenPageContent />
    </SerniaAuthGuard>
  );
}

function DocgenPageContent() {
  const { getToken } = useAuth();

  const [activeView, setActiveView] = useState('review');
  const [expandedField, setExpandedField] = useState<string | null>('recital_transfer_terms');

  // Templates state
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetch templates from API
  const fetchTemplates = async () => {
    setTemplatesLoading(true);
    try {
      const token = await getToken();
      const response = await fetch('/api/docuform/documents', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error('Failed to fetch templates');
      const data = await response.json();
      setTemplates(data.documents || []);
    } catch (err) {
      console.error('Failed to fetch templates:', err);
    } finally {
      setTemplatesLoading(false);
    }
  };

  // Fetch templates when switching to templates view
  useEffect(() => {
    if (activeView === 'templates') {
      fetchTemplates();
    }
  }, [activeView]);

  // Handle file upload
  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setUploading(true);
    setUploadError(null);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const token = await getToken();
      const response = await fetch('/api/docuform/documents/upload', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Upload failed');
      }

      // Refresh templates list
      await fetchTemplates();

      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  // Handle template deletion
  const handleDelete = async (filename: string) => {
    if (!confirm(`Are you sure you want to delete "${filename}"?`)) {
      return;
    }

    try {
      const token = await getToken();
      const response = await fetch(`/api/docuform/documents/${encodeURIComponent(filename)}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Delete failed');
      }

      // Refresh templates list
      await fetchTemplates();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Delete failed');
    }
  };

  const [fields, setFields] = useState<Field[]>([
    { id: 'company_name', title: 'Company Name', section: 'Preamble', source: 'client', status: 'reviewed', value: 'Acme Corporation', originalValue: 'Acme Corporation' },
    { id: 'recital_transfer_terms', title: 'Transfer Terms Recital', section: 'Recitals', source: 'ai', status: 'pending', value: 'the parties wish to define the terms under which shares may be transferred, including rights of first refusal, co-sale rights, and drag-along provisions, to ensure an orderly transfer process and protect minority shareholders', originalValue: 'the parties wish to define the terms under which shares may be transferred, including rights of first refusal, co-sale rights, and drag-along provisions, to ensure an orderly transfer process and protect minority shareholders', aiContext: { goal: 'Explain transfer restriction purpose', jurisdiction: 'Delaware', inputs: ['company_name', 'jurisdiction'] } },
    { id: 'jurisdiction', title: 'Jurisdiction', section: 'Preamble', source: 'client', status: 'reviewed', value: 'Delaware', originalValue: 'Delaware' },
    { id: 'def_affiliate', title: 'Affiliate Definition', section: 'Article I', source: 'ai', status: 'pending', value: 'any person or entity that directly or indirectly controls, is controlled by, or is under common control with another person or entity. For purposes of this definition, "control" means the possession, directly or indirectly, of the power to direct or cause the direction of management and policies', originalValue: 'any person or entity that directly or indirectly controls, is controlled by, or is under common control with another person or entity. For purposes of this definition, "control" means the possession, directly or indirectly, of the power to direct or cause the direction of management and policies', aiContext: { goal: 'Standard affiliate definition', jurisdiction: 'Delaware', inputs: [] } },
    { id: 'investment_amount', title: 'Investment Amount', section: 'Article I', source: 'client', status: 'reviewed', value: '$500,000', originalValue: '$500,000', derivedFrom: 'Sum of Schedule A amounts' },
    { id: 'transfer_restriction', title: 'General Restriction', section: 'Article II', source: 'ai', status: 'pending', value: 'No Shareholder shall sell, assign, transfer, pledge, hypothecate, or otherwise dispose of any Shares or any interest therein, whether voluntarily, by operation of law, or otherwise, except in compliance with the terms of this Agreement. Any purported transfer in violation of this Section 2.1 shall be null and void ab initio', originalValue: 'No Shareholder shall sell, assign, transfer, pledge, hypothecate, or otherwise dispose of any Shares or any interest therein, whether voluntarily, by operation of law, or otherwise, except in compliance with the terms of this Agreement. Any purported transfer in violation of this Section 2.1 shall be null and void ab initio', aiContext: { goal: 'Comprehensive transfer restriction clause', jurisdiction: 'Delaware', inputs: ['company_name'] } },
  ]);

  const pendingFields = fields.filter(f => f.status === 'pending');
  const reviewedFields = fields.filter(f => f.status === 'reviewed');

  const toggleField = (fieldId: string) => {
    setExpandedField(expandedField === fieldId ? null : fieldId);
  };

  const approveField = (fieldId: string) => {
    setFields(fields.map(f =>
      f.id === fieldId ? { ...f, status: 'reviewed' as FieldStatus } : f
    ));
  };

  const markNeedsReview = (fieldId: string) => {
    setFields(fields.map(f =>
      f.id === fieldId ? { ...f, status: 'pending' as FieldStatus } : f
    ));
  };

  const updateFieldValue = (fieldId: string, newValue: string) => {
    setFields(fields.map(f => {
      if (f.id !== fieldId) return f;
      const isEdited = newValue !== f.originalValue;
      // If attorney edits the value, change source to attorney and track original
      if (isEdited && f.source !== 'attorney') {
        return {
          ...f,
          value: newValue,
          edited: true,
          originalSource: f.source,
          source: 'attorney' as FieldSource,
        };
      }
      // If they revert to original, restore original source
      if (!isEdited && f.originalSource) {
        return {
          ...f,
          value: newValue,
          edited: false,
          source: f.originalSource,
          originalSource: undefined,
        };
      }
      return { ...f, value: newValue, edited: isEdited };
    }));
  };

  const regenerateField = (fieldId: string) => {
    setFields(fields.map(f => {
      if (f.id !== fieldId) return f;
      // Reset to original AI value, keep source as AI
      return {
        ...f,
        value: f.originalValue || f.value,
        edited: false,
        source: f.originalSource || f.source,
        originalSource: undefined,
      };
    }));
  };

  const navItems: NavItem[] = [
    { id: 'dashboard', icon: Home, label: 'Dashboard' },
    { id: 'documents', icon: FileText, label: 'Documents', badge: 12 },
    { id: 'templates', icon: LayoutTemplate, label: 'Templates' },
    { id: 'clients', icon: Users, label: 'Clients' },
    { id: 'review', icon: FileCheck, label: 'Review Queue', badge: 5 },
  ];

  // Helper to get highlight class for document spans
  const getHighlightClass = (source: FieldSource) => {
    switch (source) {
      case 'ai':
        return 'bg-violet-200 dark:bg-violet-500/30 border-b-2 border-violet-600';
      case 'client':
        return 'bg-blue-200 dark:bg-blue-500/30 border-b-2 border-blue-600';
      case 'attorney':
        return 'bg-emerald-200 dark:bg-emerald-500/30 border-b-2 border-emerald-600';
    }
  };

  const getFieldSource = (fieldId: string): FieldSource => {
    return fields.find(f => f.id === fieldId)?.source || 'client';
  };

  return (
    <div className="h-screen bg-background text-foreground flex overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 bg-card border-r border-border flex flex-col shrink-0">
        <div className="h-14 px-4 flex items-center border-b border-border">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-linear-to-br from-amber-500 to-amber-700 rounded-lg flex items-center justify-center">
              <FileText size={16} className="text-white" />
            </div>
            <span className="text-base font-semibold text-foreground">Counsel</span>
          </div>
        </div>

        <div className="p-3 border-b border-border">
          <button className="w-full p-2.5 bg-muted/50 rounded-lg flex items-center justify-between hover:bg-muted transition-colors">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center text-xs font-semibold text-slate-700 dark:text-slate-300">MW</div>
              <div className="text-left">
                <div className="text-sm font-medium text-foreground">Mitchell & Webb</div>
                <div className="text-xs text-muted-foreground">Pro Plan</div>
              </div>
            </div>
            <ChevronDown size={16} className="text-muted-foreground" />
          </button>
        </div>

        <nav className="flex-1 p-3 space-y-1">
          {navItems.map(item => (
            <button
              key={item.id}
              onClick={() => setActiveView(item.id)}
              className={`w-full px-3 py-2.5 rounded-lg flex items-center justify-between transition-colors ${
                activeView === item.id
                  ? 'bg-orange-500/20 text-foreground'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              }`}
            >
              <div className="flex items-center gap-3">
                <item.icon size={18} />
                <span className="text-sm font-medium">{item.label}</span>
              </div>
              {item.badge && (
                <span
                  className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    activeView === item.id ? 'bg-orange-200 dark:bg-orange-500/30' : 'bg-muted text-muted-foreground'
                  }`}
                  style={activeView === item.id ? { color: 'var(--pill-orange)' } : undefined}
                >
                  {item.badge}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="p-3 border-t border-border">
          <button className="w-full px-3 py-2.5 rounded-lg flex items-center gap-3 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
            <Settings size={18} />
            <span className="text-sm font-medium">Settings</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Top Bar */}
        <header className="h-14 px-6 flex items-center justify-between border-b border-border bg-background shrink-0">
          <div className="flex items-center gap-4">
            <div className="relative">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                placeholder="Search..."
                className="w-64 h-9 pl-10 pr-4 bg-muted/50 border border-border rounded-lg text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button className="relative p-2 rounded-lg hover:bg-muted transition-colors">
              <Bell size={18} className="text-muted-foreground" />
              <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-amber-500 rounded-full" />
            </button>
            <div className="w-px h-6 bg-border" />
            <button className="flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-muted transition-colors">
              <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center text-xs font-semibold text-white">JR</div>
              <span className="text-sm font-medium text-foreground">J. Richardson</span>
            </button>
          </div>
        </header>

        {/* Templates View */}
        {activeView === 'templates' && (
          <div className="flex-1 overflow-auto p-6">
            <div className="max-w-4xl mx-auto">
              {/* Header */}
              <div className="flex items-center justify-between mb-6">
                <div>
                  <h1 className="text-2xl font-semibold text-foreground">Document Templates</h1>
                  <p className="text-sm text-muted-foreground mt-1">
                    Upload and manage DOCX templates with content controls
                  </p>
                </div>
                <div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".docx"
                    onChange={handleUpload}
                    className="hidden"
                    id="template-upload"
                  />
                  <label
                    htmlFor="template-upload"
                    className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold cursor-pointer transition-colors ${
                      uploading
                        ? 'bg-muted text-muted-foreground cursor-not-allowed'
                        : 'bg-gradient-to-r from-amber-500 to-amber-600 text-white hover:from-amber-600 hover:to-amber-700'
                    }`}
                  >
                    {uploading ? (
                      <>
                        <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                        Uploading...
                      </>
                    ) : (
                      <>
                        <Upload size={16} />
                        Upload Template
                      </>
                    )}
                  </label>
                </div>
              </div>

              {/* Upload Error */}
              {uploadError && (
                <div className="mb-6 p-4 bg-red-100 dark:bg-red-900/30 border border-red-300 dark:border-red-800 rounded-lg text-red-700 dark:text-red-300 text-sm">
                  {uploadError}
                </div>
              )}

              {/* Templates Grid */}
              {templatesLoading ? (
                <div className="flex items-center justify-center py-12">
                  <div className="w-8 h-8 border-2 border-amber-500 border-t-transparent rounded-full animate-spin" />
                </div>
              ) : templates.length === 0 ? (
                <div className="text-center py-16 bg-card rounded-lg border border-border">
                  <LayoutTemplate size={48} className="mx-auto text-muted-foreground mb-4" />
                  <h3 className="text-lg font-medium text-foreground mb-2">No templates yet</h3>
                  <p className="text-sm text-muted-foreground mb-6">
                    Upload a DOCX file with content controls to get started
                  </p>
                  <label
                    htmlFor="template-upload"
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-muted text-foreground hover:bg-muted/80 cursor-pointer transition-colors text-sm font-medium"
                  >
                    <Plus size={16} />
                    Add Your First Template
                  </label>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {templates.map((template) => (
                    <div
                      key={template.filename}
                      className="bg-card rounded-lg border border-border p-5 hover:border-amber-500/50 transition-colors group"
                    >
                      <div className="flex items-start justify-between mb-3">
                        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center">
                          <FileText size={20} className="text-white" />
                        </div>
                        <button
                          onClick={() => handleDelete(template.filename)}
                          className="p-1.5 rounded-md text-muted-foreground hover:text-red-500 hover:bg-red-100 dark:hover:bg-red-900/30 opacity-0 group-hover:opacity-100 transition-all"
                          title="Delete template"
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                      <h3 className="font-medium text-foreground mb-1 truncate" title={template.name}>
                        {template.name}
                      </h3>
                      <p className="text-xs text-muted-foreground mb-4">{template.filename}</p>
                      <div className="flex items-center gap-2">
                        <a
                          href={`/api/docuform/documents/${template.filename}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex-1 py-2 rounded-md bg-muted text-center text-sm font-medium text-foreground hover:bg-muted/80 transition-colors"
                        >
                          Download
                        </a>
                        <button className="flex-1 py-2 rounded-md bg-amber-500/20 text-center text-sm font-medium hover:bg-amber-500/30 transition-colors" style={{ color: 'var(--pill-orange)' }}>
                          Use Template
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Document Review View */}
        {activeView === 'review' && (
        <div className="flex-1 flex overflow-hidden">
          {/* Document Preview Panel */}
          <div className="flex-1 flex flex-col border-r border-border min-w-0">
            {/* Document Header */}
            <div className="px-6 py-4 border-b border-border bg-card shrink-0">
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-3 mb-2">
                    <span
                      className="text-xs font-semibold px-2.5 py-1 rounded-md bg-orange-200 dark:bg-orange-500/30"
                      style={{ color: 'var(--pill-orange)' }}
                    >
                      {pendingFields.length} Pending Review
                    </span>
                    <span className="text-xs text-muted-foreground">Updated 2h ago</span>
                  </div>
                  <h1 className="text-xl font-semibold text-foreground">Shareholder Agreement — Acme Corp</h1>
                  <div className="flex items-center gap-4 mt-1.5 text-sm text-muted-foreground">
                    <span>Client: <span className="text-foreground font-medium">Acme Corporation</span></span>
                    <span>•</span>
                    <span>Template: <span className="text-foreground font-medium">Shareholder Agreement v2.3</span></span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button className="px-4 py-2 rounded-lg border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors flex items-center gap-2">
                    <Eye size={16} />
                    Compare
                  </button>
                  <button className="px-4 py-2 rounded-lg border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors flex items-center gap-2">
                    <Download size={16} />
                    Export
                  </button>
                </div>
              </div>
            </div>

            {/* Document Content */}
            <div className="flex-1 overflow-auto p-8 bg-muted/10">
              <div className="max-w-3xl mx-auto bg-card rounded-lg shadow-lg border border-border">
                <div className="p-12 text-foreground" style={{ fontFamily: 'Georgia, "Times New Roman", serif' }}>
                  <div className="text-center mb-10">
                    <h2 className="text-2xl font-bold tracking-tight mb-2">SHAREHOLDER AGREEMENT</h2>
                    <p className="text-sm text-muted-foreground">Confidential</p>
                  </div>

                  <p className="text-base leading-8 mb-8">
                    This Shareholder Agreement (this "<strong>Agreement</strong>") is entered into as of{' '}
                    <span className="bg-emerald-200 dark:bg-emerald-500/30 px-1.5 py-0.5 rounded border-b-2 border-emerald-500">March 15, 2025</span>
                    , by and among{' '}
                    <span
                      className={`${getHighlightClass(getFieldSource('company_name'))} px-1.5 py-0.5 rounded cursor-pointer hover:opacity-80 transition-opacity`}
                      onClick={() => toggleField('company_name')}
                    >
                      Acme Corporation
                    </span>
                    , a{' '}
                    <span
                      className={`${getHighlightClass(getFieldSource('jurisdiction'))} px-1.5 py-0.5 rounded cursor-pointer hover:opacity-80 transition-opacity`}
                      onClick={() => toggleField('jurisdiction')}
                    >
                      Delaware
                    </span>
                    {' '}corporation (the "<strong>Company</strong>"), and the shareholders listed on{' '}
                    <strong>Schedule A</strong> attached hereto.
                  </p>

                  <div className="mb-10">
                    <h3 className="text-lg font-bold mb-4">RECITALS</h3>
                    <p className="text-base leading-8 mb-4">
                      <strong>WHEREAS</strong>, the Company desires to establish certain rights and obligations
                      among its shareholders regarding the governance and operation of the Company;
                    </p>
                    <p className="text-base leading-8">
                      <strong>WHEREAS</strong>,{' '}
                      <span
                        className={`${getHighlightClass(getFieldSource('recital_transfer_terms'))} px-1.5 py-0.5 rounded cursor-pointer hover:opacity-80 transition-opacity`}
                        onClick={() => toggleField('recital_transfer_terms')}
                      >
                        the parties wish to define the terms under which shares may be transferred, including
                        rights of first refusal, co-sale rights, and drag-along provisions, to ensure an orderly
                        transfer process and protect minority shareholders
                      </span>
                      ;
                    </p>
                  </div>

                  <div className="mb-10">
                    <h3 className="text-lg font-bold mb-4">ARTICLE I: DEFINITIONS</h3>
                    <p className="text-base leading-8 mb-4">
                      <strong>1.1 "Affiliate"</strong> means{' '}
                      <span
                        className={`${getHighlightClass(getFieldSource('def_affiliate'))} px-1.5 py-0.5 rounded cursor-pointer hover:opacity-80 transition-opacity`}
                        onClick={() => toggleField('def_affiliate')}
                      >
                        any person or entity that directly or indirectly controls, is controlled by, or is under
                        common control with another person or entity. For purposes of this definition, "control"
                        means the possession, directly or indirectly, of the power to direct or cause the
                        direction of management and policies
                      </span>
                      .
                    </p>
                    <p className="text-base leading-8 mb-4">
                      <strong>1.2 "Board"</strong> means the Board of Directors of the Company.
                    </p>
                    <p className="text-base leading-8">
                      <strong>1.3 "Investment Amount"</strong> means{' '}
                      <span
                        className={`${getHighlightClass(getFieldSource('investment_amount'))} px-1.5 py-0.5 rounded cursor-pointer hover:opacity-80 transition-opacity`}
                        onClick={() => toggleField('investment_amount')}
                      >
                        $500,000
                      </span>
                      .
                    </p>
                  </div>

                  <div>
                    <h3 className="text-lg font-bold mb-4">ARTICLE II: TRANSFER RESTRICTIONS</h3>
                    <p className="text-base leading-8">
                      <strong>2.1 General Restriction.</strong>{' '}
                      <span
                        className={`${getHighlightClass(getFieldSource('transfer_restriction'))} px-1.5 py-0.5 rounded cursor-pointer hover:opacity-80 transition-opacity`}
                        onClick={() => toggleField('transfer_restriction')}
                      >
                        No Shareholder shall sell, assign, transfer, pledge, hypothecate, or otherwise dispose
                        of any Shares or any interest therein, whether voluntarily, by operation of law, or
                        otherwise, except in compliance with the terms of this Agreement. Any purported transfer
                        in violation of this Section 2.1 shall be null and void ab initio
                      </span>
                      .
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Document Legend */}
            <div className="px-6 py-3 border-t border-border bg-card flex items-center gap-6 text-xs shrink-0">
              <div className="flex items-center gap-2">
                <span className="w-4 h-4 rounded bg-violet-200 dark:bg-violet-500/30 border-2 border-violet-600" />
                <span className="text-muted-foreground font-medium">AI Generated</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-4 h-4 rounded bg-blue-200 dark:bg-blue-500/30 border-2 border-blue-600" />
                <span className="text-muted-foreground font-medium">Client Input</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-4 h-4 rounded bg-emerald-200 dark:bg-emerald-500/30 border-2 border-emerald-600" />
                <span className="text-muted-foreground font-medium">Attorney Input</span>
              </div>
            </div>
          </div>

          {/* Review Panel */}
          <div className="w-96 flex flex-col bg-card shrink-0">
            {/* Summary Bar */}
            <div className="px-5 py-4 border-b border-border flex items-center justify-between shrink-0">
              <h2 className="text-base font-semibold text-foreground">Field Review</h2>
              <span className="text-sm text-muted-foreground">
                <span className="text-foreground font-semibold">{reviewedFields.length}</span>/{fields.length} reviewed
              </span>
            </div>

            {/* Field Lists */}
            <div className="flex-1 overflow-auto">
              {/* Pending Fields */}
              <div className="border-b border-border">
                <div className="px-5 py-3 bg-muted/30 border-b border-border sticky top-0 z-10">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-amber-500" />
                      <span className="text-sm font-medium text-foreground">Pending Review</span>
                    </div>
                    <span className="text-xs px-2 py-0.5 rounded-md font-semibold bg-orange-200 dark:bg-orange-500/30" style={{ color: 'var(--pill-orange)' }}>{pendingFields.length}</span>
                  </div>
                </div>
                {pendingFields.length === 0 ? (
                  <div className="px-5 py-12 text-center">
                    <Check size={28} className="mx-auto text-emerald-500 mb-3" />
                    <p className="text-sm text-muted-foreground">All fields reviewed</p>
                  </div>
                ) : (
                  pendingFields.map(field => (
                    <FieldRow
                      key={field.id}
                      field={field}
                      isExpanded={expandedField === field.id}
                      onToggle={() => toggleField(field.id)}
                      onApprove={() => approveField(field.id)}
                      onMarkNeedsReview={() => markNeedsReview(field.id)}
                      onUpdateValue={(value) => updateFieldValue(field.id, value)}
                      onRegenerate={() => regenerateField(field.id)}
                    />
                  ))
                )}
              </div>

              {/* Reviewed Fields */}
              <div>
                <div className="px-5 py-3 bg-muted/30 border-b border-border sticky top-0 z-10">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-emerald-500" />
                      <span className="text-sm font-medium text-foreground">Reviewed</span>
                    </div>
                    <span className="text-xs px-2 py-0.5 rounded-md font-semibold bg-emerald-200 dark:bg-emerald-500/30" style={{ color: 'var(--pill-emerald)' }}>{reviewedFields.length}</span>
                  </div>
                </div>
                {reviewedFields.map(field => (
                  <FieldRow
                    key={field.id}
                    field={field}
                    isExpanded={expandedField === field.id}
                    onToggle={() => toggleField(field.id)}
                    onApprove={() => approveField(field.id)}
                    onMarkNeedsReview={() => markNeedsReview(field.id)}
                    onUpdateValue={(value) => updateFieldValue(field.id, value)}
                    onRegenerate={() => regenerateField(field.id)}
                  />
                ))}
              </div>
            </div>

            {/* Finalize Bar */}
            <div className="px-5 py-5 border-t border-border bg-background shrink-0">
              <button
                disabled={pendingFields.length > 0}
                className={`w-full py-3 rounded-lg text-sm font-semibold flex items-center justify-center gap-2 transition-all ${
                  pendingFields.length > 0
                    ? 'bg-muted text-muted-foreground cursor-not-allowed'
                    : 'bg-gradient-to-r from-amber-500 to-amber-600 text-white hover:from-amber-600 hover:to-amber-700 shadow-lg'
                }`}
              >
                <FileCheck size={18} />
                Finalize Document
              </button>
              {pendingFields.length > 0 && (
                <p className="text-xs text-center text-muted-foreground mt-3">
                  Review {pendingFields.length} pending field{pendingFields.length !== 1 ? 's' : ''} to finalize
                </p>
              )}
            </div>
          </div>
        </div>
        )}
      </main>
    </div>
  );
}

function SourcePill({ source, originalSource }: SourcePillProps) {
  const config: Record<FieldSource, { icon: LucideIcon; label: string; bg: string; colorVar: string }> = {
    ai: { icon: Sparkles, label: 'AI Generated', bg: 'bg-violet-200 dark:bg-violet-500/30', colorVar: '--pill-violet' },
    client: { icon: Users, label: 'Client Input', bg: 'bg-blue-200 dark:bg-blue-500/30', colorVar: '--pill-blue' },
    attorney: { icon: Scale, label: 'Attorney Input', bg: 'bg-emerald-200 dark:bg-emerald-500/30', colorVar: '--pill-emerald' },
  };
  const c = config[source];
  const Icon = c.icon;
  const originalConfig = originalSource ? config[originalSource] : null;

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium ${c.bg}`}
      style={{ color: `var(${c.colorVar})` }}
    >
      <Icon size={12} />
      {c.label}
      {originalSource && originalConfig && (
        <span className="text-muted-foreground line-through opacity-60 ml-1">
          {originalConfig.label.split(' ')[0]}
        </span>
      )}
    </span>
  );
}

function FieldRow({ field, isExpanded, onToggle, onApprove, onMarkNeedsReview, onUpdateValue, onRegenerate }: FieldRowProps) {
  const isPending = field.status === 'pending';

  return (
    <div className={`border-b border-border last:border-b-0 ${isExpanded ? 'bg-muted/20' : ''}`}>
      <button
        onClick={onToggle}
        className="w-full px-5 py-4 flex items-center gap-3 hover:bg-muted/20 transition-colors text-left"
      >
        <ChevronRight
          size={18}
          className={`text-muted-foreground transition-transform shrink-0 ${isExpanded ? 'rotate-90' : ''}`}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="text-sm font-medium text-foreground">{field.title}</span>
            <SourcePill source={field.source} originalSource={field.originalSource} />
          </div>
          <div className="text-xs text-muted-foreground mt-1">{field.section}</div>
        </div>
        {!isPending && (
          <Check size={18} className="text-emerald-500 shrink-0" />
        )}
      </button>

      {isExpanded && (
        <div className="px-5 pb-5 pt-1">
          {/* AI Context (only for AI fields) */}
          {(field.source === 'ai' || field.originalSource === 'ai') && field.aiContext && (
            <div className="bg-muted/30 rounded-lg p-4 mb-4 border border-border">
              <div className="flex items-center gap-2 mb-3">
                <Shield size={14} className="text-muted-foreground" />
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Generation Context</span>
              </div>
              <div className="space-y-2 text-sm">
                <div>
                  <span className="text-muted-foreground">Goal:</span>
                  <span className="text-foreground ml-2">{field.aiContext.goal}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Jurisdiction:</span>
                  <span className="text-foreground ml-2">{field.aiContext.jurisdiction}</span>
                </div>
                {field.aiContext.inputs.length > 0 && (
                  <div>
                    <span className="text-muted-foreground">Inputs:</span>
                    <span className="text-foreground ml-2">{field.aiContext.inputs.join(', ')}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Derived info */}
          {field.derivedFrom && (
            <div className="bg-muted/30 rounded-lg p-4 mb-4 border border-border">
              <div className="flex items-center gap-2 mb-2">
                <Zap size={14} className="text-muted-foreground" />
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Derived From</span>
              </div>
              <span className="text-sm text-foreground">{field.derivedFrom}</span>
            </div>
          )}

          {/* Value */}
          <div className="mb-4">
            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2 block">Value</label>
            <textarea
              className="w-full bg-background border border-border rounded-lg p-4 text-sm text-foreground resize-none focus:outline-none focus:ring-2 focus:ring-ring leading-relaxed"
              rows={field.value.length > 100 ? 4 : 2}
              value={field.value}
              onChange={(e) => onUpdateValue(e.target.value)}
            />
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            {isPending ? (
              <>
                <button
                  onClick={onApprove}
                  className="flex-1 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold transition-colors flex items-center justify-center gap-2"
                >
                  <Check size={16} />
                  Approve
                </button>
                {(field.source === 'ai' || field.originalSource === 'ai') && (
                  <button
                    onClick={onRegenerate}
                    className="px-4 py-2.5 rounded-lg border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors flex items-center gap-2"
                  >
                    <RotateCcw size={16} />
                    Regenerate
                  </button>
                )}
              </>
            ) : (
              <button
                onClick={onMarkNeedsReview}
                className="flex-1 py-2.5 rounded-lg border border-border text-sm font-medium text-muted-foreground hover:bg-muted transition-colors flex items-center justify-center gap-2"
              >
                <Undo2 size={16} />
                Mark Needs Review
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
