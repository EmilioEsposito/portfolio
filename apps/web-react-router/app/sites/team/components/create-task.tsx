import { useRef, useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "~/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "~/components/ui/dialog";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";
import type { ClickUpList, TaskDraft } from "~/sites/team/lib/team-tasks";

export function CreateTask({ lists, defaultList, disabled, onCreate }: {
  lists: ClickUpList[];
  defaultList: string;
  disabled: boolean;
  onCreate: (draft: TaskDraft) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const submitting = useRef(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const data = new FormData(event.currentTarget);
    const title = String(data.get("title")).trim();
    if (!title) {
      setError("Enter a task title.");
      return;
    }
    submitting.current = true;
    setSaving(true);
    setError("");
    try {
      await onCreate({ title, list_id: String(data.get("list")), description: String(data.get("description")).trim() });
      setOpen(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not confirm creation. Check ClickUp before trying again.");
    } finally {
      submitting.current = false;
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => {
      if (submitting.current) return;
      setOpen(next);
      setError("");
    }}>
      <DialogTrigger asChild>
        <Button size="sm" disabled={disabled}><Plus aria-hidden="true" />New task</Button>
      </DialogTrigger>
      <DialogContent className="w-[calc(100%-2rem)] max-h-[90dvh] overflow-y-auto rounded-lg">
        <DialogHeader>
          <DialogTitle>New ClickUp task</DialogTitle>
          <DialogDescription>ClickUp applies the selected list’s assignment and due-date rules.</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <fieldset disabled={saving} className="space-y-4">
            <label className="grid gap-1.5 text-sm font-medium">
              Task title
              <Input name="title" maxLength={255} required autoFocus />
            </label>
            <label className="grid gap-1.5 text-sm font-medium">
              ClickUp list
              <select name="list" defaultValue={defaultList} className="h-9 rounded-md border border-input bg-background px-2 text-sm">
                {lists.map((list) => <option key={list.id} value={list.id}>{list.name}</option>)}
              </select>
            </label>
            <label className="grid gap-1.5 text-sm font-medium">
              Description (optional)
              <Textarea name="description" rows={3} maxLength={10000} />
            </label>
          </fieldset>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter className="gap-2">
            <Button type="button" variant="outline" disabled={saving} onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit" disabled={saving}>{saving ? "Creating…" : "Create task"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
