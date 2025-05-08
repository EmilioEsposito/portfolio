import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, Button, FlatList, StyleSheet, ActivityIndicator, Alert, Platform } from 'react-native';

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

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
      };
      

      const response = await fetch(`${apiBaseUrl}/scheduler/get_jobs`, {
        method: 'GET',
        headers: headers,
      });
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setJobs(data);
    } catch (e) {
      console.error("Failed to fetch jobs:", e);
      setError(e instanceof Error ? e.message : 'An unknown error occurred');
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
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
      };


      const response = await fetch(`${apiBaseUrl}/scheduler/run_job_now/${jobId}`, {
        method: 'GET', // As defined in routes.py
        headers: headers,
      });
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: `HTTP error! status: ${response.status}` }));
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
      }
      const result = await response.json();
      Alert.alert('Success', result.message || `Job ${jobId} triggered successfully.`);
      // Optionally, refresh the jobs list or update the specific job's status
      fetchJobs(); 
    } catch (e) {
      console.error(`Failed to run job ${jobId}:`, e);
      setError(e instanceof Error ? e.message : 'An unknown error occurred while running the job');
      Alert.alert('Error', e instanceof Error ? e.message : 'Failed to run job');
    }
    setRunningJob(null);
  };

  if (loading && jobs.length === 0) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" />
        <Text>Loading jobs...</Text>
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>Error: {error}</Text>
        <Button title="Retry" onPress={fetchJobs} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Scheduled Jobs</Text>
      <Button title="Refresh Jobs" onPress={fetchJobs} disabled={loading} />
      {jobs.length === 0 && !loading && (
        <Text style={styles.centeredText}>No jobs scheduled.</Text>
      )}
      <FlatList
        data={jobs}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <View style={styles.jobItem}>
            <Text style={styles.jobName}>{item.name || item.id}</Text>
            <Text>ID: {item.id}</Text>
            <Text>Next Run: {item.next_run_time ? new Date(item.next_run_time).toLocaleString() : 'N/A'}</Text>
            <Text>Trigger: {item.trigger}</Text>
            <Text>Function: {item.func_ref}</Text>
            <Text>Args: {JSON.stringify(item.args)}</Text>
            <Text>Kwargs: {JSON.stringify(item.kwargs)}</Text>
            <Text>Coalesce: {item.coalesce.toString()}</Text>
            <Text>Executor: {item.executor}</Text>
            <Text>Max Instances: {item.max_instances}</Text>
            <Text>Misfire Grace Time: {item.misfire_grace_time}s</Text>
            <Text>Pending: {item.pending.toString()}</Text>
            <Button 
              title={runningJob === item.id ? "Running..." : "Run Now"} 
              onPress={() => handleRunJobNow(item.id)} 
              disabled={runningJob === item.id || loading}
            />
          </View>
        )}
        ListFooterComponent={loading ? <ActivityIndicator style={{ marginVertical: 20 }} /> : null}
      />
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: Platform.OS === 'web' ? 20 : 10, // More padding for web
  },
  centered: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  centeredText: {
    textAlign: 'center',
    marginTop: 20,
    fontSize: 16,
  },
  title: {
    fontSize: 24,
    fontWeight: 'bold',
    marginBottom: 20,
    textAlign: 'center',
  },
  jobItem: {
    backgroundColor: '#f9f9f9',
    padding: 15,
    marginBottom: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#eee',
  },
  jobName: {
    fontSize: 18,
    fontWeight: 'bold',
  },
  errorText: {
    color: 'red',
    marginBottom: 10,
  },
});

export default Scheduler;
