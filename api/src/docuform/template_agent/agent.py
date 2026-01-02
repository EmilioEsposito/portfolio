"""
Template Field Detection Agent

An AI agent that analyzes DOCX templates and helps attorneys identify and create
fillable fields. The agent can detect placeholders, suggest field names, and
convert text into template fields.
"""
from __future__ import annotations

import logfire
from pathlib import Path
from dataclasses import dataclass, field
from docx import Document
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel

from api.src.docuform.docx_content_controls import (
    wrap_text_in_content_control,
    read_content_controls_detailed,
    set_content_control_value,
    delete_content_control,
    update_content_control,
)


# Directory for documents
DOCUMENTS_DIR = Path(__file__).parent.parent / "documents"

# Working copy retention (in seconds) - cleanup files older than this
WORKING_COPY_MAX_AGE_SECONDS = 3600  # 1 hour


def cleanup_old_working_copies() -> int:
    """
    Clean up old working copy files on module import.
    Removes working copies older than WORKING_COPY_MAX_AGE_SECONDS.
    Returns the number of files deleted.
    """
    import time
    deleted = 0
    now = time.time()

    # Match pattern: *_template_agent_working_*.docx (conversation-scoped working copies)
    for file in DOCUMENTS_DIR.glob("*_template_agent_working_*.docx"):
        try:
            age = now - file.stat().st_mtime
            if age > WORKING_COPY_MAX_AGE_SECONDS:
                file.unlink()
                deleted += 1
                logfire.info(f"Cleaned up old working copy: {file.name}", age_seconds=age)
        except Exception as e:
            logfire.warn(f"Failed to clean up {file.name}: {e}")

    if deleted > 0:
        logfire.info(f"Cleaned up {deleted} old working copies")

    return deleted


# Run cleanup on module import
cleanup_old_working_copies()


class DetectedField(BaseModel):
    """A field detected in the document"""
    text: str
    suggested_tag: str
    suggested_alias: str
    reason: str


class FieldDetectionResult(BaseModel):
    """Result of field detection analysis"""
    fields: list[DetectedField]
    summary: str


class FieldToCreate(BaseModel):
    """Input for creating a template field from text"""
    search_text: str  # The exact text to find and convert to a field
    tag: str  # The programmatic tag/key for the field (dot notation, e.g., "declarant.name")
    alias: str | None = None  # Human-readable display name (defaults to formatted tag)
    replace_with_placeholder: bool = True  # If True, replace text with "[tag]" placeholder


@dataclass
class TemplateAgentContext:
    """Context for the template agent, holding the working document state"""
    document_filename: str  # Required - the document to work with
    conversation_id: str | None = None  # Conversation ID for scoped working copies
    document: Document | None = field(default=None, repr=False)
    working_filename: str | None = None  # Temp file for modifications
    modifications: list[str] = field(default_factory=list)

    def get_document_path(self) -> Path:
        """Get the path to the current document"""
        return DOCUMENTS_DIR / self.document_filename

    def get_working_path(self) -> Path | None:
        """Get the path to the working copy (scoped by conversation_id if available)"""
        if self.working_filename:
            return DOCUMENTS_DIR / self.working_filename
        return None

    def load_document(self) -> tuple[bool, str]:
        """Load the document from filename. Returns (success, message).

        If a working copy exists for this conversation, loads from that to preserve
        previous modifications. Otherwise loads from the original document.
        Working copies are scoped by conversation_id to isolate multi-user scenarios.
        """
        original_path = self.get_document_path()
        if not original_path.exists():
            available = [f.name for f in DOCUMENTS_DIR.glob("*.docx")]
            return False, f"Document '{self.document_filename}' not found. Available: {available}"

        # Set working filename - scoped by conversation_id
        # Format: {stem}_template_agent_working_{short_id}.docx
        if self.conversation_id:
            # Use first 8 chars of conversation_id for shorter filenames
            short_id = self.conversation_id[:8]
            self.working_filename = f"{original_path.stem}_template_agent_working_{short_id}.docx"
        else:
            # Fallback for backwards compatibility (shouldn't happen in practice)
            self.working_filename = f"{original_path.stem}_template_agent_working.docx"

        working_path = self.get_working_path()

        try:
            # Load from working copy if it exists (preserves previous modifications)
            if working_path and working_path.exists():
                self.document = Document(str(working_path))
                return True, f"Loaded {self.document_filename} (with previous modifications)"
            else:
                self.document = Document(str(original_path))
                return True, f"Loaded {self.document_filename}"
        except Exception as e:
            return False, f"Error loading document: {str(e)}"


# Create the agent
model = OpenAIChatModel("gpt-4o")

agent = Agent(
    name="template_agent",
    model=model,
    deps_type=TemplateAgentContext,
    instructions="""You are a legal document template assistant. You help attorneys convert documents
into templates with fillable fields.

IMPORTANT: A document has been pre-selected and loaded for this session. At the START of EVERY conversation,
call `get_document_info` to see which document is loaded.

## Document Types You'll Encounter

1. **Existing templates** - Documents with placeholders like [Name], {{field}}, or blank lines.
   These just need the placeholders marked as template fields.

2. **Filled documents** - Real documents with actual names, dates, addresses, amounts.
   The user wants to CONVERT these into templates by replacing specific values with placeholders.
   Example: "Allen R. Moreland" → [declarant.name], "January 15, 2024" → [signing.date]

If you see a document with real names, dates, and addresses (not placeholders), ASK the user:
"This looks like a filled document rather than a template. Would you like me to help convert it
into a template by identifying values that should become fillable fields?"

## Your Tools

**Reading & Searching:**
- `get_document_info` - See loaded document info and preview
- `get_document_text` - Get the full document text for analysis
- `search_text` - Find all occurrences of specific text (useful for names that appear multiple times)
- `list_fields` - Show existing template fields
- `inspect_document_structure` - (Internal) Search entire document including tables, headers, footers
- `browse_document_segments` - (Internal) Browse all text segments when search fails

**Modifying:**
- `create_fields` - Convert text into template fields. Always use this tool for creating fields.
- `edit_field` - Edit an existing field's tag, display name, or value
- `delete_field` - Remove a field (optionally preserving its text)
- `update_field_value` - Quick way to change just the value of an existing field
- `reset_working_copy` - Discard all modifications

## Workflow for Converting Filled Documents

1. Use `get_document_text` to read the full content
2. Identify values that should become fields (names, dates, addresses, amounts)
3. Use `search_text` to find all occurrences of each value
4. Ask user to confirm which items to convert
5. Use `create_fields` to mark ALL confirmed fields in one call

## Tag Naming
ALWAYS use dot notation (object.property format) for clarity and consistency:

**People/Entities:**
- `declarant.name`, `declarant.address`, `declarant.date_of_birth`
- `spouse.name`, `spouse.address`
- `child1.name`, `child1.date_of_birth`, `child2.name`, `child2.date_of_birth`
- `witness1.name`, `witness1.address`, `witness2.name`
- `executor.name`, `executor.address`
- `beneficiary1.name`, `beneficiary1.share_percentage`
- `guardian.name`, `guardian.address`

**Dates/Events:**
- `signing.date`, `signing.state`, `signing.city`
- `document.effective_date`, `document.expiration_date`
- `closing.date`, `closing.location`

**Transactions/Amounts:**
- `purchase.price`, `purchase.deposit`
- `lease.start_date`, `lease.end_date`, `lease.monthly_rent`

Display names should be human-readable:
- `declarant.name` → "Declarant Name"
- `child1.date_of_birth` → "Child1 Date Of Birth"
- `signing.date` → "Signing Date"

## When Field Creation Fails
If create_fields can't find text, PROACTIVELY diagnose using these steps:

1. **Use `inspect_document_structure`** with the search text to search the ENTIRE document
   (body, tables, headers, footers). Text is often in tables, especially signature blocks.

2. **If still not found**, use `browse_document_segments` with location_filter="table" to
   browse table content, or no filter to see all segments.

3. **Try shorter substrings** - search for just a first name or part of a word.

4. **Once you locate the text**, note if it's fragmented across segments. If so, create
   fields for the individual fragments that exist in single segments.

IMPORTANT: Do this investigation automatically without asking the user. Only communicate
results in user-friendly terms like "I found it in a table" or "The text is split up,
so I'll create the field in parts."

Always explain what you're doing and ask for confirmation before making changes.
""",
    retries=2,
)


@agent.instructions
async def add_document_context(ctx: RunContext[TemplateAgentContext]) -> str:
    """Dynamic system prompt that loads and describes the document."""
    print(f"🔥 document_context CALLED with filename: {ctx.deps.document_filename}")
    try:
        logfire.info(f"document_context called with filename: {ctx.deps.document_filename}")

        # Load the document if not already loaded
        if ctx.deps.document is None:
            logfire.info(f"Loading document: {ctx.deps.document_filename}")
            success, message = ctx.deps.load_document()
            logfire.info(f"Load result: success={success}, message={message}")
            if not success:
                return f"ERROR: {message}. Please inform the user."

        # Get document stats
        doc = ctx.deps.document
        para_count = len(doc.paragraphs) if doc else 0
        controls = read_content_controls_detailed(str(ctx.deps.get_document_path()))

        # Get text preview (first 500 chars)
        text_preview = ""
        if doc:
            all_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            text_preview = all_text[:500] + ("..." if len(all_text) > 500 else "")

        result = f"""
The document "{ctx.deps.document_filename}" is already loaded and ready for analysis.

Document Info:
- Paragraphs: {para_count}
- Existing template fields: {len(controls)}

Text Preview:
{text_preview}

Start by analyzing this document for potential fields, or ask the user what they'd like to do.
"""
        logfire.info(f"document_context returning prompt (len={len(result)})")
        return result
    except Exception as e:
        logfire.error(f"Error in document_context: {e}", exc_info=True)
        return f"ERROR loading document context: {e}"


@agent.tool
async def get_document_info(ctx: RunContext[TemplateAgentContext]) -> str:
    """
    Get information about the currently loaded document.

    Call this at the START of every conversation to see which document is loaded
    and get a preview of its contents.

    Returns:
        Document filename, stats, and text preview
    """
    if ctx.deps.document is None:
        return f"Document '{ctx.deps.document_filename}' is set but not loaded. This is unexpected - contact support."

    doc = ctx.deps.document
    para_count = len(doc.paragraphs)
    controls = read_content_controls_detailed(str(ctx.deps.get_document_path()))

    # Get text preview (first 1000 chars)
    all_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    text_preview = all_text[:1000] + ("..." if len(all_text) > 1000 else "")

    logfire.info(f"get_document_info called", filename=ctx.deps.document_filename, para_count=para_count)

    return f"""Document: {ctx.deps.document_filename}

Stats:
- Paragraphs: {para_count}
- Existing template fields: {len(controls)}

Text Preview:
{text_preview}

Existing template fields:
{_format_fields(controls) if controls else "None"}

You can now analyze this document for potential fields, or ask what the user wants to do."""


@agent.tool
async def load_document(ctx: RunContext[TemplateAgentContext], filename: str) -> str:
    """
    Load a DOCX document for analysis and modification.

    Args:
        filename: Name of the document file (e.g., "contract.docx")

    Returns:
        Status message with document info
    """
    file_path = DOCUMENTS_DIR / filename

    if not file_path.exists():
        available = [f.name for f in DOCUMENTS_DIR.glob("*.docx")]
        return f"Document '{filename}' not found. Available documents: {available}"

    try:
        doc = Document(str(file_path))
        ctx.deps.document = doc
        ctx.deps.document_filename = filename

        # Create working copy filename
        stem = file_path.stem
        ctx.deps.working_filename = f"{stem}_working.docx"

        # Count paragraphs and existing controls
        para_count = len(doc.paragraphs)
        controls = read_content_controls_detailed(str(file_path))

        # Get text preview
        text_preview = "\n".join([p.text for p in doc.paragraphs[:10] if p.text.strip()])
        if len(doc.paragraphs) > 10:
            text_preview += "\n..."

        logfire.info(f"Loaded document: {filename}", para_count=para_count, control_count=len(controls))

        return f"""Document loaded: {filename}
- Paragraphs: {para_count}
- Existing template fields: {len(controls)}

Preview:
{text_preview}

Existing template fields:
{_format_fields(controls) if controls else "None"}"""

    except Exception as e:
        logfire.error(f"Failed to load document: {e}")
        return f"Error loading document: {str(e)}"


@agent.tool
async def get_document_text(ctx: RunContext[TemplateAgentContext], max_chars: int = 10000) -> str:
    """
    Get the full text content of the document for analysis.

    Use this to read the document content and identify potential fields yourself.
    Look for things like names, dates, addresses, dollar amounts, or any text
    that should be replaceable in a template.

    Args:
        max_chars: Maximum characters to return (default 10000). Use higher values for longer documents.

    Returns:
        The document text content
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    doc = ctx.deps.document
    all_text = "\n\n".join([p.text for p in doc.paragraphs if p.text.strip()])

    if len(all_text) > max_chars:
        return all_text[:max_chars] + f"\n\n... (truncated, {len(all_text) - max_chars} more characters)"

    return all_text


@agent.tool
async def search_text(
    ctx: RunContext[TemplateAgentContext],
    query: str,
    case_sensitive: bool = False,
    context_before: int = 50,
    context_after: int = 50,
) -> str:
    """
    Search for text in the document. Returns all occurrences with context.

    Use this to find specific text (like a person's name) that appears in the document,
    which you can then convert into a template field. You can also use this to read sections
    of a long document by searching for a heading and using large context_after values.

    Args:
        query: The text to search for
        case_sensitive: Whether the search should be case-sensitive (default: False)
        context_before: Characters to show before each match (default: 50, max: 2000)
        context_after: Characters to show after each match (default: 50, max: 2000)

    Returns:
        List of matches with surrounding context
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    # Clamp context values
    context_before = max(0, min(context_before, 2000))
    context_after = max(0, min(context_after, 2000))

    doc = ctx.deps.document
    all_text = "\n".join([p.text for p in doc.paragraphs])

    if not case_sensitive:
        search_text_lower = query.lower()
        matches = []
        pos = 0
        while True:
            idx = all_text.lower().find(search_text_lower, pos)
            if idx == -1:
                break
            # Get the actual text (preserving case)
            actual_text = all_text[idx:idx + len(query)]
            # Get context
            start = max(0, idx - context_before)
            end = min(len(all_text), idx + len(query) + context_after)
            context = all_text[start:end]
            if start > 0:
                context = "..." + context
            if end < len(all_text):
                context = context + "..."
            matches.append({
                "text": actual_text,
                "context": context.replace("\n", " "),
                "position": idx,
            })
            pos = idx + 1
    else:
        matches = []
        pos = 0
        while True:
            idx = all_text.find(query, pos)
            if idx == -1:
                break
            start = max(0, idx - context_before)
            end = min(len(all_text), idx + len(query) + context_after)
            context = all_text[start:end]
            if start > 0:
                context = "..." + context
            if end < len(all_text):
                context = context + "..."
            matches.append({
                "text": query,
                "context": context.replace("\n", " "),
                "position": idx,
            })
            pos = idx + 1

    if not matches:
        return f"No matches found for '{query}'"

    result = f"Found {len(matches)} occurrence(s) of '{query}':\n\n"
    for i, m in enumerate(matches, 1):
        result += f"{i}. \"{m['text']}\" (position: {m['position']})\n"
        result += f"   Context: {m['context']}\n\n"

    return result


@agent.tool
async def list_fields(ctx: RunContext[TemplateAgentContext]) -> str:
    """
    List all existing template fields in the document.

    Returns:
        List of fields with their tags, display names, and current values
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    # Read from working copy if it exists, otherwise from original
    working_path = ctx.deps.get_working_path()
    if working_path and working_path.exists():
        controls = read_content_controls_detailed(str(working_path))
    else:
        controls = read_content_controls_detailed(str(ctx.deps.get_document_path()))

    if not controls:
        return "No template fields found in the document."

    result = f"Found {len(controls)} template field(s):\n\n"
    for i, c in enumerate(controls, 1):
        result += f"{i}. Tag: {c['tag']}\n"
        result += f"   Display Name: {c['alias']}\n"
        result += f"   Current Value: \"{c['value']}\"\n\n"

    return result


def _collect_all_runs(doc: Document) -> list[tuple[str, str, any]]:
    """
    Collect ALL text runs from the entire document, including:
    - Body paragraphs
    - Tables (all cells)
    - Headers and footers

    Returns list of (location, text, run_obj) tuples.
    """
    all_runs: list[tuple[str, str, any]] = []  # (location, text, run_obj)

    # Body paragraphs
    for para_idx, para in enumerate(doc.paragraphs):
        for run_idx, run in enumerate(para.runs):
            if run.text:
                all_runs.append((f"body:p{para_idx}:r{run_idx}", run.text, run))

    # Tables
    for table_idx, table in enumerate(doc.tables):
        for row_idx, row in enumerate(table.rows):
            for cell_idx, cell in enumerate(row.cells):
                for para_idx, para in enumerate(cell.paragraphs):
                    for run_idx, run in enumerate(para.runs):
                        if run.text:
                            loc = f"table{table_idx}:row{row_idx}:cell{cell_idx}:p{para_idx}:r{run_idx}"
                            all_runs.append((loc, run.text, run))

    # Headers and footers (from all sections)
    for section_idx, section in enumerate(doc.sections):
        # Header
        try:
            header = section.header
            if header:
                for para_idx, para in enumerate(header.paragraphs):
                    for run_idx, run in enumerate(para.runs):
                        if run.text:
                            loc = f"header{section_idx}:p{para_idx}:r{run_idx}"
                            all_runs.append((loc, run.text, run))
        except Exception:
            pass

        # Footer
        try:
            footer = section.footer
            if footer:
                for para_idx, para in enumerate(footer.paragraphs):
                    for run_idx, run in enumerate(para.runs):
                        if run.text:
                            loc = f"footer{section_idx}:p{para_idx}:r{run_idx}"
                            all_runs.append((loc, run.text, run))
        except Exception:
            pass

    return all_runs


@agent.tool
async def inspect_document_structure(
    ctx: RunContext[TemplateAgentContext],
    search_text: str | None = None,
    context_segments: int = 5,
    max_results: int = 10,
) -> str:
    """
    (Internal diagnostic tool) Inspect the full document structure to find text.

    Searches the ENTIRE document including body text, tables, headers, and footers.
    Use this when create_fields fails - text might be in a table cell or fragmented.

    DO NOT expose technical details to the user. Use this information internally
    to identify text fragments and suggest alternatives.

    Args:
        search_text: Text to search for (case-insensitive). If None, lists all segments.
        context_segments: Number of segments to show around each match (default: 5)
        max_results: Maximum results to return (default: 10)

    Returns:
        Document structure showing where text is located
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    doc = ctx.deps.document
    all_runs = _collect_all_runs(doc)

    if not all_runs:
        return "Document has no text content."

    # If no search text, just list all segments
    if not search_text:
        result = f"Document has {len(all_runs)} text segment(s):\n\n"
        for i, (loc, text, _) in enumerate(all_runs[:50]):  # Limit to first 50
            result += f"{i+1}. [{loc}]: \"{text[:60]}{'...' if len(text) > 60 else ''}\"\n"
        if len(all_runs) > 50:
            result += f"\n... and {len(all_runs) - 50} more segments"
        return result

    # Search for text
    search_lower = search_text.lower()

    # Build combined text and map positions back to runs
    combined_text = ""
    run_boundaries: list[tuple[int, int, int]] = []  # (start_pos, end_pos, run_list_idx)

    for i, (loc, text, run_obj) in enumerate(all_runs):
        start = len(combined_text)
        combined_text += text
        end = len(combined_text)
        run_boundaries.append((start, end, i))

    # Find all occurrences
    results = []
    pos = 0
    while len(results) < max_results:
        idx = combined_text.lower().find(search_lower, pos)
        if idx == -1:
            break

        match_end = idx + len(search_text)

        # Find the run(s) containing the match
        matching_run_indices = []
        for start, end, run_list_idx in run_boundaries:
            if start < match_end and end > idx:
                matching_run_indices.append(run_list_idx)

        if matching_run_indices:
            first_run_idx = matching_run_indices[0]
            last_run_idx = matching_run_indices[-1]

            # Get context runs
            start_idx = max(0, first_run_idx - context_segments)
            end_idx = min(len(all_runs), last_run_idx + context_segments + 1)

            # Format the runs
            runs_info = []
            for i in range(start_idx, end_idx):
                loc, text, run_obj = all_runs[i]
                is_match = i in matching_run_indices
                marker = ">>>" if is_match else "   "

                # Note formatting
                fmt = []
                try:
                    if run_obj.bold:
                        fmt.append("bold")
                    if run_obj.italic:
                        fmt.append("italic")
                except:
                    pass
                fmt_str = f" [{', '.join(fmt)}]" if fmt else ""

                runs_info.append(f"{marker} [{loc}]: \"{text}\"{fmt_str}")

            actual_match = combined_text[idx:match_end]
            result = f"Match {len(results) + 1}: \"{actual_match}\"\n"
            result += "\n".join(runs_info)
            results.append(result)

        pos = idx + 1

    if not results:
        # No matches - show summary of where text exists
        locations = set()
        for loc, _, _ in all_runs:
            if loc.startswith("body:"):
                locations.add("body paragraphs")
            elif loc.startswith("table"):
                locations.add("tables")
            elif loc.startswith("header"):
                locations.add("headers")
            elif loc.startswith("footer"):
                locations.add("footers")

        return f"No occurrences of '{search_text}' found.\n\nDocument contains text in: {', '.join(sorted(locations))}\nTotal segments: {len(all_runs)}\n\nTry searching for a shorter substring or use search_text=None to list all segments."

    header = f"Found {len(results)} occurrence(s) of '{search_text}':\n"
    header += ">>> marks matching segment(s). Location format: [container:paragraph:run]\n"
    header += "Text in tables shows as [tableX:rowY:cellZ:...]\n\n"

    return header + "\n\n".join(results)


@agent.tool
async def browse_document_segments(
    ctx: RunContext[TemplateAgentContext],
    start: int = 0,
    count: int = 30,
    location_filter: str | None = None,
) -> str:
    """
    (Internal diagnostic tool) Browse all text segments in the document.

    Use this to explore document structure when you can't find text.
    Shows segments from all parts of the document (body, tables, headers, footers).

    Args:
        start: Starting segment index (default: 0)
        count: Number of segments to return (default: 30, max: 100)
        location_filter: Filter to specific locations like "table" or "body" (optional)

    Returns:
        List of text segments with their locations
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    doc = ctx.deps.document
    all_runs = _collect_all_runs(doc)

    if not all_runs:
        return "Document has no text content."

    # Apply location filter
    if location_filter:
        filter_lower = location_filter.lower()
        all_runs = [(loc, text, run) for loc, text, run in all_runs if filter_lower in loc.lower()]

    if not all_runs:
        return f"No segments found matching filter '{location_filter}'"

    # Limit count
    count = min(count, 100)
    end = min(start + count, len(all_runs))

    result = f"Segments {start+1}-{end} of {len(all_runs)} total"
    if location_filter:
        result += f" (filtered by '{location_filter}')"
    result += ":\n\n"

    for i in range(start, end):
        loc, text, _ = all_runs[i]
        # Truncate long text
        display_text = text[:80] + ("..." if len(text) > 80 else "")
        result += f"{i+1}. [{loc}]: \"{display_text}\"\n"

    if end < len(all_runs):
        result += f"\n... {len(all_runs) - end} more segments. Use start={end} to see more."

    return result


def _create_single_field(
    document: Document,
    search_text: str,
    tag: str,
    alias: str | None = None,
    replace_with_placeholder: bool = True,
) -> tuple[bool, str]:
    """
    Helper function to convert text into a template field (content control internally).

    Args:
        document: The python-docx Document object
        search_text: The exact text to find and convert
        tag: The programmatic tag/key for the field (dot notation, e.g., "declarant.name")
        alias: Human-readable display name (defaults to formatted tag if not provided)
        replace_with_placeholder: If True (default), replace the text with "[tag]" placeholder

    Returns:
        Tuple of (success: bool, message: str)
    """
    if alias is None:
        # Convert dot notation to human-readable: "child1.date_of_birth" → "Child1 Date Of Birth"
        alias = tag.replace(".", " ").replace("_", " ").title()

    try:
        # Internal: uses content controls to implement fields
        count = wrap_text_in_content_control(
            document,
            search_text=search_text,
            tag=tag,
            alias=alias,
            first_only=False
        )

        if count > 0:
            placeholder_text = f"[{tag}]"
            if replace_with_placeholder:
                set_content_control_value(document, tag, placeholder_text)
                return True, f"'{search_text}' → '{tag}' → '{placeholder_text}'"
            return True, f"'{search_text}' → '{tag}'"
        else:
            return False, f"Text not found: '{search_text}'"

    except Exception as e:
        return False, f"Error creating field from '{search_text}': {str(e)}"


@agent.tool(sequential=True)
async def create_fields(
    ctx: RunContext[TemplateAgentContext],
    fields: list[FieldToCreate],
) -> str:
    """
    Convert text into template fields. Use this tool to mark text as fillable fields.

    Args:
        fields: List of fields to create. Each field has:
            - search_text: The exact text to find and convert
            - tag: The programmatic tag/key (dot notation, e.g., "declarant.name")
            - alias: Optional human-readable name (defaults to formatted tag)
            - replace_with_placeholder: If True (default), replace text with "[tag]"

    Example:
        fields=[
            FieldToCreate(search_text="Allen R. Moreland", tag="declarant.name"),
            FieldToCreate(search_text="January 15, 2024", tag="signing.date"),
            FieldToCreate(search_text="$50,000", tag="purchase.price", alias="Purchase Price")
        ]

    Returns:
        Summary of field creation with successes and failures
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    if not fields:
        return "No fields provided."

    results = []
    successes = 0
    failures = 0

    for field in fields:
        success, message = _create_single_field(
            document=ctx.deps.document,
            search_text=field.search_text,
            tag=field.tag,
            alias=field.alias,
            replace_with_placeholder=field.replace_with_placeholder,
        )

        if success:
            ctx.deps.modifications.append(f"Created field: {message}")
            results.append(f"✓ {message}")
            successes += 1
        else:
            results.append(f"❌ {message}")
            failures += 1

    # Save working copy once after all operations
    try:
        working_path = ctx.deps.get_working_path()
        if working_path:
            ctx.deps.document.save(str(working_path))
    except Exception as e:
        return f"Operations completed but failed to save: {str(e)}\n\nResults:\n" + "\n".join(results)

    logfire.info(f"Batch create_fields completed", successes=successes, failures=failures)

    summary = f"Created {successes} field(s)"
    if failures > 0:
        summary += f", {failures} failed"
    summary += ":\n\n" + "\n".join(results)

    return summary


@agent.tool(sequential=True)
async def update_field_value(
    ctx: RunContext[TemplateAgentContext],
    tag: str,
    new_value: str,
) -> str:
    """
    Update the text value inside an existing template field.

    Use this to change the displayed text of a field - for example,
    replacing "Allen R. Moreland" with "[declarant.name]" as a placeholder.

    Args:
        tag: The tag of the field to modify (e.g., "declarant.name")
        new_value: The new text value to set (e.g., "[declarant.name]")

    Returns:
        Status message indicating success or failure
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    try:
        # Get current value for logging
        working_path = ctx.deps.get_working_path()
        if working_path and working_path.exists():
            controls = read_content_controls_detailed(str(working_path))
        else:
            controls = read_content_controls_detailed(str(ctx.deps.get_document_path()))

        old_value = None
        for ctrl in controls:
            if ctrl["tag"] == tag:
                old_value = ctrl["value"]
                break

        if old_value is None:
            available_tags = [ctrl["tag"] for ctrl in controls]
            return f"Field with tag '{tag}' not found. Available tags: {available_tags}"

        # Replace the text
        success = set_content_control_value(ctx.deps.document, tag, new_value)

        if success:
            ctx.deps.modifications.append(f"Updated '{tag}': '{old_value}' → '{new_value}'")

            # Save working copy
            if working_path:
                ctx.deps.document.save(str(working_path))

            logfire.info(f"Updated field {tag}: '{old_value}' -> '{new_value}'")
            return f"Updated field '{tag}': '{old_value}' → '{new_value}'"
        else:
            return f"Failed to update field '{tag}'"

    except Exception as e:
        logfire.error(f"Failed to update field: {e}")
        return f"Error updating field: {str(e)}"


@agent.tool(sequential=True)
async def delete_field(
    ctx: RunContext[TemplateAgentContext],
    tag: str,
    preserve_text: bool = True,
) -> str:
    """
    Delete a template field from the document.

    Args:
        tag: The tag of the field to delete (e.g., "declarant.name")
        preserve_text: If True (default), keep the text content in place.
                       If False, remove both the field and its text.

    Returns:
        Status message indicating success or failure
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    try:
        # Get current value for logging
        working_path = ctx.deps.get_working_path()
        if working_path and working_path.exists():
            controls = read_content_controls_detailed(str(working_path))
        else:
            controls = read_content_controls_detailed(str(ctx.deps.get_document_path()))

        # Check if field exists
        field_info = None
        for ctrl in controls:
            if ctrl["tag"] == tag:
                field_info = ctrl
                break

        if field_info is None:
            available_tags = [ctrl["tag"] for ctrl in controls]
            return f"Field with tag '{tag}' not found. Available tags: {available_tags}"

        # Delete the field
        count = delete_content_control(ctx.deps.document, tag, preserve_text=preserve_text)

        if count > 0:
            action = "removed (text preserved)" if preserve_text else "removed with text"
            ctx.deps.modifications.append(f"Deleted field '{tag}' ({action})")

            # Save working copy
            if working_path:
                ctx.deps.document.save(str(working_path))

            logfire.info(f"Deleted field {tag}", preserve_text=preserve_text)
            return f"Deleted field '{tag}' (was: '{field_info['value'][:50]}...')" if len(field_info['value']) > 50 else f"Deleted field '{tag}' (was: '{field_info['value']}')"
        else:
            return f"Failed to delete field '{tag}'"

    except Exception as e:
        logfire.error(f"Failed to delete field: {e}")
        return f"Error deleting field: {str(e)}"


@agent.tool(sequential=True)
async def edit_field(
    ctx: RunContext[TemplateAgentContext],
    tag: str,
    new_tag: str | None = None,
    new_alias: str | None = None,
    new_value: str | None = None,
    sync_new_tag_changes: bool = True,
) -> str:
    """
    Edit an existing template field's properties.

    By default, changing the tag will also update the alias and value to match.
    Set sync_new_tag_changes=False to provide explicit values or change only the tag.

    Args:
        tag: The current tag of the field to edit (e.g., "declarant_name")
        new_tag: New tag value (e.g., "declarant.name"). None to keep existing.
        new_alias: New display name. Requires sync_new_tag_changes=False if provided with new_tag.
        new_value: New text value. Requires sync_new_tag_changes=False if provided with new_tag.
        sync_new_tag_changes: If True (default), changing tag auto-generates alias/value.
                              MUST be False if providing explicit new_alias or new_value with new_tag.

    Examples:
        # Rename field completely (tag, alias, value all sync)
        edit_field(tag="client_name", new_tag="client.name")

        # Rename with custom alias/value - must set sync_new_tag_changes=False
        edit_field(tag="client_name", new_tag="client.name", new_alias="Full Name", sync_new_tag_changes=False)

        # Change only the tag, keep existing alias/value
        edit_field(tag="client_name", new_tag="client.name", sync_new_tag_changes=False)

        # Change just alias or value (no new_tag, sync doesn't apply)
        edit_field(tag="client_name", new_alias="Full Name")

    Returns:
        Status message indicating success or failure
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    if new_tag is None and new_alias is None and new_value is None:
        return "No changes specified. Provide at least one of: new_tag, new_alias, or new_value."

    # Validation: explicit values with sync_new_tag_changes=True is ambiguous
    if sync_new_tag_changes and new_tag is not None:
        if new_alias is not None or new_value is not None:
            provided = []
            if new_alias is not None:
                provided.append("new_alias")
            if new_value is not None:
                provided.append("new_value")
            return (
                f"Ambiguous call: {', '.join(provided)} provided with sync_new_tag_changes=True. "
                f"Set sync_new_tag_changes=False when providing explicit values with new_tag."
            )
        # Auto-generate alias and value
        new_alias = new_tag.replace(".", " ").replace("_", " ").title()
        new_value = f"[{new_tag}]"

    try:
        # Get current info for logging
        working_path = ctx.deps.get_working_path()
        if working_path and working_path.exists():
            controls = read_content_controls_detailed(str(working_path))
        else:
            controls = read_content_controls_detailed(str(ctx.deps.get_document_path()))

        # Check if field exists
        field_info = None
        for ctrl in controls:
            if ctrl["tag"] == tag:
                field_info = ctrl
                break

        if field_info is None:
            available_tags = [ctrl["tag"] for ctrl in controls]
            return f"Field with tag '{tag}' not found. Available tags: {available_tags}"

        # Update the field
        count = update_content_control(
            ctx.deps.document,
            tag=tag,
            new_tag=new_tag,
            new_alias=new_alias,
            new_value=new_value,
        )

        if count > 0:
            # Build change summary
            changes = []
            if new_tag is not None:
                changes.append(f"tag: '{tag}' → '{new_tag}'")
            if new_alias is not None:
                changes.append(f"display name: '{field_info['alias']}' → '{new_alias}'")
            if new_value is not None:
                old_val = field_info['value'][:30] + "..." if len(field_info['value']) > 30 else field_info['value']
                new_val = new_value[:30] + "..." if len(new_value) > 30 else new_value
                changes.append(f"value: '{old_val}' → '{new_val}'")

            change_summary = ", ".join(changes)
            ctx.deps.modifications.append(f"Edited field '{tag}': {change_summary}")

            # Save working copy
            if working_path:
                ctx.deps.document.save(str(working_path))

            logfire.info(f"Edited field {tag}", new_tag=new_tag, new_alias=new_alias, new_value=new_value)
            return f"Edited field '{tag}': {change_summary}"
        else:
            return f"Failed to edit field '{tag}'"

    except Exception as e:
        logfire.error(f"Failed to edit field: {e}")
        return f"Error editing field: {str(e)}"


@agent.tool(sequential=True)
async def reset_working_copy(ctx: RunContext[TemplateAgentContext]) -> str:
    """
    Reset the working copy by deleting it and reloading from the original document.

    Use this to discard all modifications and start fresh.

    Returns:
        Status message indicating success
    """
    if ctx.deps.document_filename is None:
        return "No document loaded."

    try:
        # Delete working copy if it exists
        working_path = ctx.deps.get_working_path()
        if working_path and working_path.exists():
            working_path.unlink()
            logfire.info(f"Deleted working copy: {working_path}")

        # Clear modifications list
        ctx.deps.modifications.clear()

        # Reload from original
        ctx.deps.document = None
        success, message = ctx.deps.load_document()

        if success:
            return f"Working copy reset. Reloaded original document: {ctx.deps.document_filename}"
        else:
            return f"Reset working copy but failed to reload: {message}"

    except Exception as e:
        logfire.error(f"Failed to reset working copy: {e}")
        return f"Error resetting working copy: {str(e)}"


@agent.tool(sequential=True)
async def get_fields(ctx: RunContext[TemplateAgentContext]) -> str:
    """
    Get the current list of template fields in the document.

    Returns:
        List of all fields with their tags, display names, and values
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    # Save current state to temp file to read fields
    working_path = ctx.deps.get_working_path()
    if working_path:
        ctx.deps.document.save(str(working_path))
        controls = read_content_controls_detailed(str(working_path))
    else:
        controls = read_content_controls_detailed(str(ctx.deps.get_document_path()))

    if not controls:
        return "No template fields found in the document."

    return f"Current template fields ({len(controls)}):\n\n{_format_fields(controls)}"


@agent.tool(sequential=True)
async def get_modifications(ctx: RunContext[TemplateAgentContext]) -> str:
    """
    Get the list of modifications made during this session.

    Returns:
        List of all changes made to the document
    """
    if not ctx.deps.modifications:
        return "No modifications made yet."

    result = f"Modifications made ({len(ctx.deps.modifications)}):\n"
    for i, mod in enumerate(ctx.deps.modifications, 1):
        result += f"{i}. {mod}\n"
    return result


@agent.tool(sequential=True)
async def save_template(
    ctx: RunContext[TemplateAgentContext],
    new_filename: str | None = None,
    overwrite: bool = False,
) -> str:
    """
    Save the modified document as a template.

    Args:
        new_filename: Optional new filename. If not provided and overwrite=False,
                      will add '_template' suffix to original name.
        overwrite: If True, overwrite the original file

    Returns:
        Status message with the saved file path
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    if not ctx.deps.modifications:
        return "No modifications have been made. Nothing to save."

    original_path = ctx.deps.get_document_path()

    if overwrite and not new_filename:
        save_path = original_path
    elif new_filename:
        if not new_filename.endswith(".docx"):
            new_filename += ".docx"
        save_path = DOCUMENTS_DIR / new_filename
    else:
        # Default: add _template suffix
        stem = original_path.stem
        save_path = DOCUMENTS_DIR / f"{stem}_template.docx"

    # Check if file exists and we're not overwriting
    if save_path.exists() and save_path != original_path and not overwrite:
        return f"File '{save_path.name}' already exists. Use overwrite=True or provide a different filename."

    try:
        ctx.deps.document.save(str(save_path))

        # Clean up working file
        working_path = ctx.deps.get_working_path()
        if working_path and working_path.exists() and working_path != save_path:
            working_path.unlink()

        logfire.info(f"Saved template: {save_path.name}", modifications=len(ctx.deps.modifications))

        return f"Template saved successfully as '{save_path.name}' with {len(ctx.deps.modifications)} modifications."

    except Exception as e:
        logfire.error(f"Failed to save template: {e}")
        return f"Error saving template: {str(e)}"


@agent.tool(sequential=True)
async def list_documents(_ctx: RunContext[TemplateAgentContext]) -> str:
    """
    List all available DOCX documents.

    Returns:
        List of document filenames
    """
    docs = list(DOCUMENTS_DIR.glob("*.docx"))

    if not docs:
        return "No documents found. Upload a document first."

    result = f"Available documents ({len(docs)}):\n"
    for doc in sorted(docs):
        result += f"- {doc.name}\n"
    return result


def _format_fields(controls: list[dict]) -> str:
    """Format template fields for display"""
    result = ""
    for i, ctrl in enumerate(controls, 1):
        result += f"{i}. Tag: {ctrl['tag']}\n"
        result += f"   Display Name: {ctrl['alias']}\n"
        result += f"   Current Value: {ctrl['value'][:50]}{'...' if len(ctrl['value']) > 50 else ''}\n\n"
    return result
