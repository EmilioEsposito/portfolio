import type { Route } from "./+types/sernia-admin";
import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate } from "react-router";
import { useAuth } from "@clerk/react-router";
import type {
  ColumnDef,
  Column,
  FilterFn,
  SortingState,
  ColumnFiltersState,
} from "@tanstack/react-table";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getFacetedRowModel,
  getFacetedUniqueValues,
  flexRender,
} from "@tanstack/react-table";
import { AuthGuard } from "~/components/auth-guard";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Badge } from "~/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "~/components/ui/table";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "~/components/ui/sheet";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "~/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "~/components/ui/command";
import { cn } from "~/lib/utils";
import { Markdown } from "~/components/markdown";
import { ToolResultCard } from "~/components/chat/tool-cards";
import { processMessage } from "~/components/chat/process-message";
import { FileMessageDisplay } from "~/components/chat/file-message-display";
import {
  Building,
  Loader2,
  RefreshCw,
  Trash2,
  Phone,
  Mail,
  MessageSquare,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
  Check,
  Filter,
  FilterX,
  ArrowUpDown,
  ArrowDown,
  ArrowUp,
  Settings,
} from "lucide-react";

const API_BASE = "/api/sernia-ai";
const PAGE_SIZE = 30;

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Sernia Admin — Conversations" },
    {
      name: "description",
      content: "Browse and inspect all Sernia AI conversations.",
    },
  ];
}

interface ConversationSummary {
  conversation_id: string;
  agent_name: string;
  clerk_user_id: string | null;
  user_email: string | null;
  modality: string;
  preview: string;
  estimated_tokens: number;
  cost_last_run: number | null;
  cost_total: number;
  run_count: number;
  contact_identifier: string | null;
  participant: string;
  has_pending: boolean;
  trigger_source: string | null;
  trigger_contact_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

// ---------------------------------------------------------------------------
// TanStack Table filter helpers
// ---------------------------------------------------------------------------

const FACETED_COLUMNS = new Set(["modality", "participant"]);

const facetedFilterFn: FilterFn<ConversationSummary> = (
  row,
  columnId,
  filterValue: string[]
) => {
  if (!filterValue || filterValue.length === 0) return true;
  const cellValue = String(row.getValue(columnId) ?? "");
  return filterValue.includes(cellValue);
};

const dateRangeFilterFn: FilterFn<ConversationSummary> = (
  row,
  columnId,
  filterValue: [string, string]
) => {
  if (!filterValue) return true;
  const [start, end] = filterValue;
  const cellValue = row.getValue(columnId) as string | null;
  if (!cellValue) return false;
  const date = cellValue.slice(0, 10); // YYYY-MM-DD
  if (start && date < start) return false;
  if (end && date > end) return false;
  return true;
};

const RESPONSIVE_CLASSES: Record<string, string> = {
  modality: "hidden sm:table-cell",
  cost_total: "hidden lg:table-cell",
  has_pending: "hidden sm:table-cell",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function modalityIcon(modality: string) {
  switch (modality) {
    case "sms":
      return <Phone className="w-3.5 h-3.5" />;
    case "email":
      return <Mail className="w-3.5 h-3.5" />;
    default:
      return <MessageSquare className="w-3.5 h-3.5" />;
  }
}

function formatDateTime(dateString: string | null) {
  if (!dateString) return "";
  const date = new Date(dateString);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();

  if (isToday) {
    return date.toLocaleString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZone: "America/New_York",
    }).replace(" ", "").toLowerCase() + " ET";
  }

  const msPerDay = 86_400_000;
  const daysDiff = Math.floor((now.getTime() - date.getTime()) / msPerDay);
  if (daysDiff < 7) {
    return date.toLocaleString("en-US", {
      weekday: "short",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZone: "America/New_York",
    });
  }

  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "America/New_York",
  });
}

function formatTokens(tokens: number) {
  if (tokens === 0) return "—";
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(1)}k`;
  return String(tokens);
}

function formatCost(cost: number | null) {
  if (cost == null) return "—";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// Detail Drawer — shows full message thread for a conversation
// ---------------------------------------------------------------------------

function ConversationDetail({
  conversationId,
  getToken,
  onDelete,
}: {
  conversationId: string;
  getToken: () => Promise<string | null>;
  onDelete: () => void;
}) {
  const navigate = useNavigate();
  const [messages, setMessages] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setMessages(null);
      setError(null);
      try {
        const token = await getToken();
        const res = await fetch(
          `${API_BASE}/conversation/${conversationId}/messages`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const data = await res.json();
        if (!cancelled) setMessages(data.messages || []);
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId, getToken]);

  const handleDelete = async () => {
    if (!confirm("Delete this conversation?")) return;
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/conversation/${conversationId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) onDelete();
    } catch (err) {
      console.error("Failed to delete:", err);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Actions */}
      <div className="flex items-center gap-2 px-4 py-3 border-b">
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={() => navigate(`/sernia-chat?id=${conversationId}`)}
        >
          <ExternalLink className="w-3.5 h-3.5" />
          Open in Chat
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5 text-destructive hover:text-destructive"
          onClick={handleDelete}
        >
          <Trash2 className="w-3.5 h-3.5" />
          Delete
        </Button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/20 p-4 text-sm text-red-700 dark:text-red-400">
            {error}
          </div>
        )}

        {messages === null && !error && (
          <div className="flex justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {messages?.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-8">
            No messages
          </p>
        )}

        {messages?.map((message, index) => {
          const { segments } = processMessage(message);

          return (
            <div
              key={message.id || index}
              className={cn(
                "flex gap-3",
                message.role === "user" ? "justify-end" : "justify-start"
              )}
            >
              {message.role === "assistant" && (
                <div className="shrink-0">
                  <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center">
                    <Building className="w-3.5 h-3.5 text-primary-foreground" />
                  </div>
                </div>
              )}

              <div
                className={cn(
                  "flex flex-col gap-2 max-w-[90%] min-w-0",
                  message.role === "user" && "items-end"
                )}
              >
                {message.role === "user" ? (
                  <>
                    <FileMessageDisplay
                      files={
                        segments.filter((s) => s.type === "file") as any
                      }
                    />
                    {segments.some((s) => s.type === "text") && (
                      <div className="bg-primary text-primary-foreground rounded-2xl px-3 py-2 shadow-sm">
                        <p className="text-sm whitespace-pre-wrap">
                          {segments.find((s) => s.type === "text")?.type ===
                          "text"
                            ? (
                                segments.find((s) => s.type === "text") as any
                              ).content
                            : ""}
                        </p>
                      </div>
                    )}
                  </>
                ) : (
                  segments.map((seg, i) =>
                    seg.type === "text" ? (
                      <div
                        key={i}
                        className="bg-muted/50 rounded-2xl px-3 py-2 shadow-sm overflow-hidden"
                      >
                        <div className="text-sm prose prose-sm dark:prose-invert max-w-none break-words">
                          <Markdown>{seg.content}</Markdown>
                        </div>
                      </div>
                    ) : seg.type === "tool" ? (
                      <ToolResultCard
                        key={seg.toolCallId}
                        toolName={seg.toolName}
                        args={seg.args}
                        result={
                          typeof seg.result === "string"
                            ? seg.result
                            : JSON.stringify(seg.result)
                        }
                      />
                    ) : null
                  )
                )}
              </div>

              {message.role === "user" && (
                <div className="shrink-0">
                  <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center text-xs font-medium">
                    U
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Admin Page
// ---------------------------------------------------------------------------

export default function SerniaAdminPage() {
  const { getToken } = useAuth();
  const navigate = useNavigate();

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  // TanStack Table state
  const [sorting, setSorting] = useState<SortingState>([
    { id: "updated_at", desc: true },
  ]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  // Detail drawer
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedConv = conversations.find(
    (c) => c.conversation_id === selectedId
  );

  const fetchConversations = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
      });

      const res = await fetch(
        `${API_BASE}/conversations/history?${params}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      setConversations(data.conversations || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [getToken, page]);

  useEffect(() => {
    fetchConversations();
  }, [fetchConversations]);

  const handleDelete = () => {
    setSelectedId(null);
    fetchConversations();
  };

  // Column definitions
  const columns = useMemo(
    (): ColumnDef<ConversationSummary>[] => [
      {
        accessorKey: "updated_at",
        header: ({ column }) => (
          <ColumnHeader column={column} title="Time" />
        ),
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground whitespace-nowrap">
            {formatDateTime(row.getValue("updated_at") as string | null)}
          </span>
        ),
        filterFn: dateRangeFilterFn,
        enableSorting: true,
      },
      {
        accessorKey: "participant",
        header: ({ column }) => (
          <ColumnHeader column={column} title="Participant" />
        ),
        cell: ({ row }) => (
          <div className="flex items-center gap-1.5 min-w-0 max-w-[200px]">
            <span className="text-muted-foreground shrink-0">
              {modalityIcon(row.original.modality)}
            </span>
            <span className="text-sm font-medium truncate">
              {row.getValue("participant") || "—"}
            </span>
          </div>
        ),
        filterFn: facetedFilterFn,
        enableSorting: true,
      },
      {
        accessorKey: "preview",
        header: ({ column }) => (
          <ColumnHeader column={column} title="Preview" />
        ),
        cell: ({ row }) => (
          <p className="truncate text-sm text-muted-foreground max-w-[300px]">
            {row.getValue("preview") || "Empty conversation"}
          </p>
        ),
        filterFn: "includesString",
        enableSorting: true,
      },
      {
        accessorKey: "modality",
        header: ({ column }) => (
          <ColumnHeader column={column} title="Source" />
        ),
        cell: ({ row }) => {
          const modality = row.getValue("modality") as string;
          return (
            <span className="text-xs text-muted-foreground capitalize">
              {modality.replace("_", " ")}
            </span>
          );
        },
        filterFn: facetedFilterFn,
        enableSorting: true,
      },
      {
        accessorKey: "cost_total",
        header: ({ column }) => (
          <ColumnHeader column={column} title="Cost (all)" />
        ),
        cell: ({ row }) => {
          const total = row.getValue("cost_total") as number;
          const runs = row.original.run_count;
          return (
            <div className="text-right">
              <span className="text-xs text-muted-foreground font-mono block">
                {total > 0 ? formatCost(total) : "—"}
              </span>
              {runs > 1 && (
                <span className="text-[10px] text-muted-foreground/60">
                  {runs} runs
                </span>
              )}
            </div>
          );
        },
        enableSorting: true,
        enableColumnFilter: false,
      },
      {
        accessorKey: "has_pending",
        header: ({ column }) => (
          <ColumnHeader column={column} title="Status" />
        ),
        cell: ({ row }) =>
          row.getValue("has_pending") ? (
            <Badge
              variant="outline"
              className="text-xs text-amber-600 border-amber-300"
            >
              Pending
            </Badge>
          ) : null,
        enableSorting: true,
        enableColumnFilter: false,
      },
    ],
    []
  );

  const table = useReactTable({
    data: conversations,
    columns,
    state: { sorting, columnFilters },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getFacetedRowModel: getFacetedRowModel(),
    getFacetedUniqueValues: getFacetedUniqueValues(),
  });

  return (
    <AuthGuard
      requireDomain="serniacapital.com"
      message="Admin access required"
      icon={<Building className="w-16 h-16 text-muted-foreground" />}
    >
      <div className="flex flex-col h-[calc(100dvh-52px)] bg-background">
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0"
            onClick={() => navigate("/sernia-chat")}
          >
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div className="flex-1 min-w-0">
            <h1 className="text-lg font-semibold">Conversations</h1>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => navigate("/sernia-settings")}
            title="Schedule & trigger settings"
          >
            <Settings className="w-4 h-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchConversations}
            disabled={loading}
            className="gap-1.5"
          >
            <RefreshCw
              className={cn("w-3.5 h-3.5", loading && "animate-spin")}
            />
            Refresh
          </Button>
        </div>

        {/* Error */}
        {error && (
          <div className="mx-4 mt-2 rounded-lg border border-red-300 bg-red-50 dark:bg-red-950/20 p-3 text-sm text-red-700 dark:text-red-400">
            {error}
          </div>
        )}

        {/* Table */}
        <div className="flex-1 overflow-auto">
          {loading && conversations.length === 0 ? (
            <div className="flex justify-center py-16">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <Table>
              <TableHeader className="sticky top-0 bg-background z-10">
                {table.getHeaderGroups().map((headerGroup) => (
                  <TableRow key={headerGroup.id}>
                    {headerGroup.headers.map((header) => (
                      <TableHead
                        key={header.id}
                        className={RESPONSIVE_CLASSES[header.id] ?? ""}
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(
                              header.column.columnDef.header,
                              header.getContext()
                            )}
                      </TableHead>
                    ))}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {table.getRowModel().rows.length ? (
                  table.getRowModel().rows.map((row) => (
                    <TableRow
                      key={row.id}
                      className={cn(
                        "cursor-pointer",
                        row.original.conversation_id === selectedId &&
                          "bg-muted"
                      )}
                      onClick={() =>
                        setSelectedId(row.original.conversation_id)
                      }
                    >
                      {row.getVisibleCells().map((cell) => (
                        <TableCell
                          key={cell.id}
                          className={
                            RESPONSIVE_CLASSES[cell.column.id] ?? ""
                          }
                        >
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext()
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell
                      colSpan={columns.length}
                      className="h-24 text-center text-muted-foreground"
                    >
                      No conversations found
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between px-4 py-2 border-t text-sm text-muted-foreground">
          <span>
            Page {page + 1}
            {conversations.length === PAGE_SIZE && "+"}
          </span>
          <div className="flex gap-1">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
              className="h-8 w-8 p-0"
            >
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={conversations.length < PAGE_SIZE}
              onClick={() => setPage((p) => p + 1)}
              className="h-8 w-8 p-0"
            >
              <ChevronRight className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </div>

      {/* Detail drawer */}
      <Sheet
        open={!!selectedId}
        onOpenChange={(open) => !open && setSelectedId(null)}
      >
        <SheetContent side="right" className="w-full sm:max-w-xl p-0">
          <SheetHeader className="px-4 py-3 border-b">
            <SheetTitle className="text-sm font-medium flex items-center gap-2">
              {selectedConv && (
                <>
                  {modalityIcon(selectedConv.modality)}
                  <span className="truncate flex-1">
                    {selectedConv.participant}
                  </span>
                  {selectedConv.has_pending && (
                    <Badge
                      variant="outline"
                      className="text-xs text-amber-600 border-amber-300"
                    >
                      Pending
                    </Badge>
                  )}
                </>
              )}
            </SheetTitle>
            {selectedConv && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span className="capitalize">
                  {selectedConv.modality.replace("_", " ")}
                </span>
                {selectedConv.cost_total > 0 && (
                  <span>
                    {formatCost(selectedConv.cost_total)} total
                    {selectedConv.run_count > 1 &&
                      ` · ${selectedConv.run_count} runs`}
                  </span>
                )}
                {selectedConv.created_at && (
                  <span>
                    Created: {formatDateTime(selectedConv.created_at)}
                  </span>
                )}
                <span className="font-mono">
                  {selectedConv.conversation_id.slice(0, 8)}
                </span>
              </div>
            )}
          </SheetHeader>
          {selectedId && (
            <ConversationDetail
              key={selectedId}
              conversationId={selectedId}
              getToken={getToken}
              onDelete={handleDelete}
            />
          )}
        </SheetContent>
      </Sheet>
    </AuthGuard>
  );
}

// ---------------------------------------------------------------------------
// Column Header with Sort + Filter
// ---------------------------------------------------------------------------

function ColumnHeader({
  column,
  title,
}: {
  column: Column<ConversationSummary, unknown>;
  title: string;
}) {
  const isFiltered = column.getIsFiltered();
  const canSort = column.getCanSort();
  const canFilter = column.getCanFilter();
  const isFaceted = FACETED_COLUMNS.has(column.id);
  const isDate = column.id === "updated_at";

  return (
    <div className="flex items-center space-x-1 justify-between pr-1">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-3 h-8 p-1 data-[state=open]:bg-accent text-left justify-start grow truncate text-xs"
        onClick={() =>
          canSort && column.toggleSorting(column.getIsSorted() === "asc")
        }
        disabled={!canSort}
      >
        <span className="truncate">{title}</span>
        {canSort &&
          (column.getIsSorted() === "desc" ? (
            <ArrowDown className="ml-1 h-3 w-3" />
          ) : column.getIsSorted() === "asc" ? (
            <ArrowUp className="ml-1 h-3 w-3" />
          ) : (
            <ArrowUpDown className="ml-1 h-3 w-3 opacity-30" />
          ))}
      </Button>

      {canFilter && (
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              className={cn("h-5 w-5 p-1", isFiltered && "text-primary")}
            >
              {isFiltered ? (
                <FilterX className="h-3 w-3" />
              ) : (
                <Filter className="h-3 w-3" />
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent
            className={cn("p-0", isDate ? "w-56" : "w-48")}
            align="start"
          >
            {isFaceted ? (
              <FacetedFilter column={column} title={title} />
            ) : isDate ? (
              <DateRangeFilter column={column} />
            ) : (
              <div className="p-2">
                <Input
                  type="text"
                  value={(column.getFilterValue() as string) ?? ""}
                  onChange={(e) => column.setFilterValue(e.target.value)}
                  placeholder={`Filter ${title}...`}
                  className="h-8 text-sm p-1 w-full"
                />
              </div>
            )}
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Faceted Multi-Select Filter
// ---------------------------------------------------------------------------

function FacetedFilter({
  column,
  title,
}: {
  column: Column<ConversationSummary, unknown>;
  title: string;
}) {
  const facets = column.getFacetedUniqueValues();
  const selectedValues = new Set(
    column.getFilterValue() as string[] | undefined
  );

  const sortedOptions = useMemo(() => {
    return Array.from(facets.keys()).sort();
  }, [facets]);

  const toggleValue = (value: string) => {
    const next = new Set(selectedValues);
    if (next.has(value)) {
      next.delete(value);
    } else {
      next.add(value);
    }
    column.setFilterValue(next.size > 0 ? Array.from(next) : undefined);
  };

  return (
    <Command>
      <CommandInput
        placeholder={`Search ${title}...`}
        className="h-8 text-sm"
      />
      <CommandList className="max-h-48">
        <CommandEmpty>No values found.</CommandEmpty>
        <CommandGroup>
          {sortedOptions.map((value) => {
            const isSelected = selectedValues.has(value);
            const count = facets.get(value);
            return (
              <CommandItem
                key={value}
                onSelect={() => toggleValue(value)}
                className="cursor-pointer"
              >
                <div
                  className={cn(
                    "mr-2 flex h-4 w-4 items-center justify-center rounded-sm border border-primary",
                    isSelected
                      ? "bg-primary text-primary-foreground"
                      : "[&_svg]:invisible"
                  )}
                >
                  <Check className="h-3 w-3" />
                </div>
                <span>{value}</span>
                {count != null && (
                  <span className="ml-auto text-muted-foreground font-mono text-xs">
                    {count}
                  </span>
                )}
              </CommandItem>
            );
          })}
        </CommandGroup>
        {selectedValues.size > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup>
              <CommandItem
                onSelect={() => column.setFilterValue(undefined)}
                className="justify-center text-center cursor-pointer"
              >
                Clear filters
              </CommandItem>
            </CommandGroup>
          </>
        )}
      </CommandList>
    </Command>
  );
}

// ---------------------------------------------------------------------------
// Date Range Filter
// ---------------------------------------------------------------------------

function DateRangeFilter({
  column,
}: {
  column: Column<ConversationSummary, unknown>;
}) {
  const filterValue = column.getFilterValue() as
    | [string, string]
    | undefined;
  const start = filterValue?.[0] ?? "";
  const end = filterValue?.[1] ?? "";

  const setRange = (s: string, e: string) => {
    column.setFilterValue(s || e ? [s, e] : undefined);
  };

  return (
    <div className="p-2 space-y-2">
      <div className="space-y-1">
        <label className="text-xs text-muted-foreground">From</label>
        <Input
          type="date"
          value={start}
          onChange={(e) => setRange(e.target.value, end)}
          className="h-8 text-sm"
        />
      </div>
      <div className="space-y-1">
        <label className="text-xs text-muted-foreground">To</label>
        <Input
          type="date"
          value={end}
          onChange={(e) => setRange(start, e.target.value)}
          className="h-8 text-sm"
        />
      </div>
      {(start || end) && (
        <Button
          variant="ghost"
          size="sm"
          className="w-full h-7 text-xs"
          onClick={() => column.setFilterValue(undefined)}
        >
          Clear
        </Button>
      )}
    </div>
  );
}
