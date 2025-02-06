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

// This should match the salt in your .env
const SALT = process.env.NEXT_PUBLIC_ADMIN_PASSWORD_SALT;

async function hashPassword(password: string): Promise<string> {
  // Convert password+salt to Uint8Array
  const encoder = new TextEncoder();
  console.log("SALT", SALT);
  if (!SALT) {
    throw new Error("SALT not found");
  }
  const data = encoder.encode(password + SALT);
  
  // Hash using SHA-256
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  
  // Convert to hex string
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  
  return hashHex;
}

const BUILDINGS = ["Test"] as const;
type Building = typeof BUILDINGS[number];

export default function SendBuildingMessage() {
  const [building, setBuilding] = useState<Building | ''>('');
  const [message, setMessage] = useState('');
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus('Sending...');

    try {
      const hashedPassword = await hashPassword(password);
      
      const response = await fetch('/api/open_phone/send_message_to_building', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          building_name: building,
          message: message,
          password_hash: hashedPassword,
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
    </div>
  );
} 