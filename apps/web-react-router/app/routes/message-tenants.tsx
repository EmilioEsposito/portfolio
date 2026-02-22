import type { Route } from "./+types/message-tenants";
import { useState, useEffect, useMemo } from "react";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Skeleton } from "~/components/ui/skeleton";
import { Separator } from "~/components/ui/separator";
import { AuthGuard } from "~/components/auth-guard";
import {
  AlertCircle,
  Check,
  Filter,
  FilterX,
  ArrowUpDown,
  ArrowDown,
  ArrowUp,
  MessageCircle,
} from "lucide-react";
import { cn } from "~/lib/utils";
import { DataTable } from "~/components/data-table";
import type {
  RowSelectionState,
  ColumnDef,
  Column,
  Row,
  HeaderContext,
  CellContext,
  FilterFn,
} from "@tanstack/react-table";
import { Textarea } from "~/components/ui/textarea";
import { Button } from "~/components/ui/button";
import { Checkbox } from "~/components/ui/checkbox";
import { Input } from "~/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "~/components/ui/dialog";
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

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Message Tenants | Emilio Esposito" },
    {
      name: "description",
      content: "Send SMS messages to selected tenants",
    },
  ];
}

interface Tenant {
  Property?: string | null;
  "Active Lease"?: string | null;
  Unit?: string | null;
  Company?: string | null;
  Role?: string | null;
  "Lease Start Date"?: string | null;
  "Lease End Date"?: string | null;
  Email?: string | null;
  "Phone Number"?: string | null;
  "First Name"?: string | null;
  "Last Name"?: string | null;
  external_id?: string | null;
  [key: string]: any;
}

// Columns that use multi-select faceted filtering
const FACETED_COLUMNS = new Set([
  "Property",
  "Unit #",
  "Company",
  "Role",
  "Active Lease",
]);

// Custom filter function for multi-select columns
const facetedFilterFn: FilterFn<Tenant> = (row, columnId, filterValue: string[]) => {
  if (!filterValue || filterValue.length === 0) return true;
  const cellValue = String(row.getValue(columnId) ?? "");
  return filterValue.includes(cellValue);
};

// Column display priority and visibility
const COLUMN_CONFIG: Record<string, { priority: number; mobile: boolean; size: number }> = {
  "First Name": { priority: 1, mobile: true, size: 100 },
  "Last Name": { priority: 2, mobile: false, size: 100 },
  Property: { priority: 3, mobile: true, size: 50 },
  "Unit #": { priority: 4, mobile: true, size: 25 },
  "Active Lease": { priority: 5, mobile: true, size: 30 },
  "Lease Start Date": { priority: 6, mobile: true, size: 50 },
  "Lease End Date": { priority: 7, mobile: true, size: 50 },
  Company: { priority: 8, mobile: false, size: 120 },
  Role: { priority: 9, mobile: false, size: 80 },
  "Phone Number": { priority: 10, mobile: false, size: 120 },
  Email: { priority: 11, mobile: false, size: 150 },
  "Image URL": { priority: 12, mobile: false, size: 100 },
  external_id: { priority: 13, mobile: false, size: 120 },
};

export default function MessageTenantsPage() {
  return (
    <AuthGuard
      requireDomain="serniacapital.com"
      message="Sign in with a Sernia Capital account to send SMS messages to tenants"
      icon={<MessageCircle className="w-16 h-16 text-muted-foreground" />}
    >
      <MessageTenantsContent />
    </AuthGuard>
  );
}

function MessageTenantsContent() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<{
    type: "success" | "error" | "loading";
    message: string;
  } | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [message, setMessage] = useState("");
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [serniaPhoneNumber, setSerniaPhoneNumber] = useState<string | null>(null);
  const [showAllColumns, setShowAllColumns] = useState(false);
  const [contactFilter, setContactFilter] = useState<"active" | "all">("active");

  // Fetch data once on mount
  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);
      setError(null);
      setStatus(null);

      try {
        // Fetch both in parallel
        const [serniaContactResponse, tenantsResponse] = await Promise.all([
          fetch("/api/contacts/slug/sernia"),
          fetch("/api/open_phone/tenants"),
        ]);

        // Process Sernia contact
        if (serniaContactResponse.ok) {
          const serniaContactData = await serniaContactResponse.json();
          if (serniaContactData?.phone_number) {
            setSerniaPhoneNumber(serniaContactData.phone_number);
          } else {
            console.error("Sernia contact info fetched but no phone number found.");
          }
        } else {
          console.error("Failed to fetch Sernia contact info", serniaContactResponse.status);
        }

        // Process tenants
        if (tenantsResponse.status === 401 || tenantsResponse.status === 403) {
          throw new Error("Unauthorized: You do not have permission to view this page.");
        }
        if (!tenantsResponse.ok) {
          let errorDetail = `HTTP error! ${tenantsResponse.status}`;
          try {
            const errorData = await tenantsResponse.json().catch(() => ({}));
            errorDetail = errorData.detail || errorDetail;
          } catch {
            /* Ignore */
          }
          throw new Error(errorDetail);
        }

        const tenantData: Tenant[] = await tenantsResponse.json();
        if (!Array.isArray(tenantData) || tenantData.length === 0) {
          setTenants([]);
          return;
        }

        const processedData = tenantData.map((tenant) => ({
          ...tenant,
          "Phone Number": tenant["Phone Number"]?.replace(/[^+\d]/g, ""),
          "Active Lease": tenant["Active Lease"] ? "Yes" : "No",
        }));

        setTenants(processedData);
      } catch (fetchError: any) {
        console.error("Error during data fetching:", fetchError);
        setError(fetchError.message || "Failed to fetch data.");
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, []);

  // Filter data based on contactFilter
  const filteredTenants = useMemo(() => {
    if (contactFilter === "all") return tenants;
    // "active" — active tenants only (default)
    return tenants.filter(
      (t) => t["Role"] === "Tenant" && t["Active Lease"] === "Yes"
    );
  }, [tenants, contactFilter]);

  // Generate columns reactively (no re-fetch needed for column toggle)
  const columns = useMemo((): ColumnDef<Tenant>[] => {
    if (tenants.length === 0) return [];

    const keysFromData = Object.keys(tenants[0]);

    const sortedKeys = keysFromData
      .filter((key) => {
        const config = COLUMN_CONFIG[key];
        return config ? showAllColumns || config.mobile : true;
      })
      .sort((a, b) => {
        const priorityA = COLUMN_CONFIG[a]?.priority ?? 999;
        const priorityB = COLUMN_CONFIG[b]?.priority ?? 999;
        return priorityA - priorityB;
      });

    return [
      // Selection column
      {
        id: "select",
        header: ({ table }: HeaderContext<Tenant, unknown>) => (
          <Checkbox
            checked={
              table.getIsAllPageRowsSelected() ||
              (table.getIsSomePageRowsSelected() && "indeterminate")
            }
            onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
            aria-label="Select all"
            className="translate-y-[2px]"
          />
        ),
        cell: ({ row }: CellContext<Tenant, unknown>) => (
          <Checkbox
            checked={row.getIsSelected()}
            onCheckedChange={(value) => row.toggleSelected(!!value)}
            aria-label="Select row"
            className="translate-y-[2px]"
          />
        ),
        enableSorting: false,
        enableHiding: false,
        size: 40,
      },
      // Data columns
      ...sortedKeys.map((key): ColumnDef<Tenant> => {
        const config = COLUMN_CONFIG[key];
        const isFaceted = FACETED_COLUMNS.has(key);

        return {
          accessorKey: key,
          filterFn: isFaceted ? facetedFilterFn : "includesString",
          header: ({ column }: { column: Column<Tenant, unknown> }) => (
            <ColumnHeader columnKey={key} column={column} isFaceted={isFaceted} />
          ),
          cell: ({ row }: { row: Row<Tenant> }) => {
            const value = row.getValue(key);
            const isSticky = key === "First Name";
            const stickyClasses = isSticky
              ? "sticky left-0 z-20 bg-background border-r shadow-sm"
              : "";

            if (key === "Active Lease") {
              return (
                <div className={stickyClasses}>
                  {value === "Yes" ? (
                    <span className="text-green-600 text-xs sm:text-sm">Yes</span>
                  ) : (
                    <span className="text-red-600 text-xs sm:text-sm">No</span>
                  )}
                </div>
              );
            }
            return (
              <div
                className={`truncate max-w-[80px] sm:max-w-[120px] lg:max-w-[150px] text-xs sm:text-sm ${stickyClasses}`}
              >
                {String(value ?? "")}
              </div>
            );
          },
          enableSorting: true,
          size: config?.size || 80,
        };
      }),
    ];
  }, [tenants, showAllColumns]);

  // Derive selected tenants using stable IDs (not indices)
  const selectedTenants = useMemo(() => {
    const selectedIds = new Set(Object.keys(rowSelection));
    return filteredTenants.filter((t) =>
      selectedIds.has(t.external_id ?? t["Phone Number"] ?? "")
    );
  }, [rowSelection, filteredTenants]);

  // Clear selections when dataset semantically changes
  useEffect(() => {
    setRowSelection({});
  }, [contactFilter]);

  const handleSendClick = () => {
    if (selectedTenants.length === 0 || !message.trim() || !serniaPhoneNumber) {
      if (!serniaPhoneNumber) {
        setStatus({
          type: "error",
          message: "From phone number (Sernia contact) is not loaded. Cannot send messages.",
        });
      }
      return;
    }

    // Validate all selected tenants have valid phone numbers
    const tenantsWithoutPhone = selectedTenants.filter((t) => {
      const phone = t["Phone Number"];
      return !phone || !/\+?\d{10,}/.test(phone.replace(/[^+\d]/g, ""));
    });

    if (tenantsWithoutPhone.length > 0) {
      const names = tenantsWithoutPhone
        .map((t) => `${t["First Name"] || ""} ${t["Last Name"] || ""}`.trim())
        .join(", ");
      setStatus({
        type: "error",
        message: `Cannot send: ${tenantsWithoutPhone.length} selected tenant(s) have no valid phone number: ${names}`,
      });
      return;
    }

    setStatus(null);
    setShowConfirmation(true);
  };

  const handleConfirmedSubmit = async () => {
    if (!serniaPhoneNumber) {
      setStatus({
        type: "error",
        message: "Sernia phone number is not available. Cannot send messages.",
      });
      setIsSending(false);
      setShowConfirmation(false);
      return;
    }
    setShowConfirmation(false);
    setIsSending(true);
    setStatus({
      type: "loading",
      message: `Sending messages to ${selectedTenants.length} tenant(s)...`,
    });

    let successes = 0;
    let failures = 0;
    const failedDetails: { name: string; phone: string; error: string }[] = [];

    const results = await Promise.allSettled(
      selectedTenants.map(async (tenant) => {
        let phoneNumber = tenant["Phone Number"];
        const firstName = tenant["First Name"] || "";
        const lastName = tenant["Last Name"] || "";
        const name = `${firstName} ${lastName}`.trim();

        if (phoneNumber && !phoneNumber.startsWith("+1")) {
          phoneNumber = "+1" + phoneNumber;
        }

        if (!phoneNumber || !/\+1\d{10}$/.test(phoneNumber)) {
          throw new Error(
            `Invalid or missing phone number for ${name}: ${phoneNumber}`
          );
        }

        const response = await fetch("/api/open_phone/send_message", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            to_phone_number: phoneNumber,
            message: message,
            from_phone_number: serniaPhoneNumber,
          }),
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(
            errorData.detail || `API Error (${response.status}) for ${name}`
          );
        }
        return { name };
      })
    );

    results.forEach((result, index) => {
      const tenant = selectedTenants[index];
      const firstName = tenant["First Name"] || "";
      const lastName = tenant["Last Name"] || "";
      const name = `${firstName} ${lastName}`.trim();
      const phone = tenant["Phone Number"] || "N/A";

      if (result.status === "fulfilled") {
        successes++;
      } else {
        failures++;
        const errorMessage =
          result.reason instanceof Error ? result.reason.message : "Unknown error";
        failedDetails.push({ name, phone, error: errorMessage });
        console.error(`Failed to send to ${name} (${phone}):`, result.reason);
      }
    });

    setIsSending(false);

    if (failures > 0) {
      const detailedErrors = failedDetails
        .map((f) => `${f.name} (${f.phone}): ${f.error}`)
        .join("\n");
      setStatus({
        type: "error",
        message: `${failures} message(s) failed to send. ${successes} sent successfully.\nErrors:\n${detailedErrors}`,
      });
    } else {
      setStatus({
        type: "success",
        message: `Successfully sent messages to all ${successes} selected tenant(s)!`,
      });
      setMessage("");
      setRowSelection({});
    }
  };

  return (
    <div className="container mx-auto px-4 sm:px-6 lg:px-8 py-4 sm:py-6 lg:py-8">
      <h1 className="text-2xl sm:text-3xl font-bold mb-4 sm:mb-6">
        Message Tenants
      </h1>

      {/* Loading State */}
      {isLoading && (
        <div>
          <Skeleton className="h-6 sm:h-8 w-1/3 sm:w-1/4 mb-4" />
          <Skeleton className="h-80 sm:h-96 w-full" />
        </div>
      )}

      {/* Error State */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error Fetching Data</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Data Loaded State */}
      {!isLoading && !error && (
        <div className="space-y-4 sm:space-y-6">
          {/* Filter and Column View Toggles */}
          {tenants.length > 0 && (
            <div className="flex flex-col sm:flex-row gap-3 sm:gap-4">
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">Show:</span>
                <Button
                  variant={contactFilter === "active" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setContactFilter("active")}
                  className="text-xs"
                >
                  Active Tenants
                </Button>
                <Button
                  variant={contactFilter === "all" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setContactFilter("all")}
                  className="text-xs"
                >
                  All Contacts
                </Button>
              </div>

              <div className="flex justify-between items-center sm:hidden">
                <span className="text-sm text-muted-foreground">
                  {showAllColumns ? "All Columns" : "Mobile View"}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowAllColumns(!showAllColumns)}
                  className="text-xs"
                >
                  {showAllColumns ? "Mobile View" : "All Columns"}
                </Button>
              </div>
            </div>
          )}

          {columns.length > 0 && filteredTenants.length > 0 ? (
            <DataTable
              columns={columns}
              data={filteredTenants}
              rowSelection={rowSelection}
              onRowSelectionChange={setRowSelection}
              getRowId={(row: Tenant) =>
                row.external_id ?? row["Phone Number"] ?? ""
              }
            />
          ) : (
            <p>No tenant data found or could not generate columns.</p>
          )}

          {/* Message Input Area */}
          {filteredTenants.length > 0 && (
            <div className="space-y-3 sm:space-y-4">
              <div>
                <label className="block text-sm font-medium text-foreground mb-2">
                  Message
                </label>
                <Textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Type your message here..."
                  className="resize-none text-sm sm:text-base"
                  rows={3}
                  disabled={isSending || !serniaPhoneNumber}
                />
                {!serniaPhoneNumber && !isLoading && (
                  <p className="text-xs text-destructive mt-1">
                    From phone number could not be loaded. Sending disabled.
                  </p>
                )}
              </div>

              <Button
                onClick={handleSendClick}
                disabled={
                  selectedTenants.length === 0 ||
                  !message.trim() ||
                  isSending ||
                  !serniaPhoneNumber
                }
                className="w-full sm:w-auto text-sm sm:text-base"
              >
                {isSending
                  ? "Sending..."
                  : `Send Message to ${selectedTenants.length} Selected`}
              </Button>
            </div>
          )}

          {/* Status Alert */}
          {status && (
            <Alert
              className={`mt-4 ${
                status.type === "error"
                  ? "bg-destructive/15 text-destructive"
                  : status.type === "loading"
                    ? "bg-muted text-muted-foreground"
                    : "bg-primary/15 text-primary"
              }`}
            >
              <AlertCircle className="h-4 w-4" />
              <AlertTitle className="text-sm sm:text-base">
                {status.type === "error"
                  ? "Error"
                  : status.type === "loading"
                    ? "Processing"
                    : "Success"}
              </AlertTitle>
              <AlertDescription className="whitespace-pre-wrap text-xs sm:text-sm">
                {status.message}
              </AlertDescription>
            </Alert>
          )}
        </div>
      )}

      {/* Confirmation Dialog */}
      {selectedTenants.length > 0 && (
        <Dialog open={showConfirmation} onOpenChange={setShowConfirmation}>
          <DialogContent className="max-w-sm sm:max-w-md">
            <DialogHeader>
              <DialogTitle className="text-lg sm:text-xl">
                Confirm Message
              </DialogTitle>
              <DialogDescription className="text-sm">
                Are you sure you want to send the following message to{" "}
                {selectedTenants.length} selected tenant(s)?
              </DialogDescription>
            </DialogHeader>

            <div className="mt-4 p-3 bg-muted rounded-md space-y-3 max-h-48 sm:max-h-60 overflow-y-auto">
              <p className="text-sm font-medium text-foreground">
                Recipients ({selectedTenants.length}):
              </p>
              <ul className="space-y-1">
                {selectedTenants.map((t) => (
                  <li
                    key={t.external_id ?? t["Phone Number"]}
                    className="text-xs sm:text-sm text-muted-foreground flex justify-between"
                  >
                    <span className="font-medium">
                      {t["First Name"] || ""} {t["Last Name"] || ""}
                    </span>
                    <span className="font-mono text-xs">
                      {t["Phone Number"] || "NO PHONE"}
                    </span>
                  </li>
                ))}
              </ul>
              <Separator />
              <p className="text-sm font-medium text-foreground">Message:</p>
              <p className="text-xs sm:text-sm text-muted-foreground whitespace-pre-wrap">
                {message}
              </p>
            </div>

            <DialogFooter className="mt-6 flex-col sm:flex-row gap-2">
              <Button
                variant="outline"
                onClick={() => setShowConfirmation(false)}
                disabled={isSending}
                className="w-full sm:w-auto"
              >
                Cancel
              </Button>
              <Button
                onClick={handleConfirmedSubmit}
                disabled={isSending || !serniaPhoneNumber}
                className="w-full sm:w-auto"
              >
                {isSending ? "Sending..." : "Confirm & Send"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

// --- Column Header with Sort + Filter ---

function ColumnHeader({
  columnKey,
  column,
  isFaceted,
}: {
  columnKey: string;
  column: Column<Tenant, unknown>;
  isFaceted: boolean;
}) {
  const isFiltered = column.getIsFiltered();
  const canSort = column.getCanSort();

  return (
    <div
      className={`flex items-center space-x-1 justify-between pr-1 ${
        columnKey === "First Name"
          ? "sticky left-0 z-20 bg-background border-r shadow-sm"
          : ""
      }`}
    >
      <Button
        variant="ghost"
        size="sm"
        className="-ml-3 h-8 p-1 data-[state=open]:bg-accent text-left justify-start grow truncate text-xs sm:text-sm"
        onClick={() =>
          canSort && column.toggleSorting(column.getIsSorted() === "asc")
        }
        disabled={!canSort}
      >
        <span className="truncate">{columnKey}</span>
        {canSort &&
          (column.getIsSorted() === "desc" ? (
            <ArrowDown className="ml-1 h-3 w-3 sm:ml-2 sm:h-4 sm:w-4" />
          ) : column.getIsSorted() === "asc" ? (
            <ArrowUp className="ml-1 h-3 w-3 sm:ml-2 sm:h-4 sm:w-4" />
          ) : (
            <ArrowUpDown className="ml-1 h-3 w-3 sm:ml-2 sm:h-4 sm:w-4 opacity-30" />
          ))}
      </Button>

      <Popover>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            className={`h-5 w-5 sm:h-6 sm:w-6 p-1 ${isFiltered ? "text-primary" : ""}`}
          >
            {isFiltered ? (
              <FilterX className="h-3 w-3 sm:h-4 sm:w-4" />
            ) : (
              <Filter className="h-3 w-3 sm:h-4 sm:w-4" />
            )}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-48 sm:w-64 p-0" align="start">
          {isFaceted ? (
            <FacetedFilter column={column} title={columnKey} />
          ) : (
            <div className="p-2">
              <Input
                type="text"
                value={(column.getFilterValue() as string) ?? ""}
                onChange={(event) => column.setFilterValue(event.target.value)}
                placeholder={`Filter ${columnKey}...`}
                className="h-8 text-sm p-1 w-full"
              />
            </div>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}

// --- Faceted Multi-Select Filter (TanStack + shadcn standard pattern) ---

function FacetedFilter({
  column,
  title,
}: {
  column: Column<Tenant, unknown>;
  title: string;
}) {
  const facets = column.getFacetedUniqueValues();
  const selectedValues = new Set(column.getFilterValue() as string[] | undefined);

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
      <CommandInput placeholder={`Search ${title}...`} className="h-8 text-sm" />
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
