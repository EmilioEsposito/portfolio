"""
Template Field Detection Agent

An AI agent that analyzes DOCX templates and helps attorneys identify and create
content controls for fillable fields. The agent can detect placeholders, suggest
field names, and wrap text in content controls.
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


class FieldToWrap(BaseModel):
    """Input for a single field wrapping operation"""
    search_text: str  # The exact text to find and wrap
    tag: str  # The programmatic tag/key for the content control (snake_case)
    alias: str | None = None  # Human-readable display name (defaults to tag)
    replace_with_placeholder: bool = True  # If True, replace text with "[tag]" format


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
    system_prompt="""You are a legal document template assistant. You help attorneys convert documents
into templates with proper content controls (fillable fields).

IMPORTANT: A document has been pre-selected and loaded for this session. At the START of EVERY conversation,
call `get_document_info` to see which document is loaded.

## Document Types You'll Encounter

1. **Existing templates** - Documents with placeholders like [Name], {{field}}, or blank lines.
   These just need the placeholders wrapped as content controls.

2. **Filled documents** - Real documents with actual names, dates, addresses, amounts.
   The user wants to CONVERT these into templates by replacing specific values with placeholders.
   Example: "Allen R. Moreland" → [declarant_name], "January 15, 2024" → [effective_date]

If you see a document with real names, dates, and addresses (not placeholders), ASK the user:
"This looks like a filled document rather than a template. Would you like me to help convert it
into a template by identifying values that should become fillable fields?"

## Your Tools

**Reading & Searching:**
- `get_document_info` - See loaded document info and preview
- `get_document_text` - Get the full document text for analysis
- `search_text` - Find all occurrences of specific text (useful for names that appear multiple times)
- `list_content_controls` - Show existing content controls

**Modifying:**
- `wrap_fields` - Wrap text as content controls. Always use this tool for wrapping.
- `replace_text` - Change the value inside an existing content control
- `reset_working_copy` - Discard all modifications

## Workflow for Converting Filled Documents

1. Use `get_document_text` to read the full content
2. Identify values that should become fields (names, dates, addresses, amounts)
3. Use `search_text` to find all occurrences of each value
4. Ask user to confirm which items to convert
5. Use `wrap_fields` to wrap ALL confirmed fields in one call (more efficient than multiple wrap_field calls)

## Tag Naming
- Use snake_case: client_name, effective_date, purchase_price
- Be specific: declarant_name vs just "name", closing_date vs just "date"
- Use descriptive aliases: "Declarant Name", "Closing Date"

Always explain what you're doing and ask for confirmation before making changes.
""",
    retries=2,
)


@agent.system_prompt
async def document_context(ctx: RunContext[TemplateAgentContext]) -> str:
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
- Existing content controls: {len(controls)}

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
- Existing content controls: {len(controls)}

Text Preview:
{text_preview}

Existing content controls:
{_format_controls(controls) if controls else "None"}

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
- Existing content controls: {len(controls)}

Preview:
{text_preview}

Existing content controls:
{_format_controls(controls) if controls else "None"}"""

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
    which you can then wrap as a content control. You can also use this to read sections
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
async def list_content_controls(ctx: RunContext[TemplateAgentContext]) -> str:
    """
    List all existing content controls in the document.

    Returns:
        List of content controls with their tags, aliases, and current values
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
        return "No content controls found in the document."

    result = f"Found {len(controls)} content control(s):\n\n"
    for i, c in enumerate(controls, 1):
        result += f"{i}. Tag: {c['tag']}\n"
        result += f"   Alias: {c['alias']}\n"
        result += f"   Value: \"{c['value']}\"\n\n"

    return result


def _wrap_single_field(
    document: Document,
    search_text: str,
    tag: str,
    alias: str | None = None,
    replace_with_placeholder: bool = True,
) -> tuple[bool, str]:
    """
    Helper function to wrap specific text in the document with a content control.

    Args:
        document: The python-docx Document object
        search_text: The exact text to find and wrap
        tag: The programmatic tag/key for the content control (snake_case)
        alias: Human-readable display name (defaults to tag if not provided)
        replace_with_placeholder: If True (default), replace the text content with "[tag]" format

    Returns:
        Tuple of (success: bool, message: str)
    """
    if alias is None:
        alias = tag.replace("_", " ").title()

    try:
        count = wrap_text_in_content_control(
            document,
            search_text=search_text,
            tag=tag,
            alias=alias,
            first_only=True
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
        return False, f"Error wrapping '{search_text}': {str(e)}"


@agent.tool(sequential=True)
async def wrap_fields(
    ctx: RunContext[TemplateAgentContext],
    fields: list[FieldToWrap],
) -> str:
    """
    Wrap text in content controls. Use this tool for ALL field wrapping operations.

    Args:
        fields: List of fields to wrap. Each field has:
            - search_text: The exact text to find and wrap
            - tag: The programmatic tag/key (snake_case, e.g., "declarant_name")
            - alias: Optional human-readable name (defaults to tag titlecased)
            - replace_with_placeholder: If True (default), replace text with "[tag]"

    Example:
        fields=[
            FieldToWrap(search_text="Allen R. Moreland", tag="declarant_name"),
            FieldToWrap(search_text="January 15, 2024", tag="effective_date"),
            FieldToWrap(search_text="$50,000", tag="purchase_price", alias="Purchase Price")
        ]

    Returns:
        Summary of all wrap operations with successes and failures
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    if not fields:
        return "No fields provided."

    results = []
    successes = 0
    failures = 0

    for field in fields:
        success, message = _wrap_single_field(
            document=ctx.deps.document,
            search_text=field.search_text,
            tag=field.tag,
            alias=field.alias,
            replace_with_placeholder=field.replace_with_placeholder,
        )

        if success:
            ctx.deps.modifications.append(f"Wrapped {message}")
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

    logfire.info(f"Batch wrap_fields completed", successes=successes, failures=failures)

    summary = f"Wrapped {successes} field(s)"
    if failures > 0:
        summary += f", {failures} failed"
    summary += ":\n\n" + "\n".join(results)

    return summary


@agent.tool(sequential=True)
async def replace_text(
    ctx: RunContext[TemplateAgentContext],
    tag: str,
    new_value: str,
) -> str:
    """
    Replace the text content inside an existing content control.

    Use this to change the display text of a content control - for example,
    replacing "Allen R. Moreland" with "[declarant_name]" as a placeholder.

    Args:
        tag: The programmatic tag of the content control to modify
        new_value: The new text value to set (e.g., "[declarant_name]")

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
            return f"Content control with tag '{tag}' not found. Available tags: {available_tags}"

        # Replace the text
        success = set_content_control_value(ctx.deps.document, tag, new_value)

        if success:
            ctx.deps.modifications.append(f"Replaced '{old_value}' with '{new_value}' in '{tag}'")

            # Save working copy
            if working_path:
                ctx.deps.document.save(str(working_path))

            logfire.info(f"Replaced text in {tag}: '{old_value}' -> '{new_value}'")
            return f"Successfully replaced text in '{tag}': '{old_value}' → '{new_value}'"
        else:
            return f"Failed to replace text in content control '{tag}'"

    except Exception as e:
        logfire.error(f"Failed to replace text: {e}")
        return f"Error replacing text: {str(e)}"


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
async def get_current_controls(ctx: RunContext[TemplateAgentContext]) -> str:
    """
    Get the current list of content controls in the document.

    Returns:
        List of all content controls with their tags, aliases, and values
    """
    if ctx.deps.document is None:
        return "No document loaded. Use load_document first."

    # Save current state to temp file to read controls
    working_path = ctx.deps.get_working_path()
    if working_path:
        ctx.deps.document.save(str(working_path))
        controls = read_content_controls_detailed(str(working_path))
    else:
        controls = read_content_controls_detailed(str(ctx.deps.get_document_path()))

    if not controls:
        return "No content controls found in the document."

    return f"Current content controls ({len(controls)}):\n\n{_format_controls(controls)}"


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


def _format_controls(controls: list[dict]) -> str:
    """Format content controls for display"""
    result = ""
    for i, ctrl in enumerate(controls, 1):
        result += f"{i}. Tag: {ctrl['tag']}\n"
        result += f"   Alias: {ctrl['alias']}\n"
        result += f"   Value: {ctrl['value'][:50]}{'...' if len(ctrl['value']) > 50 else ''}\n\n"
    return result
