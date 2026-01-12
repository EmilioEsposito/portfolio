# Docuform: AI-Assisted Legal Document Generation Platform

## Business Context

### The Problem

Lawyers generate legal documents from Word templates using static placeholders, brittle mail merge, and manual copy/paste from client questionnaires. This breaks down when:

- Documents require conditional or context-dependent language
- Clauses vary by jurisdiction or facts
- Lawyers need to review asynchronously (days later)
- AI is introduced but feels unsafe, opaque, or hard to control

Existing tools either over-automate (black-box AI drafting entire documents) or under-automate (simple form fills with no intelligence).

### The Solution

A **lawyer-first web application** that:

1. Uses Microsoft Word content controls as stable anchors in DOCX templates
2. Maps anchors to client questionnaire data and/or AI-assisted generation
3. Auto-generates a draft when a client submits data
4. Provides a **structured, auditable review UI** where lawyers approve, edit, or regenerate AI content
5. Preserves lawyer control, async workflows, and Word compatibility

**Key principle**: AI is scoped to individual fields, not entire documents. No document finalizes without explicit lawyer review.

### Target Users

- Small to mid-size law firms
- Solo practitioners scaling intake
- Legal teams wanting automation without losing control
- Lawyers who live in Word and don't want to abandon it

---

## Data Model

### Field

Each fillable slot in a document template:

```
Field {
  id: string              // Machine identifier (matches Word content control tag)
  title: string           // Human-readable label
  section: string         // Document section (e.g., "Article I", "Recitals")
  source: 'ai' | 'client' | 'attorney'
  originalSource?: 'ai' | 'client'  // Tracks original source when attorney overrides
  status: 'pending' | 'reviewed'
  value: string           // Current content
  originalValue?: string  // For tracking edits
  edited?: boolean        // True if user modified content

  // AI-specific metadata (only when source === 'ai')
  aiContext?: {
    goal: string          // What the AI was asked to generate
    jurisdiction: string  // Legal jurisdiction for compliance
    inputs: string[]      // Other field IDs used as context
  }
}
```

### Orthogonal Concepts

The UI deliberately separates two independent dimensions:

| Dimension | Values | Meaning |
|-----------|--------|---------|
| **Status** (workflow) | `pending`, `reviewed` | Has the lawyer approved this field? |
| **Source** (provenance) | `ai`, `client`, `attorney` | Where did the value come from? |

This separation means:
- An AI field can be reviewed (approved)
- A client field can be pending (needs verification)
- Source pills persist regardless of status

### Source Override Behavior

When an attorney edits a field's value:
1. The source changes to `attorney`
2. The original source (`ai` or `client`) is preserved in `originalSource`
3. The pill displays the new source with the original source shown as strikethrough
4. If the attorney reverts to the original value, the source reverts back

Clicking "Regenerate" on an AI field keeps the source as `ai` (it's still AI-generated content).

---

## UI Architecture

### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ Sidebar (240px)          │ Top Bar                              │
│ ─────────────────────────┼──────────────────────────────────────│
│ • Logo                   │ Search │ Notifications │ User Avatar │
│ • Workspace Selector     ├──────────────────────────────────────│
│ • Nav:                   │                                      │
│   - Dashboard            │  Document Preview    │  Review Panel │
│   - Documents            │  (flex-1)            │  (380px)      │
│   - Templates            │                      │               │
│   - Clients              │  ┌─────────────┐     │  ┌──────────┐ │
│   - Review Queue ←active │  │ DOCX render │     │  │ Pending  │ │
│ • Settings               │  │ with inline │     │  │ fields   │ │
│                          │  │ highlights  │     │  ├──────────┤ │
│                          │  └─────────────┘     │  │ Reviewed │ │
│                          │                      │  │ fields   │ │
│                          │  Legend bar          │  ├──────────┤ │
│                          │                      │  │ Finalize │ │
└──────────────────────────┴──────────────────────┴──────────────┘
```

### Document Preview (Left Panel)

- Renders document as styled HTML mimicking Word/paper aesthetic
- Inline highlights indicate field source:
  - Violet background + border = AI-generated
  - Blue background + border = Client data
  - Emerald/green background + border = Attorney input (most authoritative)
- Clicking any highlighted span selects the corresponding field in the Review Panel
- Legend bar at bottom explains color coding

### Review Panel (Right Panel)

Two collapsible sections with sticky headers:

**1. Pending Review**
- Orange dot indicator
- Count badge
- Lists all fields where `status === 'pending'`
- Empty state shows checkmark when all reviewed

**2. Reviewed**
- Green dot indicator
- Count badge
- Lists all fields where `status === 'reviewed'`

### Field Row (Collapsed)

```
[▶] Field Title    [Source Pill]    [✓ if reviewed]
    Section name
```

### Field Row (Expanded)

```
[▼] Field Title    [Source Pill]
    Section name
    
    ┌─ Generation Context (AI only) ─────────────┐
    │ Goal: ...                                   │
    │ Jurisdiction: ...                           │
    │ Inputs: field1, field2                      │
    └─────────────────────────────────────────────┘
    
    ┌─ Value ────────────────────────────────────┐
    │ [editable textarea]                        │
    └─────────────────────────────────────────────┘
    
    [Approve] [Regenerate]     ← if pending
    [Mark Needs Review]        ← if reviewed
```

### Source Pills

Persistent badges showing field provenance:

| Source | Color | Icon |
|--------|-------|------|
| AI Generated | Violet | Sparkles |
| Client Input | Blue | Users |
| Attorney Input | Emerald/Green | Scale |

When attorney edits a field, the pill shows "Attorney Input" with the original source (e.g., "AI") displayed as strikethrough text.

### Finalize Bar

- Disabled until `pendingFields.length === 0`
- Shows count of remaining pending fields
- Gold/bronze gradient when active

---

## Interaction Flows

### Approve a Field
1. Expand pending field
2. (Optional) Edit value in textarea
3. Click "Approve"
4. Field moves from Pending → Reviewed section
5. Progress updates

### Regenerate AI Content
1. Expand pending AI field
2. Click "Regenerate"
3. System re-runs AI with same context
4. New value appears in textarea
5. Lawyer reviews and approves

### Send Back for Review
1. Expand reviewed field
2. Click "Mark Needs Review"
3. Field moves from Reviewed → Pending section

### Finalize Document
1. All fields must be reviewed (pending count = 0)
2. Click "Finalize Document"
3. System generates final DOCX
4. Document status changes, available for download

---

## Design System

### Colors

The UI supports both light and dark modes with inverted contrast patterns.

**Source Colors** (used for pills and highlights):

| Source | Light Mode | Dark Mode |
|--------|------------|-----------|
| AI Generated | Violet bg (#ddd6fe) + dark text (#7c3aed) | Violet bg (violet-500/30) + light text (#c4b5fd) |
| Client Input | Blue bg (#bfdbfe) + dark text (#2563eb) | Blue bg (blue-500/30) + light text (#93c5fd) |
| Attorney Input | Emerald bg (#a7f3d0) + dark text (#059669) | Emerald bg (emerald-500/30) + light text (#6ee7b7) |

**Status Colors**:
- Pending: Orange (#ea580c family)
- Reviewed: Emerald (#10b981 family)

**Semantic Colors** (from shadcn/ui):
- Background, foreground, card, border etc. are defined as CSS variables that swap between light/dark modes

### Typography

- UI: Inter or system sans-serif
- Document preview: Georgia (serif, for legal document feel)

### Spacing

- Compact: 4-8px padding on pills/badges
- Standard: 12-16px padding on cards/sections
- Document: 40px padding for paper aesthetic

---

## MVP Action Plan & Open Questions

### Critical Decisions Needed

#### 1. Document Preview Strategy

**Question**: Should we render DOCX files in the browser, or use a different approach?

| Option | Pros | Cons |
|--------|------|------|
| **A. Static HTML mockup** (current) | Simple, fast, full control over styling | Not real document, can't test with actual templates |
| **B. Server-side DOCX → HTML conversion** | Real document rendering, python-docx available | Complex, formatting loss, need to maintain converter |
| **C. PDF preview** | Accurate rendering, well-supported | Read-only, can't show interactive highlights easily |
| **D. Embedded Office Online** | Perfect fidelity | Requires Microsoft 365, complex auth, cost |
| **E. docxjs** ([GitHub](https://github.com/VolodymyrBaydalka/docxjs)) | Client-side, good fidelity, actively maintained, MIT license | Need to test with content controls, may need custom styling for highlights |

**Recommendation**: **Option E (docxjs)** - Client-side DOCX rendering with good fidelity. Key benefits:
- Renders directly from DOCX blob/URL
- Supports most Word formatting (tables, lists, styles)
- Can inject custom CSS for styling
- No server-side processing needed
- ~200KB bundle size

**Next steps**:
1. Spike: Test docxjs with a template containing content controls
2. Verify we can identify/style content control elements in the rendered HTML
3. If content controls aren't accessible, fall back to placeholder syntax or server-side pre-processing

#### 2. Content Control Strategy

**Question**: How do we identify fillable fields in Word templates?

| Option | Pros | Cons |
|--------|------|------|
| **A. Content Controls** | Native Word feature, semantic, accessible via python-docx | Requires template authors to know how to add them |
| **B. Placeholder syntax** `{{field_name}}` | Simple, familiar (like Jinja) | Brittle, conflicts with legal text using braces |
| **C. Custom XML Parts** | Powerful, used by enterprise tools | Complex, overkill for MVP |

**Recommendation**: **Option A** (Content Controls) - they're the right tool for this job. Create a simple guide for template authors.

#### 3. AI Integration Scope

**Question**: What should AI generate vs. what's just data mapping?

| Field Type | Example | AI Needed? |
|------------|---------|-----------|
| Direct client data | Company name, address | No - direct mapping |
| Computed values | Total investment amount | No - formula/code |
| Contextual clauses | Transfer restriction recital | Yes - needs legal reasoning |
| Jurisdiction-specific | Delaware vs. California language | Yes - needs legal knowledge |

**Recommendation**: Start with AI for contextual/legal clauses only. Direct mappings and computations should be deterministic.

#### 4. Backend Architecture

**Question**: Where does document processing happen?

```
Option A: All in FastAPI
- Template upload → python-docx parsing → store field definitions
- Client data submission → field population → AI calls → draft generation
- Review completion → final DOCX generation

Option B: Separate document service
- Dedicated microservice for DOCX manipulation
- FastAPI handles business logic, auth, AI orchestration
```

**Recommendation**: **Option A** for MVP. Keep it simple. Extract to service if/when we need to scale.

### MVP Feature Scope

#### Phase 1: Template Field Creation ✅
- [x] Upload DOCX documents
- [x] AI-powered Template Agent that analyzes documents
- [x] Create content controls (fillable fields) from existing text
- [x] Dot notation naming convention (`declarant.name`, `signing.date`)
- [x] Split fields (name → first/middle/last)
- [x] Live document preview with field highlighting
- [x] Save modified templates

#### Phase 2: Schema Inference & Form Generation (Current Focus)
- [ ] Infer field schema from template content controls
- [ ] AI classification of field sources (client/attorney/ai)
- [ ] Dynamic form generation from schema
- [ ] Client intake form UI
- [ ] Form submission → template population

#### Phase 3: Attorney Review Flow
- [x] Review UI mockup with pending/reviewed sections
- [x] Source tracking (AI, Client, Attorney)
- [x] Attorney override with original source tracking
- [ ] Connect to real backend API
- [ ] AI regeneration for AI-sourced fields
- [ ] Finalize and export document

---

## Schema Inference Architecture

### Overview

After creating template fields, we need to:
1. **Infer a schema** defining each field's type, source, and validation
2. **Generate dynamic forms** for client intake based on the schema
3. **Allow attorney override** of AI-suggested classifications

### Data Model

```python
class FieldSource(Enum):
    CLIENT = "client"      # Collected from client intake form
    ATTORNEY = "attorney"  # Entered by attorney during review
    AI = "ai"              # Generated by AI based on context

class FieldType(Enum):
    TEXT = "text"          # Short text input
    TEXTAREA = "textarea"  # Long text (paragraphs)
    DATE = "date"          # Date picker
    CURRENCY = "currency"  # Money amount with formatting
    EMAIL = "email"        # Email with validation
    PHONE = "phone"        # Phone number
    ADDRESS = "address"    # Multi-field address
    SELECT = "select"      # Dropdown with options
    NUMBER = "number"      # Numeric input

class FieldSchema:
    tag: str                    # Content control tag (e.g., "declarant.name")
    display_name: str           # Human-readable label (e.g., "Declarant Name")
    source: FieldSource         # Who provides this value
    field_type: FieldType       # Input type for form rendering
    group: str                  # Logical grouping (e.g., "Declarant Information")
    required: bool              # Is this field required?
    order: int                  # Display order within group

    # Validation
    min_length: int | None
    max_length: int | None
    pattern: str | None         # Regex pattern

    # For SELECT fields
    options: list[str] | None

    # For AI-sourced fields
    ai_prompt: str | None       # Prompt template for generation
    ai_context_fields: list[str] | None  # Other fields used as context

class TemplateSchema:
    template_id: str            # Unique identifier
    template_name: str          # Human-readable name
    version: str                # Schema version
    fields: list[FieldSchema]
    groups: list[FieldGroup]    # Ordered groups for form layout

    # Metadata
    created_at: datetime
    updated_at: datetime
    created_by: str             # Attorney who finalized schema

class FieldGroup:
    id: str
    name: str                   # e.g., "Declarant Information"
    description: str | None
    order: int
    collapsed_by_default: bool
```

### Architecture Options

#### Option A: Schema as Part of Template Agent Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│                    Template Agent Session                        │
├─────────────────────────────────────────────────────────────────┤
│ 1. Upload document                                               │
│ 2. "Detect and create fields" → creates content controls        │
│ 3. "Generate schema" → AI analyzes fields, suggests sources     │
│ 4. Attorney reviews/adjusts schema in chat                       │
│ 5. Save template + schema together                               │
└─────────────────────────────────────────────────────────────────┘
```

**Pros:**
- Single workflow, AI has full document context
- Conversational refinement of schema
- Natural extension of existing agent

**Cons:**
- Makes agent more complex
- Schema changes require re-running agent

#### Option B: Separate Schema Editor UI

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  Template Agent  │ →  │  Schema Editor   │ →  │  Client Intake   │
│  (create fields) │    │  (classify/edit) │    │  (fill form)     │
└──────────────────┘    └──────────────────┘    └──────────────────┘
```

**Pros:**
- Separation of concerns
- Visual schema editor with drag-drop grouping
- Can edit schema without AI agent

**Cons:**
- Extra step in workflow
- Need to build dedicated UI

#### Option C: Hybrid - Auto-Schema with UI Override

```
┌─────────────────────────────────────────────────────────────────┐
│                    Template Agent Session                        │
├─────────────────────────────────────────────────────────────────┤
│ 1. Create fields as normal                                       │
│ 2. On save, auto-generate schema using AI                        │
│ 3. Schema appears in side panel for review                       │
│ 4. Attorney can edit inline or use dedicated editor              │
└─────────────────────────────────────────────────────────────────┘
```

**Pros:**
- Best of both worlds
- Quick path for simple templates
- Full editor for complex ones

**Cons:**
- More UI surface area

**Recommendation: Option C** - Auto-generate schema on save, show in UI for override.

### AI Classification Strategy

The AI can infer field classification from multiple signals:

**1. Field Naming Convention**
| Pattern | Likely Source | Field Type |
|---------|---------------|------------|
| `*.name`, `*.first_name` | client | text |
| `*.date_of_birth`, `*.dob` | client | date |
| `*.address`, `*.city`, `*.state` | client | address/text |
| `*.email` | client | email |
| `*.phone` | client | phone |
| `signing.*` | attorney (date) or client (location) | varies |
| `clause.*`, `recital.*`, `definition.*` | ai | textarea |

**2. Placeholder Content Analysis**
| Placeholder | Inference |
|-------------|-----------|
| `[Name]`, `[Date]` | Simple client input |
| `[Insert clause...]` | AI-generated content |
| `[TBD]`, `[Attorney to complete]` | Attorney input |

**3. Document Context**
- Fields in signature blocks → often client or signing ceremony
- Fields in definitions section → often AI-generated
- Fields in schedules/exhibits → often client data

**4. Legal Pattern Recognition**
- Jurisdiction selections → attorney or client
- Defined terms → AI with attorney review
- Boilerplate clauses → AI

### Form Generation Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Template   │ →   │    Schema    │ →   │  Intake Form │
│   (DOCX)     │     │   (JSON)     │     │   (React)    │
└──────────────┘     └──────────────┘     └──────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────┐
│ Dynamic Form Groups:                                      │
│ ┌────────────────────────────────────────────────────┐   │
│ │ Declarant Information                              │   │
│ │  • First Name [________]                           │   │
│ │  • Middle Name [________]                          │   │
│ │  • Last Name [________]                            │   │
│ │  • Address [________]                              │   │
│ └────────────────────────────────────────────────────┘   │
│ ┌────────────────────────────────────────────────────┐   │
│ │ Spouse Information                                  │   │
│ │  • Spouse Name [________]                          │   │
│ └────────────────────────────────────────────────────┘   │
│ ┌────────────────────────────────────────────────────┐   │
│ │ Signing Details                                     │   │
│ │  • State [▼ Select]                                │   │
│ └────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

### End-to-End Workflow

```
ATTORNEY WORKFLOW:
1. Upload filled document (e.g., executed will)
2. Template Agent creates fields from actual values
3. Schema auto-generated with AI classification
4. Attorney reviews schema, adjusts sources
5. Save as template with schema

CLIENT WORKFLOW:
1. Attorney shares intake form link
2. Client sees dynamic form (only "client" source fields)
3. Client fills and submits
4. System populates template with client data
5. AI generates content for "ai" source fields

ATTORNEY REVIEW:
1. Attorney sees populated draft
2. Reviews AI-generated fields (approve/edit/regenerate)
3. Fills in "attorney" source fields
4. Finalizes document
```

### Storage Considerations (Future)

When we move to DB/cloud storage:

```python
class DocumentMetadata:
    id: str
    filename: str
    document_type: 'template' | 'draft' | 'final'
    storage_path: str          # S3/GCS path or blob reference

    # For templates
    schema: TemplateSchema | None

    # For drafts/finals
    source_template_id: str | None
    client_submission_id: str | None

    # Audit
    created_at: datetime
    updated_at: datetime
    created_by: str
    firm_id: str
```

Keep templates, schemas, and documents loosely coupled for now - store schema as JSON alongside template file. Migrate to proper DB when structure is proven.

### Technical Debt / Known Issues

- Documents stored as files on disk (need DB/cloud storage)
- Schema not yet persisted (stored as JSON sidecar files for now)
- Review UI is mockup with local state (not connected to backend)
- No multi-tenant isolation yet

---

## Future Considerations

### Not Yet Implemented

- Schema persistence and versioning
- Client intake form generation
- Multi-document batch review
- Diff/compare view against template
- Audit trail / version history
- User permissions and firm-level AI policies
- Word add-in for template tagging

### Potential Enhancements

- Keyboard navigation (j/k to move between fields, Enter to expand)
- Bulk approve all AI fields
- "Regenerate with different tone" option
- Jurisdiction-specific clause library
- Integration with practice management systems
- Conditional fields (show/hide based on other answers)
- Field dependencies and computed values
