import { useState, useEffect, useCallback } from "react";
import { Button } from "~/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "~/components/ui/collapsible";
import { ChevronDown, RefreshCw, Play } from "lucide-react";

interface SchedulerJob {
  id: string;
  name?: string;
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
  authToken?: string;
}

export function Scheduler({ apiBaseUrl, authToken }: SchedulerProps) {
  const [jobs, setJobs] = useState<SchedulerJob[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [runningJob, setRunningJob] = useState<string | null>(null);
  const [openJobs, setOpenJobs] = useState<Set<string>>(new Set());

  const fetchJobs = useCallback(async () => {
    if (!authToken) {
      setError("No auth token available!");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const headers: HeadersInit = {
        "Content-Type": "application/json",
        Authorization: `Bearer ${authToken}`,
      };

      const response = await fetch(`${apiBaseUrl}/scheduler/get_jobs`, {
        method: "GET",
        headers: headers,
      });
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setJobs(data);
    } catch (e) {
      console.error("Failed to fetch jobs:", e);
      setError(e instanceof Error ? e.message : "An unknown error occurred");
    }
    setLoading(false);
  }, [apiBaseUrl, authToken]);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  const handleRunJobNow = async (jobId: string) => {
    setRunningJob(jobId);
    setError(null);
    try {
      const headers: HeadersInit = {
        "Content-Type": "application/json",
        Authorization: `Bearer ${authToken}`,
      };

      const response = await fetch(
        `${apiBaseUrl}/scheduler/run_job_now/${jobId}`,
        {
          method: "GET",
          headers: headers,
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
      alert(result.message || `Job ${jobId} triggered successfully.`);
      fetchJobs();
    } catch (e) {
      console.error(`Failed to run job ${jobId}:`, e);
      const errorMessage =
        e instanceof Error
          ? e.message
          : "An unknown error occurred while running the job";
      setError(errorMessage);
      alert(`Error: ${errorMessage}`);
    }
    setRunningJob(null);
  };

  const toggleJob = (jobId: string) => {
    setOpenJobs((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(jobId)) {
        newSet.delete(jobId);
      } else {
        newSet.add(jobId);
      }
      return newSet;
    });
  };

  if (loading && jobs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 space-y-2">
        <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
        <p className="text-muted-foreground">Loading jobs...</p>
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
        {jobs.map((job) => (
          <Collapsible
            key={job.id}
            open={openJobs.has(job.id)}
            onOpenChange={() => toggleJob(job.id)}
          >
            <div className="border rounded-lg">
              <CollapsibleTrigger asChild>
                <button className="flex w-full items-center justify-between p-4 hover:bg-muted/50 transition-colors">
                  <div className="flex flex-col items-start text-left">
                    <span className="font-medium">{job.name || job.id}</span>
                    <span className="text-sm text-muted-foreground">
                      Next Run:{" "}
                      {job.next_run_time
                        ? new Date(job.next_run_time).toLocaleString()
                        : "N/A"}
                    </span>
                  </div>
                  <ChevronDown
                    className={`h-4 w-4 text-muted-foreground transition-transform ${
                      openJobs.has(job.id) ? "rotate-180" : ""
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
                    <Button
                      onClick={() => handleRunJobNow(job.id)}
                      disabled={runningJob === job.id || loading}
                      size="sm"
                    >
                      <Play className="h-4 w-4 mr-2" />
                      {runningJob === job.id ? "Running..." : "Run Now"}
                    </Button>
                  </div>
                </div>
              </CollapsibleContent>
            </div>
          </Collapsible>
        ))}
      </div>

      {loading && jobs.length > 0 && (
        <div className="flex justify-center py-2">
          <RefreshCw className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      )}
    </div>
  );
}
