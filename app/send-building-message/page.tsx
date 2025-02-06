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
  DialogClose,
} from "@/components/ui/dialog"

const BUILDINGS = ["Test"] as const;
type Building = typeof BUILDINGS[number];

export default function SendBuildingMessage() {
  const [building, setBuilding] = useState<Building | ''>('');
  const [message, setMessage] = useState('');
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState('');
  const [showConfirmation, setShowConfirmation] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setShowConfirmation(true);
  };

  const handleConfirmedSubmit = async () => {
    setShowConfirmation(false);
    setStatus('Sending...');

    try {
      const response = await fetch('/api/open_phone/send_message_to_building', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          building_name: building,
          message: message,
          password: password,
        }),
      });

      if (!response.ok) {
        throw new Error(await response.text());
      }

      const data = await response.json();
      setStatus(`Message sent to ${data.length} contacts`);
      
      // Clear form
      setBuilding('');
      setMessage('');
      setPassword('');
      
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
      setStatus(`Error: ${errorMessage}`);
    }
  };

  return (
    <div className="max-w-md mx-auto mt-10 p-6 bg-white rounded-lg shadow-lg">
      <h1 className="text-2xl font-bold mb-6">Send Building Message</h1>
      
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium mb-2">Building Name</label>
          <Select
            value={building}
            onValueChange={(value: Building) => setBuilding(value)}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select a building" />
            </SelectTrigger>
            <SelectContent>
              {BUILDINGS.map((b) => (
                <SelectItem key={b} value={b}>
                  {b}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">Message</label>
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
          <label className="block text-sm font-medium mb-2">Password</label>
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>

        <button
          type="submit"
          className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500"
        >
          Send Message
        </button>
      </form>

      {status && (
        <div className="mt-4 p-3 rounded bg-gray-100">
          {status}
        </div>
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
              Are you sure you want to send this message to {building}?
            </DialogDescription>
          </DialogHeader>
          
          <div className="mt-4 p-3 bg-gray-50 rounded-md">
            <p className="text-sm font-medium text-gray-700">Message:</p>
            <p className="mt-1 text-sm text-gray-600">{message}</p>
          </div>

          <DialogFooter className="mt-6">
            <button
              type="button"
              className="mr-3 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500"
              onClick={() => setShowConfirmation(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmedSubmit}
              className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 border border-transparent rounded-md hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500"
            >
              Confirm & Send
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
} 