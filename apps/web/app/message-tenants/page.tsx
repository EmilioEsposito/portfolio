'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton"; // For loading state
import { AlertCircle, Filter, FilterX, ArrowUpDown, ArrowDown, ArrowUp } from "lucide-react";
import { DataTable } from "@/components/data-table"; // Import the DataTable component
import { RowSelectionState, ColumnDef, Column, Row, HeaderContext, CellContext } from '@tanstack/react-table'; // Import RowSelectionState, ColumnDef, and FilterFn
import { Textarea } from "@/components/ui/textarea"; // Import Textarea
import { Button } from "@/components/ui/button"; // Import Button
import { Checkbox } from "@/components/ui/checkbox"; // Import Checkbox for selection column
import { Input } from "@/components/ui/input"; // Keep Input for filtering
import { 
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"; // Import Dialog components
import { 
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"; // Import Popover
import { cn } from "@/lib/utils"; // Import cn for conditional classnames

// Define Tenant interface more explicitly
interface Tenant {
  // Known keys from column lists (make optional with ?)
  'Property'?: string | null;
  'Active Lease'?: boolean | null;
  'Unit'?: string | null;
  'Company'?: string | null;
  'Role'?: string | null;
  'Lease Start Date'?: string | null;
  'Lease End Date'?: string | null;
  'Email'?: string | null;
  'Phone Number'?: string | null;
  'First Name'?: string | null;
  'Last Name'?: string | null;
  'external_id'?: string | null;
  
  // Allow any other keys dynamically
  [key: string]: any; 
}

export default function MessageTenantsPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [columns, setColumns] = useState<ColumnDef<Tenant>[]>([]); // State for dynamic columns
  const [isLoading, setIsLoading] = useState(true);
  const [isSending, setIsSending] = useState(false); // Loading state for sending
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<{ type: 'success' | 'error' | 'loading', message: string } | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({}); // State for selected rows
  const [message, setMessage] = useState(''); // State for message input
  const [showConfirmation, setShowConfirmation] = useState(false);

  useEffect(() => {
    const fetchTenants = async () => {
      setIsLoading(true);
      setError(null);
      setStatus(null); // Clear status on new fetch
      setColumns([]); // Clear columns on new fetch
      setTenants([]); // Clear tenants initially

      try {
        // --- Fetch Data --- 
        const response = await fetch('/api/open_phone/tenants');
        
        if (response.status === 401 || response.status === 403) {
          throw new Error('Unauthorized: You do not have permission to view this page.');
        }
        if (!response.ok) {
          let errorDetail = `HTTP error! ${response.status}`;
          try {
            const errorData = await response.json().catch(() => ({}));
            errorDetail = errorData.detail || errorDetail;
          } catch (jsonError) { /* Ignore */ }
          throw new Error(errorDetail);
        }
        
        const data: Tenant[] = await response.json();
        
        if (!Array.isArray(data) || data.length === 0) {
          setTenants([]); // Set empty tenants if data is invalid or empty
          console.log("No tenant data received or data is not an array.");
          // No need to generate columns or unique values
          return; // Exit early
        }
        
        // --- Process Data --- 
        const processedData = data.map(tenant => ({
          ...tenant,
          'Phone Number': tenant['Phone Number']?.replace(/[^+\d]/g, '') 
        }));
        
        setTenants(processedData);

        // --- Generate Columns Dynamically --- 
        const keysFromData = Object.keys(processedData[0]);
        const generatedColumns: ColumnDef<Tenant>[] = [
          // Selection Column (always first)
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
          },
          // Data Columns (Dynamically Generated)
          ...keysFromData.map((key): ColumnDef<Tenant> => {
            const columnDef: ColumnDef<Tenant> = {
                accessorKey: key,
                header: ({ column }: { column: Column<Tenant, unknown> }) => {
                     const isFiltered = column.getIsFiltered();
                     const canSort = column.getCanSort();

                    return (
                        <div className="flex items-center space-x-1 justify-between pr-1"> {/* Added justify-between */} 
                          {/* Clickable Title for Sorting */}
                          <Button 
                            variant="ghost"
                            size="sm"
                            className="-ml-3 h-8 p-1 data-[state=open]:bg-accent text-left justify-start flex-grow truncate" // Adjusted classes
                            onClick={() => canSort && column.toggleSorting(column.getIsSorted() === 'asc')} 
                            disabled={!canSort}
                          >
                              <span>{key}</span>
                              {canSort && (
                                  column.getIsSorted() === 'desc' ? <ArrowDown className="ml-2 h-4 w-4" /> 
                                  : column.getIsSorted() === 'asc' ? <ArrowUp className="ml-2 h-4 w-4" /> 
                                  : <ArrowUpDown className="ml-2 h-4 w-4 opacity-30" /> // Dimmed default arrow
                              )}
                          </Button>

                          {/* Filter Trigger */} 
                          <Popover>
                            <PopoverTrigger asChild>
                              <Button variant="ghost" className={`h-6 w-6 p-1 ${isFiltered ? 'text-primary' : ''}`}>
                                {isFiltered ? <FilterX className="h-4 w-4" /> : <Filter className="h-4 w-4" />}
                              </Button>
                            </PopoverTrigger>
                            <PopoverContent className="w-64 p-2" align="start">
                              <Input
                                type="text"
                                value={(column.getFilterValue() as string) ?? ''}
                                onChange={(event) => column.setFilterValue(event.target.value)}
                                placeholder={`Filter ${key}...`}
                                className="h-8 text-sm p-1 w-full"
                              />
                            </PopoverContent>
                          </Popover>
                        </div>
                      )
                },
                cell: ({ row }: { row: Row<Tenant> }) => {
                    const value = row.getValue(key);
                    // Special rendering for boolean 'Active Lease'
                    if (key === 'Active Lease') {
                        return value ? <span className="text-green-600">Yes</span> : <span className="text-red-600">No</span>;
                    }
                    // Default string rendering
                    return <div className="truncate max-w-[150px]">{String(value)}</div>;
                },
                // Explicitly enable sorting for all generated data columns
                enableSorting: true, 
            };
            
            return columnDef;
        }),
      ];
      setColumns(generatedColumns);

      } catch (computeError) {
        console.error("Error computing unique values or generating columns:", computeError);
        setError("Failed to prepare table columns after fetching data.");
        setColumns([]); // Ensure columns are cleared on error
      } finally {
        setIsLoading(false);
      }
    };

    fetchTenants();
  }, []); // Keep useEffect dependency array empty

  // Memoize selected tenants to avoid recalculation on every render
  const selectedTenants = useMemo(() => {
     const selectedIndices = Object.keys(rowSelection).map(Number);
     return selectedIndices.map(index => tenants[index]).filter(Boolean);
  }, [rowSelection, tenants]);

  const handleSendClick = () => {
    if (selectedTenants.length === 0 || !message.trim()) return;
    setStatus(null); // Clear previous status
    setShowConfirmation(true);
  }

  const handleConfirmedSubmit = async () => {
    setShowConfirmation(false);
    setIsSending(true);
    setStatus({ type: 'loading', message: `Sending messages to ${selectedTenants.length} tenant(s)...` });

    let successes = 0;
    let failures = 0;
    const failedDetails: { name: string, phone: string, error: string }[] = [];

    const results = await Promise.allSettled(
      selectedTenants.map(async (tenant) => {
        let phoneNumber = tenant['Phone Number']; 
        const firstName = tenant['First Name'] || '';
        const lastName = tenant['Last Name'] || '';
        const name = `${firstName} ${lastName}`.trim();
        const from_phone_number = '+14129101989';

        if (phoneNumber && !phoneNumber.startsWith('+1')) {
            phoneNumber = '+1' + phoneNumber;
        }

        if (!phoneNumber || !/\+1\d{10}$/.test(phoneNumber)) {
            throw new Error(`Invalid or missing phone number for ${name}: ${phoneNumber}`);
        }

        const response = await fetch('/api/open_phone/send_message', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            to_phone_number: phoneNumber,
            message: message,       
            from_phone_number: from_phone_number,
          }),
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail || `API Error (${response.status}) for ${name}`);
        }
        return { name }; 
      })
    );

    // Process results
    results.forEach((result, index) => {
      const tenant = selectedTenants[index];
       const firstName = tenant['First Name'] || '';
        const lastName = tenant['Last Name'] || '';
        const name = `${firstName} ${lastName}`.trim();
      const phone = tenant['Phone Number'] || 'N/A';

      if (result.status === 'fulfilled') {
        successes++;
      } else {
        failures++;
        const errorMessage = result.reason instanceof Error ? result.reason.message : 'Unknown error';
        failedDetails.push({ name, phone, error: errorMessage });
        console.error(`Failed to send to ${name} (${phone}):`, result.reason);
      }
    });

    setIsSending(false);

    // Set final status message
    if (failures > 0) {
        let detailedErrors = failedDetails.map(f => `${f.name} (${f.phone}): ${f.error}`).join('\n');
        setStatus({
            type: 'error',
            message: `${failures} message(s) failed to send. ${successes} sent successfully.\nErrors:\n${detailedErrors}`
        });
    } else {
        setStatus({
            type: 'success',
            message: `Successfully sent messages to all ${successes} selected tenant(s)!`
        });
        // Optionally clear form on complete success
        setMessage('');
        setRowSelection({}); 
    }
  };

  return (
    <div className="container mx-auto px-8 py-8">
      <h1 className="text-3xl font-bold mb-6">Message Tenants</h1>

      {/* Loading State */}
      {isLoading && (
        <div>
          <Skeleton className="h-8 w-1/4 mb-4" />
          <Skeleton className="h-96 w-full" /> {/* Skeleton for table area */}
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
        <div className="space-y-6">
          {/* Render DataTable only when columns are generated */}
          {columns.length > 0 && tenants.length > 0 ? (
            <DataTable 
              columns={columns} 
              data={tenants} 
              rowSelection={rowSelection}
              onRowSelectionChange={setRowSelection}
            />
          ) : (
            <p>No tenant data found or could not generate columns.</p> 
          )}

          {/* Message Input Area */}
          {/* Ensure tenants exist before showing message area */}
          {tenants.length > 0 && (
            <div className="space-y-4">
               <div>
                <label className="block text-sm font-medium text-foreground mb-2">Message</label>
                <Textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Type your message here..."
                  className="resize-none"
                  rows={4}
                  disabled={isSending}
                />
              </div>
              
              <Button 
                onClick={handleSendClick}
                disabled={selectedTenants.length === 0 || !message.trim() || isSending}
                className="w-full sm:w-auto"
              >
                {isSending ? 'Sending...' : `Send Message to ${selectedTenants.length} Selected`}
              </Button>
            </div>
          )}

          {/* Status Alert */} 
          {status && (
            <Alert className={`mt-4 ${ 
              status.type === 'error' ? 'bg-destructive/15 text-destructive' 
              : status.type === 'loading' ? 'bg-muted text-muted-foreground' 
              : 'bg-primary/15 text-primary' // Success styling
            }`}>
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>{status.type === 'error' ? 'Error' : status.type === 'loading' ? 'Processing' : 'Success'}</AlertTitle>
              <AlertDescription className="whitespace-pre-wrap">{status.message}</AlertDescription> 
            </Alert>
          )}
        </div>
      )}

      {/* Confirmation Dialog */}
      {/* Check if selectedTenants is not empty before rendering details */}
      {selectedTenants.length > 0 && (
        <Dialog 
          open={showConfirmation} 
          onOpenChange={setShowConfirmation}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Confirm Message</DialogTitle>
              <DialogDescription>
                Are you sure you want to send the following message to {selectedTenants.length} selected tenant(s)?
              </DialogDescription>
            </DialogHeader>
            
            <div className="mt-4 p-3 bg-muted rounded-md space-y-2 max-h-60 overflow-y-auto">
              <p className="text-sm font-medium text-foreground">Recipients:</p>
              <p className="text-sm text-muted-foreground">
                {/* Safely access dynamic keys */}
                {selectedTenants.map(t => `${t['First Name'] || ''} ${t['Last Name'] || ''} (${t['Phone Number'] || 'N/A'})`).join(', ')}
              </p>
              <p className="text-sm font-medium text-foreground mt-2">Message:</p>
              <p className="mt-1 text-sm text-muted-foreground whitespace-pre-wrap">{message}</p>
            </div>

            <DialogFooter className="mt-6">
              <Button
                variant="outline"
                onClick={() => setShowConfirmation(false)}
                disabled={isSending}
              >
                Cancel
              </Button>
              <Button
                onClick={handleConfirmedSubmit}
                disabled={isSending}
              >
                {isSending ? 'Sending...' : 'Confirm & Send'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
       )}
    </div>
  );
} 