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
  const [isLoading, setIsLoading] = useState(true); // This will now cover both tenant and Sernia phone fetching
  const [isSending, setIsSending] = useState(false); // Loading state for sending
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<{ type: 'success' | 'error' | 'loading', message: string } | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({}); // State for selected rows
  const [message, setMessage] = useState(''); // State for message input
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [serniaPhoneNumber, setSerniaPhoneNumber] = useState<string | null>(null); // State for Sernia's phone number
  const [showAllColumns, setShowAllColumns] = useState(false); // State to toggle column visibility on mobile
  const [showOnlyTenants, setShowOnlyTenants] = useState(true); // State to filter for tenants only

  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);
      setError(null);
      setStatus(null);
      setColumns([]);
      setTenants([]);
      setSerniaPhoneNumber(null); // Reset on new fetch

      try {
        // --- Fetch Sernia Contact Info --- 
        const serniaContactResponse = await fetch('/api/contacts/slug/sernia');
        if (!serniaContactResponse.ok) {
          // Handle error fetching Sernia contact, but don't necessarily block tenant fetching
          console.error('Failed to fetch Sernia contact info', serniaContactResponse.status);
          // Optionally set a specific error or allow proceeding without it
        } else {
          const serniaContactData = await serniaContactResponse.json();
          if (serniaContactData && serniaContactData.phone_number) {
            setSerniaPhoneNumber(serniaContactData.phone_number);
          } else {
            console.error('Sernia contact info fetched but no phone number found.');
          }
        }

        // --- Fetch Tenants Data --- 
        const tenantsResponse = await fetch('/api/open_phone/tenants');
        
        if (tenantsResponse.status === 401 || tenantsResponse.status === 403) {
          throw new Error('Unauthorized: You do not have permission to view this page.');
        }
        if (!tenantsResponse.ok) {
          let errorDetail = `HTTP error! ${tenantsResponse.status}`;
          try {
            const errorData = await tenantsResponse.json().catch(() => ({}));
            errorDetail = errorData.detail || errorDetail;
          } catch (jsonError) { /* Ignore */ }
          throw new Error(errorDetail);
        }
        
        const tenantData: Tenant[] = await tenantsResponse.json();
        
        if (!Array.isArray(tenantData) || tenantData.length === 0) {
          setTenants([]);
          console.log("No tenant data received or data is not an array.");
          return;
        }
        
        const processedData = tenantData.map(tenant => ({
          ...tenant,
          'Phone Number': tenant['Phone Number']?.replace(/[^+\d]/g, '') 
        }));
        
        setTenants(processedData);

        const keysFromData = Object.keys(processedData[0]);
        
        // Define column priority and visibility for mobile
        const columnConfig = {
          // High priority - always visible
          'First Name': { priority: 1, mobile: true, size: 100 },
          'Last Name': { priority: 2, mobile: false, size: 100 },
          'Property': { priority: 3, mobile: true, size: 50 },
          'Unit #': { priority: 4, mobile: true, size: 25 },
          'Active Lease': { priority: 5, mobile: true, size: 30 },
          'Lease Start Date': { priority: 6, mobile: true, size: 50 },
          'Lease End Date': { priority: 7, mobile: true, size: 50 },
          
          'Company': { priority: 8, mobile: false, size: 120 },
          'Role': { priority: 9, mobile: false, size: 80 },
          
          'Phone Number': { priority: 10, mobile: false, size: 120 },
          'Email': { priority: 11, mobile: false, size: 150 },
          'Image URL': { priority: 12, mobile: false, size: 100 },
          'external_id': { priority: 13, mobile: false, size: 120 },
        };

        // Sort columns by priority and filter for mobile
        const sortedKeys = keysFromData
          .filter(key => {
            const config = columnConfig[key as keyof typeof columnConfig];
            // Show all columns if showAllColumns is true, otherwise only show mobile columns
            return config ? (showAllColumns || config.mobile) : true; // Default to visible if not configured
          })
          .sort((a, b) => {
            const configA = columnConfig[a as keyof typeof columnConfig];
            const configB = columnConfig[b as keyof typeof columnConfig];
            const priorityA = configA ? configA.priority : 999;
            const priorityB = configB ? configB.priority : 999;
            return priorityA - priorityB;
          });

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
            size: 40, // Smaller size for mobile
          },
          // Data Columns (Dynamically Generated)
          ...sortedKeys.map((key): ColumnDef<Tenant> => {
            const config = columnConfig[key as keyof typeof columnConfig];
            const columnDef: ColumnDef<Tenant> = {
                accessorKey: key,
                header: ({ column }: { column: Column<Tenant, unknown> }) => {
                     const isFiltered = column.getIsFiltered();
                     const canSort = column.getCanSort();

                    return (
                        <div className={`flex items-center space-x-1 justify-between pr-1 ${key === 'First Name' ? 'sticky left-0 z-20 bg-background border-r shadow-sm' : ''}`}> {/* Added sticky positioning for First Name */}
                          {/* Clickable Title for Sorting */}
                          <Button 
                            variant="ghost"
                            size="sm"
                            className="-ml-3 h-8 p-1 data-[state=open]:bg-accent text-left justify-start flex-grow truncate text-xs sm:text-sm" // Added responsive text sizing
                            onClick={() => canSort && column.toggleSorting(column.getIsSorted() === 'asc')} 
                            disabled={!canSort}
                          >
                              <span className="truncate">{key}</span>
                              {canSort && (
                                  column.getIsSorted() === 'desc' ? <ArrowDown className="ml-1 h-3 w-3 sm:ml-2 sm:h-4 sm:w-4" /> 
                                  : column.getIsSorted() === 'asc' ? <ArrowUp className="ml-1 h-3 w-3 sm:ml-2 sm:h-4 sm:w-4" /> 
                                  : <ArrowUpDown className="ml-1 h-3 w-3 sm:ml-2 sm:h-4 sm:w-4 opacity-30" /> // Dimmed default arrow
                              )}
                          </Button>

                          {/* Filter Trigger */} 
                          <Popover>
                            <PopoverTrigger asChild>
                              <Button variant="ghost" className={`h-5 w-5 sm:h-6 sm:w-6 p-1 ${isFiltered ? 'text-primary' : ''}`}>
                                {isFiltered ? <FilterX className="h-3 w-3 sm:h-4 sm:w-4" /> : <Filter className="h-3 w-3 sm:h-4 sm:w-4" />}
                              </Button>
                            </PopoverTrigger>
                            <PopoverContent className="w-48 sm:w-64 p-2" align="start">
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
                    const isSticky = key === 'First Name';
                    const stickyClasses = isSticky ? 'sticky left-0 z-20 bg-background border-r shadow-sm' : '';
                    
                    // Special rendering for boolean 'Active Lease'
                    if (key === 'Active Lease') {
                        return (
                          <div className={stickyClasses}>
                            {value ? <span className="text-green-600 text-xs sm:text-sm">Yes</span> : <span className="text-red-600 text-xs sm:text-sm">No</span>}
                          </div>
                        );
                    }
                    // Default string rendering with responsive sizing and sticky positioning for First Name
                    return (
                      <div className={`truncate max-w-[80px] sm:max-w-[120px] lg:max-w-[150px] text-xs sm:text-sm ${stickyClasses}`}>
                        {String(value)}
                      </div>
                    );
                },
                // Explicitly enable sorting for all generated data columns
                enableSorting: true,
                size: config?.size || 80, // Use configured size or default
            };
            
            return columnDef;
        }),
      ];
      setColumns(generatedColumns);

      } catch (computeError: any) { // Added : any for computeError type
        console.error("Error during data fetching or processing:", computeError);
        // Set a general error if specific error for tenants wasn't thrown before
        if (!error) {
            setError(computeError.message || "Failed to fetch or process data.");
        }
        setColumns([]);
      } finally {
        setIsLoading(false);
      }
    };

    fetchData(); // Renamed from fetchTenants to fetchData
  }, [showAllColumns]); // Add showAllColumns as dependency

  // Filter data based on showOnlyTenants
  const filteredTenants = useMemo(() => {
    if (!showOnlyTenants) return tenants;
    return tenants.filter(tenant => tenant['Role'] === 'Tenant');
  }, [tenants, showOnlyTenants]);

  // Memoize selected tenants to avoid recalculation on every render
  const selectedTenants = useMemo(() => {
     const selectedIndices = Object.keys(rowSelection).map(Number);
     return selectedIndices.map(index => filteredTenants[index]).filter(Boolean);
  }, [rowSelection, filteredTenants]);

  // Clear selections when filter changes to prevent stale indices
  useEffect(() => {
    setRowSelection({});
  }, [showOnlyTenants]);

  // Clear selections when column filters change
  const handleFiltersChange = React.useCallback(() => {
    setRowSelection({});
  }, []);

  const handleSendClick = () => {
    if (selectedTenants.length === 0 || !message.trim() || !serniaPhoneNumber) { // Check for serniaPhoneNumber
        if (!serniaPhoneNumber) {
            setStatus({ type: 'error', message: 'From phone number (Sernia contact) is not loaded. Cannot send messages.' });
        }
        return;
    }
    setStatus(null);
    setShowConfirmation(true);
  }

  const handleConfirmedSubmit = async () => {
    if (!serniaPhoneNumber) { // Guard clause if Sernia phone number isn't loaded
        setStatus({ type: 'error', message: 'Sernia phone number is not available. Cannot send messages.' });
        setIsSending(false);
        setShowConfirmation(false);
        return;
    }
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
            from_phone_number: serniaPhoneNumber, // Use serniaPhoneNumber from state
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
    <div className="container mx-auto px-4 sm:px-6 lg:px-8 py-4 sm:py-6 lg:py-8">
      <h1 className="text-2xl sm:text-3xl font-bold mb-4 sm:mb-6">Message Tenants</h1>

      {/* Loading State */}
      {isLoading && (
        <div>
          <Skeleton className="h-6 sm:h-8 w-1/3 sm:w-1/4 mb-4" />
          <Skeleton className="h-80 sm:h-96 w-full" /> {/* Skeleton for table area */}
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
              {/* Tenant Filter Toggle */}
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">Show:</span>
                <Button
                  variant={showOnlyTenants ? "default" : "outline"}
                  size="sm"
                  onClick={() => setShowOnlyTenants(true)}
                  className="text-xs"
                >
                  Tenants Only
                </Button>
                <Button
                  variant={!showOnlyTenants ? "default" : "outline"}
                  size="sm"
                  onClick={() => setShowOnlyTenants(false)}
                  className="text-xs"
                >
                  All Contacts
                </Button>
              </div>

              {/* Column View Toggle - Only show on mobile */}
              <div className="flex justify-between items-center sm:hidden">
                <span className="text-sm text-muted-foreground">
                  {showAllColumns ? 'All Columns' : 'Mobile View'}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowAllColumns(!showAllColumns)}
                  className="text-xs"
                >
                  {showAllColumns ? 'Mobile View' : 'All Columns'}
                </Button>
              </div>
            </div>
          )}

          {/* Render DataTable only when columns are generated */}
          {columns.length > 0 && filteredTenants.length > 0 ? (
            <DataTable 
              columns={columns} 
              data={filteredTenants} 
              rowSelection={rowSelection}
              onRowSelectionChange={setRowSelection}
              onFiltersChange={handleFiltersChange}
            />
          ) : (
            <p>No tenant data found or could not generate columns.</p> 
          )}

          {/* Message Input Area */}
          {/* Ensure tenants exist before showing message area */}
          {filteredTenants.length > 0 && (
            <div className="space-y-3 sm:space-y-4">
               <div>
                <label className="block text-sm font-medium text-foreground mb-2">Message</label>
                <Textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Type your message here..."
                  className="resize-none text-sm sm:text-base"
                  rows={3}
                  disabled={isSending || !serniaPhoneNumber} // Disable if Sernia phone is not loaded
                />
                {!serniaPhoneNumber && !isLoading && (
                  <p className="text-xs text-destructive mt-1">From phone number could not be loaded. Sending disabled.</p>
                )}
              </div>
              
              <Button 
                onClick={handleSendClick}
                disabled={selectedTenants.length === 0 || !message.trim() || isSending || !serniaPhoneNumber}
                className="w-full sm:w-auto text-sm sm:text-base"
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
              <AlertTitle className="text-sm sm:text-base">{status.type === 'error' ? 'Error' : status.type === 'loading' ? 'Processing' : 'Success'}</AlertTitle>
              <AlertDescription className="whitespace-pre-wrap text-xs sm:text-sm">{status.message}</AlertDescription> 
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
          <DialogContent className="max-w-sm sm:max-w-md">
            <DialogHeader>
              <DialogTitle className="text-lg sm:text-xl">Confirm Message</DialogTitle>
              <DialogDescription className="text-sm">
                Are you sure you want to send the following message to {selectedTenants.length} selected tenant(s)?
              </DialogDescription>
            </DialogHeader>
            
            <div className="mt-4 p-3 bg-muted rounded-md space-y-2 max-h-48 sm:max-h-60 overflow-y-auto">
              <p className="text-sm font-medium text-foreground">Recipients:</p>
              <p className="text-xs sm:text-sm text-muted-foreground">
                {/* Safely access dynamic keys */}
                {selectedTenants.map(t => `${t['First Name'] || ''} ${t['Last Name'] || ''} (${t['Phone Number'] || 'N/A'})`).join(', ')}
              </p>
              <p className="text-sm font-medium text-foreground mt-2">Message:</p>
              <p className="mt-1 text-xs sm:text-sm text-muted-foreground whitespace-pre-wrap">{message}</p>
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
                disabled={isSending || !serniaPhoneNumber} // Disable if Sernia phone is not loaded
                className="w-full sm:w-auto"
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