import { useState, useEffect, useCallback } from "react";
import { Button } from "~/components/ui/button";
import { Badge } from "~/components/ui/badge";
import { useAuth } from "@clerk/react-router";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "~/components/ui/collapsible";
import { ChevronDown, RefreshCw, Play, Trash2 } from "lucide-react";

interface SchedulerJob {
  id: string;
  name?: string;
  service: "dbos" | "apscheduler";
  func_ref: string;
  args: any[];
  kwargs: Record<string, any>;
  trigger: string;
  next_run_time: string | null;
  coalesce: boolean;
  executor: string;
  max_instances: number;
  misfire_grace_time: number;
  pending: boolean;
}

interface SchedulerProps {
  apiBaseUrl: string;
}

export function Scheduler({ apiBaseUrl }: SchedulerProps) {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [jobs, setJobs] = useState<SchedulerJob[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [runningJob, setRunningJob] = useState<string | null>(null);
  const [deletingJob, setDeletingJob] = useState<string | null>(null);
  const [openJobs, setOpenJobs] = useState<Set<string>>(new Set());

  const fetchWithAuth = useCallback(
    async (url: string, init: RequestInit, retryOn401 = true) => {
      const token = await getToken();
      if (!token) {
        throw new Error("Not authenticated (no token available).");
      }

      const headers: HeadersInit = {
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
        Authorization: `Bearer ${token}`,
      };

      const response = await fetch(url, { ...init, headers });
      if (response.status === 401 && retryOn401) {
        // Clerk tokens can rotate; retry once with a fresh token call.
        const token2 = await getToken();
        if (!token2) return response;
        const headers2: HeadersInit = {
          "Content-Type": "application/json",
          ...(init.headers ?? {}),
          Authorization: `Bearer ${token2}`,
        };
        return await fetch(url, { ...init, headers: headers2 });
      }
      return response;
    },
    [getToken]
  );

  const fetchJobs = useCallback(async () => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      setError("Not signed in.");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await fetchWithAuth(
        `${apiBaseUrl}/schedulers/get_jobs`,
        { method: "GET" },
        true
      );
      if (!response.ok) {
        const errorData = await response
          .json()
          .catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
        throw new Error(
          errorData.detail || `HTTP error! status: ${response.status}`
        );
      }
      const data = await response.json();
      setJobs(data);
    } catch (e) {
      console.error("Failed to fetch jobs:", e);
      setError(e instanceof Error ? e.message : "An unknown error occurred");
    }
    setLoading(false);
  }, [apiBaseUrl, fetchWithAuth, isLoaded, isSignedIn]);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  const handleRunJobNow = async (job: SchedulerJob) => {
    const jobKey = `${job.service}:${job.id}`;
    setRunningJob(jobKey);
    setError(null);
    try {
      const response = await fetchWithAuth(
        `${apiBaseUrl}/schedulers/run_job_now/${job.service}/${job.id}`,
        {
          method: "GET",
        }
      );
      if (!response.ok) {
        const errorData = await response
          .json()
          .catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
        throw new Error(
          errorData.detail || `HTTP error! status: ${response.status}`
        );
      }
      const result = await response.json();
      alert(result.message || `Job ${job.id} triggered successfully.`);
      fetchJobs();
    } catch (e) {
      console.error(`Failed to run job ${job.id}:`, e);
      const errorMessage =
        e instanceof Error
          ? e.message
          : "An unknown error occurred while running the job";
      setError(errorMessage);
      alert(`Error: ${errorMessage}`);
    }
    setRunningJob(null);
  };

  const handleDeleteJob = async (job: SchedulerJob) => {
    if (job.service !== "apscheduler") return;

    const ok = window.confirm(
      `Delete APScheduler job "${job.name || job.id}"?\n\nThis removes it from the persisted job store.`
    );
    if (!ok) return;

    const jobKey = `${job.service}:${job.id}`;
    setDeletingJob(jobKey);
    setError(null);
    try {
      const response = await fetchWithAuth(
        `${apiBaseUrl}/schedulers/delete_job/${job.service}/${job.id}`,
        {
          method: "DELETE",
        }
      );

      if (!response.ok) {
        const errorData = await response
          .json()
          .catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
        throw new Error(
          errorData.detail || `HTTP error! status: ${response.status}`
        );
      }

      const result = await response.json();
      alert(result.message || `Job ${job.id} deleted successfully.`);
      fetchJobs();
    } catch (e) {
      console.error(`Failed to delete job ${job.id}:`, e);
      const errorMessage =
        e instanceof Error
          ? e.message
          : "An unknown error occurred while deleting the job";
      setError(errorMessage);
      alert(`Error: ${errorMessage}`);
    }
    setDeletingJob(null);
  };

  const toggleJob = (jobKey: string) => {
    setOpenJobs((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(jobKey)) {
        newSet.delete(jobKey);
      } else {
        newSet.add(jobKey);
      }
      return newSet;
    });
  };

  if (loading && jobs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 space-y-2">
        <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
        <p className="text-muted-foreground">
          {!isLoaded ? "Loading authentication..." : "Loading jobs..."}
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-8 space-y-4">
        <p className="text-destructive">Error: {error}</p>
        <Button onClick={fetchJobs} variant="outline">
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold">Scheduled Jobs</h3>
        <Button
          onClick={fetchJobs}
          disabled={loading}
          variant="outline"
          size="sm"
        >
          <RefreshCw
            className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`}
          />
          Refresh
        </Button>
      </div>

      {jobs.length === 0 && !loading && (
        <p className="text-center text-muted-foreground py-4">
          No jobs scheduled.
        </p>
      )}

      <div className="space-y-2">
        {jobs.map((job) => {
          const jobKey = `${job.service}:${job.id}`;
          return (
          <Collapsible
            key={jobKey}
            open={openJobs.has(jobKey)}
            onOpenChange={() => toggleJob(jobKey)}
          >
            <div className="border rounded-lg">
              <CollapsibleTrigger asChild>
                <button className="flex w-full items-center justify-between p-4 hover:bg-muted/50 transition-colors">
                  <div className="flex flex-col items-start text-left">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{job.name || job.id}</span>
                      <Badge variant={job.service === "dbos" ? "default" : "secondary"}>
                        {job.service.toUpperCase()}
                      </Badge>
                    </div>
                    <span className="text-sm text-muted-foreground">
                      Next Run:{" "}
                      {job.next_run_time
                        ? new Date(job.next_run_time).toLocaleString()
                        : "N/A"}
                    </span>
                  </div>
                  <ChevronDown
                    className={`h-4 w-4 text-muted-foreground transition-transform ${
                      openJobs.has(jobKey) ? "rotate-180" : ""
                    }`}
                  />
                </button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="px-4 pb-4 space-y-2 text-sm border-t pt-4">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="font-medium">ID:</span> {job.id}
                    </div>
                    <div>
                      <span className="font-medium">Service:</span> {job.service}
                    </div>
                    <div>
                      <span className="font-medium">Trigger:</span> {job.trigger}
                    </div>
                    <div className="col-span-2">
                      <span className="font-medium">Function:</span>{" "}
                      <code className="text-xs bg-muted px-1 py-0.5 rounded">
                        {job.func_ref}
                      </code>
                    </div>
                    <div>
                      <span className="font-medium">Args:</span>{" "}
                      {JSON.stringify(job.args)}
                    </div>
                    <div>
                      <span className="font-medium">Kwargs:</span>{" "}
                      {JSON.stringify(job.kwargs)}
                    </div>
                    <div>
                      <span className="font-medium">Coalesce:</span>{" "}
                      {job.coalesce.toString()}
                    </div>
                    <div>
                      <span className="font-medium">Executor:</span>{" "}
                      {job.executor}
                    </div>
                    <div>
                      <span className="font-medium">Max Instances:</span>{" "}
                      {job.max_instances}
                    </div>
                    <div>
                      <span className="font-medium">Misfire Grace:</span>{" "}
                      {job.misfire_grace_time}s
                    </div>
                    <div>
                      <span className="font-medium">Pending:</span>{" "}
                      {job.pending.toString()}
                    </div>
                  </div>
                  <div className="pt-2">
                    <div className="flex flex-wrap gap-2">
                      <Button
                        onClick={() => handleRunJobNow(job)}
                        disabled={runningJob === jobKey || loading || deletingJob === jobKey}
                        size="sm"
                      >
                        <Play className="h-4 w-4 mr-2" />
                        {runningJob === jobKey ? "Running..." : "Run Now"}
                      </Button>

                      {job.service === "apscheduler" && (
                        <Button
                          onClick={() => handleDeleteJob(job)}
                          disabled={loading || runningJob === jobKey || deletingJob === jobKey}
                          size="sm"
                          variant="destructive"
                        >
                          <Trash2 className="h-4 w-4 mr-2" />
                          {deletingJob === jobKey ? "Deleting..." : "Delete"}
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              </CollapsibleContent>
            </div>
          </Collapsible>
          );
        })}
      </div>

      {loading && jobs.length > 0 && (
        <div className="flex justify-center py-2">
          <RefreshCw className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      )}
    </div>
  );
}
