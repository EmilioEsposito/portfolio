import React, { useState, useEffect, useCallback } from "react";
import {
  Button,
  FlatList,
  StyleSheet,
  ActivityIndicator,
  Alert,
  Platform,
  View,
} from "react-native";
import {
  ThemedView,
  ThemedText,
  useThemeColor,
  Colors,
  Collapsible,
  IconSymbol,
} from "@portfolio/ui";

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
  authToken?: string; // Added for authentication
}

const Scheduler: React.FC<SchedulerProps> = ({ apiBaseUrl, authToken }) => {
  const [jobs, setJobs] = useState<SchedulerJob[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [runningJob, setRunningJob] = useState<string | null>(null);

  // Get themed colors for dynamic styling
  const cardBackgroundColor = useThemeColor({}, "card");
  const cardBorderColor = useThemeColor({}, "border");
  const iconColor = useThemeColor({}, "icon"); // For collapsible icon

  const fetchJobs = useCallback(async () => {
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
          method: "GET", // As defined in routes.py
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
      Alert.alert(
        "Success",
        result.message || `Job ${jobId} triggered successfully.`
      );
      // Optionally, refresh the jobs list or update the specific job's status
      fetchJobs();
    } catch (e) {
      console.error(`Failed to run job ${jobId}:`, e);
      setError(
        e instanceof Error
          ? e.message
          : "An unknown error occurred while running the job"
      );
      Alert.alert(
        "Error",
        e instanceof Error ? e.message : "Failed to run job"
      );
    }
    setRunningJob(null);
  };

  if (loading && jobs.length === 0) {
    return (
      <ThemedView style={styles.centered}>
        <ActivityIndicator size="large" />
        <ThemedText>Loading jobs...</ThemedText>
      </ThemedView>
    );
  }

  if (error) {
    return (
      <ThemedView style={styles.centered}>
        <ThemedText style={styles.errorText}>Error: {error}</ThemedText>
        <Button title="Retry" onPress={fetchJobs} />
      </ThemedView>
    );
  }

  const renderJobItem = ({ item }: { item: SchedulerJob }) => {
    const jobTitle = (
      <View style={styles.jobItemHeader}>
        <ThemedText style={styles.jobNameCollapsed}>
          {item.name || item.id}
        </ThemedText>
        <ThemedText style={styles.jobNextRunCollapsed}>
          Next Run:{" "}
          {item.next_run_time
            ? new Date(item.next_run_time).toLocaleString()
            : "N/A"}
        </ThemedText>
      </View>
    );

    return (
      <ThemedView
        style={[
          styles.jobItemContainer, // Renamed from jobItem to avoid confusion with internal styling
          {
            backgroundColor: cardBackgroundColor,
            borderColor: cardBorderColor,
          },
        ]}
      >
        <Collapsible
          title={jobTitle}
          // Pass other props to Collapsible if your implementation supports them (e.g., icon color)
          // For now, assuming Collapsible handles its own icon theming or uses one from @portfolio/ui
        >
          {/* Added a View for details padding  */}
          <View style={styles.jobItemDetails}>
            <ThemedText style={styles.jobNameExpanded}>
              {item.name || item.id}
            </ThemedText>
            <ThemedText>ID: {item.id}</ThemedText>
            <ThemedText>
              Next Run:{" "}
              {item.next_run_time
                ? new Date(item.next_run_time).toLocaleString()
                : "N/A"}
            </ThemedText>
            <ThemedText>Trigger: {item.trigger}</ThemedText>
            <ThemedText>Function: {item.func_ref}</ThemedText>
            <ThemedText>Args: {JSON.stringify(item.args)}</ThemedText>
            <ThemedText>Kwargs: {JSON.stringify(item.kwargs)}</ThemedText>
            <ThemedText>Coalesce: {item.coalesce.toString()}</ThemedText>
            <ThemedText>Executor: {item.executor}</ThemedText>
            <ThemedText>Max Instances: {item.max_instances}</ThemedText>
            <ThemedText>
              Misfire Grace Time: {item.misfire_grace_time}s
            </ThemedText>
            <ThemedText>Pending: {item.pending.toString()}</ThemedText>
            {/* Added View for button margin */}
            <View style={styles.buttonContainer}>
              <Button
                title={runningJob === item.id ? "Running..." : "Run Now"}
                onPress={() => handleRunJobNow(item.id)}
                disabled={runningJob === item.id || loading}
              />
            </View>
          </View>
        </Collapsible>
      </ThemedView>
    );
  };

  return (
    <ThemedView style={styles.container}>
      <ThemedText style={styles.title}>Scheduled Jobs</ThemedText>
      <View style={styles.refreshButtonContainer}>
        <Button title="Refresh Jobs" onPress={fetchJobs} disabled={loading} />
      </View>
      {jobs.length === 0 && !loading && (
        <ThemedText style={styles.centeredText}>No jobs scheduled.</ThemedText>
      )}
      <FlatList
        data={jobs}
        keyExtractor={(item) => item.id}
        renderItem={renderJobItem} // Use the new render function
        ListFooterComponent={
          loading ? <ActivityIndicator style={{ marginVertical: 20 }} /> : null
        }
      />
    </ThemedView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: Platform.OS === "web" ? 20 : 10,
  },
  centered: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
  },
  centeredText: {
    textAlign: "center",
    marginTop: 20,
    fontSize: 16,
  },
  title: {
    fontSize: 24,
    fontWeight: "bold",
    marginBottom: 10, // Adjusted for button spacing
    textAlign: "center",
  },
  refreshButtonContainer: {
    // Style for refresh button spacing
    marginBottom: 20,
    alignItems: "center", // Center the button if it's not full width
  },
  jobItemContainer: {
    // Styles for the outer container of each job item (Collapsible wrapper)
    marginBottom: 10,
    borderRadius: 8,
    borderWidth: 1,
    // backgroundColor and borderColor are dynamic
  },
  jobItemHeader: {
    // Styles for the title prop of Collapsible
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: 15, // Add padding to the header of collapsible
  },
  jobNameCollapsed: {
    fontSize: 16, // Slightly smaller for collapsed view
    fontWeight: "bold",
    flexShrink: 1, // Allow text to shrink if too long
    marginRight: 8, // Space before next run time
  },
  jobNextRunCollapsed: {
    fontSize: 14,
    fontStyle: "italic",
    flexShrink: 1,
  },
  jobItemDetails: {
    // Styles for the content inside Collapsible (expanded view)
    padding: 15,
    paddingTop: 0, // Avoid double padding with header
  },
  jobNameExpanded: {
    fontSize: 18,
    fontWeight: "bold",
    marginBottom: 5,
  },
  buttonContainer: {
    marginTop: 15,
  },
  errorText: {
    color: "red",
    marginBottom: 10,
  },
});

export default Scheduler;
