import { useState, useEffect, useCallback } from "react";
import type { Route } from "./+types/workspace";
import { useAuth } from "@clerk/react-router";
import { Button } from "~/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import { Textarea } from "~/components/ui/textarea";
import { Input } from "~/components/ui/input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "~/components/ui/alert-dialog";
import { AuthGuard } from "~/components/auth-guard";
import {
  FolderOpen,
  FileText,
  Folder,
  ArrowLeft,
  Plus,
  Trash2,
  Download,
  Save,
  Pencil,
  X,
  FolderPlus,
  Loader2,
  RefreshCw,
} from "lucide-react";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "AI Workspace | Sernia Capital" },
    {
      name: "description",
      content: "Browse and manage workspace files for the Sernia AI agent",
    },
  ];
}

interface Entry {
  name: string;
  type: "file" | "directory";
  size?: number;
}

const API_BASE = "/api/ai-sernia/workspace";

export default function WorkspacePage() {
  const { isSignedIn, getToken } = useAuth();

  const [currentPath, setCurrentPath] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // File view state
  const [viewingFile, setViewingFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [saving, setSaving] = useState(false);

  // New file/folder dialogs
  const [showNewFile, setShowNewFile] = useState(false);
  const [newFileName, setNewFileName] = useState("");
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");

  const authFetch = useCallback(
    async (url: string, options?: RequestInit) => {
      const token = await getToken();
      return fetch(url, {
        ...options,
        headers: {
          ...options?.headers,
          Authorization: `Bearer ${token}`,
        },
      });
    },
    [getToken],
  );

  const fetchDirectory = useCallback(
    async (path: string) => {
      if (!isSignedIn) return;
      setLoading(true);
      setError(null);
      try {
        const res = await authFetch(
          `${API_BASE}/ls?path=${encodeURIComponent(path)}`,
        );
        if (!res.ok) {
          const data = await res.json();
          throw new Error(data.detail || "Failed to list directory");
        }
        const data = await res.json();
        setEntries(data.entries);
        setCurrentPath(path);
        setViewingFile(null);
        setEditMode(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    },
    [isSignedIn, authFetch],
  );

  const fetchFile = useCallback(
    async (path: string) => {
      if (!isSignedIn) return;
      setLoading(true);
      setError(null);
      try {
        const res = await authFetch(
          `${API_BASE}/read?path=${encodeURIComponent(path)}`,
        );
        if (!res.ok) {
          const data = await res.json();
          throw new Error(data.detail || "Failed to read file");
        }
        const data = await res.json();
        setViewingFile(path);
        setFileContent(data.content);
        setEditContent(data.content);
        setEditMode(false);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    },
    [isSignedIn, authFetch],
  );

  useEffect(() => {
    if (isSignedIn) fetchDirectory("");
  }, [isSignedIn, fetchDirectory]);

  const handleEntryClick = (entry: Entry) => {
    const entryPath = currentPath ? `${currentPath}/${entry.name}` : entry.name;
    if (entry.type === "directory") {
      fetchDirectory(entryPath);
    } else {
      fetchFile(entryPath);
    }
  };

  const handleSave = async () => {
    if (!viewingFile) return;
    setSaving(true);
    setError(null);
    try {
      const res = await authFetch(`${API_BASE}/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: viewingFile, content: editContent }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to save file");
      }
      setFileContent(editContent);
      setEditMode(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  };

  const handleCreateFile = async () => {
    if (!newFileName.trim()) return;
    setError(null);
    const path = currentPath
      ? `${currentPath}/${newFileName.trim()}`
      : newFileName.trim();
    try {
      const res = await authFetch(`${API_BASE}/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, content: "" }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to create file");
      }
      setNewFileName("");
      setShowNewFile(false);
      fetchDirectory(currentPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) return;
    setError(null);
    const path = currentPath
      ? `${currentPath}/${newFolderName.trim()}`
      : newFolderName.trim();
    try {
      const res = await authFetch(`${API_BASE}/mkdir`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to create folder");
      }
      setNewFolderName("");
      setShowNewFolder(false);
      fetchDirectory(currentPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  const handleDelete = async (name: string, type: string) => {
    setError(null);
    const path = currentPath ? `${currentPath}/${name}` : name;
    try {
      const res = await authFetch(
        `${API_BASE}/delete?path=${encodeURIComponent(path)}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to delete");
      }
      // If we were viewing the deleted file, go back to directory
      if (viewingFile === path) {
        setViewingFile(null);
      }
      fetchDirectory(currentPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  };

  const handleDownload = (path: string) => {
    // Open download URL in a new tab - auth is via cookie/session
    // We need to use authFetch for token, so do it via blob
    authFetch(`${API_BASE}/download?path=${encodeURIComponent(path)}`).then(
      async (res) => {
        if (!res.ok) return;
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = path.split("/").pop() || "download";
        a.click();
        URL.revokeObjectURL(url);
      },
    );
  };

  // Build path segments for the breadcrumb bar
  const pathSegments = currentPath ? currentPath.split("/") : [];

  const navigateToSegment = (index: number) => {
    if (index < 0) {
      fetchDirectory("");
    } else {
      fetchDirectory(pathSegments.slice(0, index + 1).join("/"));
    }
  };

  return (
    <AuthGuard
      message="Sign in to manage workspace files"
      icon={<FolderOpen className="w-16 h-16 text-muted-foreground" />}
    >
      <div className="container mx-auto py-10 px-4 md:px-8 max-w-4xl">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
            <FolderOpen className="w-5 h-5 text-primary" />
          </div>
          <div>
            <h1 className="text-3xl font-bold">AI Workspace</h1>
            <p className="text-muted-foreground">
              Browse and manage the Sernia AI agent's workspace files
            </p>
          </div>
        </div>

        {/* Error display */}
        {error && (
          <div className="mb-4 p-3 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive text-sm">
            {error}
            <Button
              variant="ghost"
              size="sm"
              className="ml-2 h-6"
              onClick={() => setError(null)}
            >
              <X className="w-3 h-3" />
            </Button>
          </div>
        )}

        <Card>
          <CardHeader className="pb-3">
            {/* Path bar */}
            <div className="flex items-center gap-1 text-sm flex-wrap">
              <Button
                variant="link"
                size="sm"
                className="h-6 px-1 font-mono"
                onClick={() => navigateToSegment(-1)}
              >
                .workspace
              </Button>
              {pathSegments.map((segment, i) => (
                <span key={i} className="flex items-center gap-1">
                  <span className="text-muted-foreground">/</span>
                  <Button
                    variant="link"
                    size="sm"
                    className="h-6 px-1 font-mono"
                    onClick={() => navigateToSegment(i)}
                  >
                    {segment}
                  </Button>
                </span>
              ))}
            </div>

            {/* Actions bar */}
            {!viewingFile && (
              <div className="flex items-center gap-2 pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => fetchDirectory(currentPath)}
                  disabled={loading}
                >
                  <RefreshCw
                    className={`w-4 h-4 mr-1 ${loading ? "animate-spin" : ""}`}
                  />
                  Refresh
                </Button>
                {currentPath && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      const parent = pathSegments.slice(0, -1).join("/");
                      fetchDirectory(parent);
                    }}
                  >
                    <ArrowLeft className="w-4 h-4 mr-1" />
                    Back
                  </Button>
                )}
                <div className="flex-1" />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowNewFile(!showNewFile)}
                >
                  <Plus className="w-4 h-4 mr-1" />
                  New File
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowNewFolder(!showNewFolder)}
                >
                  <FolderPlus className="w-4 h-4 mr-1" />
                  New Folder
                </Button>
              </div>
            )}
          </CardHeader>

          <CardContent>
            {/* New file input */}
            {showNewFile && (
              <div className="flex items-center gap-2 mb-4 p-3 border rounded-lg bg-muted/30">
                <FileText className="w-4 h-4 text-muted-foreground" />
                <Input
                  value={newFileName}
                  onChange={(e) => setNewFileName(e.target.value)}
                  placeholder="filename.md"
                  className="flex-1"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreateFile();
                    if (e.key === "Escape") {
                      setShowNewFile(false);
                      setNewFileName("");
                    }
                  }}
                  autoFocus
                />
                <Button size="sm" onClick={handleCreateFile}>
                  Create
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setShowNewFile(false);
                    setNewFileName("");
                  }}
                >
                  <X className="w-4 h-4" />
                </Button>
              </div>
            )}

            {/* New folder input */}
            {showNewFolder && (
              <div className="flex items-center gap-2 mb-4 p-3 border rounded-lg bg-muted/30">
                <Folder className="w-4 h-4 text-muted-foreground" />
                <Input
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  placeholder="folder-name"
                  className="flex-1"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreateFolder();
                    if (e.key === "Escape") {
                      setShowNewFolder(false);
                      setNewFolderName("");
                    }
                  }}
                  autoFocus
                />
                <Button size="sm" onClick={handleCreateFolder}>
                  Create
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setShowNewFolder(false);
                    setNewFolderName("");
                  }}
                >
                  <X className="w-4 h-4" />
                </Button>
              </div>
            )}

            {/* File view */}
            {viewingFile ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => fetchDirectory(currentPath)}
                  >
                    <ArrowLeft className="w-4 h-4 mr-1" />
                    Back
                  </Button>
                  <div className="flex-1" />
                  {editMode ? (
                    <>
                      <Button
                        size="sm"
                        onClick={handleSave}
                        disabled={saving}
                      >
                        {saving ? (
                          <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                        ) : (
                          <Save className="w-4 h-4 mr-1" />
                        )}
                        Save
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setEditMode(false);
                          setEditContent(fileContent);
                        }}
                      >
                        Cancel
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setEditMode(true)}
                      >
                        <Pencil className="w-4 h-4 mr-1" />
                        Edit
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleDownload(viewingFile)}
                      >
                        <Download className="w-4 h-4 mr-1" />
                        Download
                      </Button>
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button variant="destructive" size="sm">
                            <Trash2 className="w-4 h-4 mr-1" />
                            Delete
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>Delete file?</AlertDialogTitle>
                            <AlertDialogDescription>
                              This will permanently delete{" "}
                              <code className="bg-muted px-1 rounded">
                                {viewingFile}
                              </code>
                              . This action cannot be undone.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              onClick={() =>
                                handleDelete(
                                  viewingFile.split("/").pop() || "",
                                  "file",
                                )
                              }
                            >
                              Delete
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    </>
                  )}
                </div>
                <Textarea
                  value={editMode ? editContent : fileContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  readOnly={!editMode}
                  className="min-h-[400px] font-mono text-sm"
                />
              </div>
            ) : loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
              </div>
            ) : entries.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <Folder className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>Empty directory</p>
              </div>
            ) : (
              /* Directory listing */
              <div className="divide-y">
                {entries.map((entry) => (
                  <div
                    key={entry.name}
                    className="flex items-center gap-3 py-2 px-2 hover:bg-muted/50 rounded-lg cursor-pointer group"
                  >
                    <button
                      className="flex items-center gap-3 flex-1 text-left"
                      onClick={() => handleEntryClick(entry)}
                    >
                      {entry.type === "directory" ? (
                        <Folder className="w-4 h-4 text-blue-500 shrink-0" />
                      ) : (
                        <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                      )}
                      <span className="font-mono text-sm">{entry.name}</span>
                      {entry.type === "file" && entry.size != null && (
                        <span className="text-xs text-muted-foreground">
                          {entry.size < 1024
                            ? `${entry.size} B`
                            : `${(entry.size / 1024).toFixed(1)} KB`}
                        </span>
                      )}
                    </button>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="opacity-0 group-hover:opacity-100 h-7 w-7 p-0"
                        >
                          <Trash2 className="w-3.5 h-3.5 text-muted-foreground" />
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>
                            Delete {entry.type}?
                          </AlertDialogTitle>
                          <AlertDialogDescription>
                            This will permanently delete{" "}
                            <code className="bg-muted px-1 rounded">
                              {entry.name}
                            </code>
                            .{" "}
                            {entry.type === "directory" &&
                              "The directory must be empty."}
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction
                            onClick={() =>
                              handleDelete(entry.name, entry.type)
                            }
                          >
                            Delete
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </AuthGuard>
  );
}
