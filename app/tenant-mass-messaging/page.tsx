'use client';

import { useState } from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { MultiSelect } from "@/components/multi-select"

const PROPERTIES = ["Test"] as const;
type Property = typeof PROPERTIES[number];

const propertyOptions = PROPERTIES.map(property => ({
  label: property,
  value: property,
}));

export default function TenantMassMessaging() {
  const [propertyNames, setPropertyNames] = useState<string[]>([]);
  const [message, setMessage] = useState('');
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState<{ type: 'success' | 'error' | 'loading', message: string } | null>(null);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (propertyNames.length === 0) {
      setStatus({
        type: 'error',
        message: 'Please select at least one property'
      });
      return;
    }
    setShowConfirmation(true);
  };

  const handleConfirmedSubmit = async () => {
    setShowConfirmation(false);
    setIsLoading(true);
    setStatus({ type: 'loading', message: 'Sending...' });

    try {
      const response = await fetch('/api/open_phone/tenant_mass_message', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          property_names: propertyNames,
          message: message,
          password: password,
        }),
      });

      const data = await response.json();
      console.log('Response data:', data);

      // Handle authentication error
      if (response.status === 401) {
        setStatus({ 
          type: 'error', 
          message: data.detail || 'Authentication failed. Please check your password.'
        });
        return;
      }

      // Handle other error responses
      if (!response.ok) {
        throw new Error(data.detail || data.message || data.error || 'Failed to send message');
      }
      
      // Handle partial success (some messages sent, some failed)
      if (data.successes > 0 && data.failures > 0) {
        setStatus({
          type: 'error',
          message: `Partial success: ${data.successes} messages sent, ${data.failures} failed. Check the logs for details.`
        });
        return;
      }

      // Handle complete failure
      if (data.failures > 0 && data.successes === 0) {
        throw new Error(data.message || 'Failed to send messages');
      }
      
      // Handle complete success
      if (data.success || (data.successes > 0 && data.failures === 0)) {
        setStatus({
          type: 'success',
          message: data.message || `Successfully sent ${data.successes} messages!`
        });

        // Clear form on success
        setPropertyNames([]);
        setMessage('');
        setPassword('');
        return;
      }

      // Fallback error
      throw new Error(data.message || data.error || data.detail || 'Failed to send message');

    } catch (error) {
      console.error('Error details:', error);
      const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
      setStatus({
        type: 'error',
        message: errorMessage
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <>
      <div className="max-w-md mx-auto mt-10 p-6 bg-card text-card-foreground rounded-lg shadow-lg">
        <h1 className="text-2xl font-bold mb-4">Tenant Mass Messaging</h1>
        
        <div className="mb-6 text-muted-foreground">
          <p className="text-sm">
            This tool allows property managers to send SMS messages to all tenants in 
            selected properties. This is useful for notifying tenants of a building-wide event like a water leak, 
            power outage, or common area maintenance.
          </p><br/>
          <p className="text-sm">
            Since OpenPhone doesn't support mass messaging, this tool uses their API to send 
            individual messages to each tenant. 
            
            Messages are sent securely and require password authentication.
          </p>
        </div>
        
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-foreground mb-2">Property Name(s)</label>
            <MultiSelect
              options={propertyOptions}
              onValueChange={setPropertyNames}
              defaultValue={propertyNames}
              placeholder="Select properties to message"
              maxCount={5}
              className="w-full"
              showSelectAll={false}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-2">Message</label>
            <Textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Type your message here"
              className="resize-none"
              rows={4}
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-foreground mb-2">Password</label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-primary-foreground bg-primary hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? 'Sending...' : 'Send Message'}
          </button>
        </form>

        {status && (
          <Alert className={`mt-4 ${
            status.type === 'error' 
              ? 'bg-destructive/15 text-destructive' 
              : status.type === 'loading'
              ? 'bg-muted text-muted-foreground'
              : 'bg-muted'
          }`}>
            <AlertDescription>
              {status.message}
            </AlertDescription>
          </Alert>
        )}

        <Dialog 
          open={showConfirmation} 
          onOpenChange={(open) => {
            if (!open) setShowConfirmation(false);
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Confirm Message</DialogTitle>
              <DialogDescription>
                Are you sure you want to send this message to {propertyNames.length} property(s)?
              </DialogDescription>
            </DialogHeader>
            
            <div className="mt-4 p-3 bg-muted rounded-md">
              <p className="text-sm font-medium text-foreground">Selected Properties:</p>
              <p className="mt-1 text-sm text-muted-foreground">{propertyNames.join(', ')}</p>
              <p className="text-sm font-medium text-foreground mt-2">Message:</p>
              <p className="mt-1 text-sm text-muted-foreground">{message}</p>
            </div>

            <DialogFooter className="mt-6">
              <button
                type="button"
                className="mr-3 px-4 py-2 text-sm font-medium text-muted-foreground bg-secondary border border-input rounded-md hover:bg-secondary/80 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-ring"
                onClick={() => setShowConfirmation(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isLoading}
                onClick={handleConfirmedSubmit}
                className="px-4 py-2 text-sm font-medium text-primary-foreground bg-primary border border-transparent rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isLoading ? 'Sending...' : 'Confirm & Send'}
              </button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {/* Source Links */}
      <div className="max-w-md mx-auto mt-6 p-4 text-sm text-muted-foreground">
        <p className="font-medium mb-2">Source Code & Documentation:</p>
        <ul className="space-y-2">
          <li>
            <a 
              href="https://eesposito.com/api/docs#/open_phone/send_tenant_mass_message_api_open_phone_tenant_mass_message_post"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline flex items-center"
            >
              API Documentation
            </a>
          </li>
          <li>
            <a 
              href="https://github.com/EmilioEsposito/portfolio/blob/main/api_src/open_phone.py#L258"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline flex items-center"
            >
              Backend Implementation (FastAPI)
            </a>
          </li>
          <li>
            <a 
              href="https://github.com/EmilioEsposito/portfolio/blob/main/app/tenant-mass-messaging/page.tsx"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline flex items-center"
            >
              Frontend Implementation (Next.js)
            </a>
          </li>
        </ul>
      </div>
    </>
  );
} 