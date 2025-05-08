import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Constants from 'expo-constants'; // Import Constants
import Scheduler from '../../../../packages/features/scheduler/Scheduler'; // Adjusted path
import { useAuth } from '@clerk/clerk-expo';
import { Stack } from 'expo-router';

// Access the API base URL from expo-constants
const API_BASE_URL = Constants.expoConfig?.extra?.apiBaseUrl as string;

// It's good practice to still ensure it's available, though app.config.js should have caught it.
if (!API_BASE_URL) {
  // This should ideally not be reached if app.config.js is set up correctly
  throw new Error("apiBaseUrl is not defined in app.config.js extra. This is required.");
}

export default function SchedulerAdminScreen() {
  const { getToken } = useAuth();
  const [authToken, setAuthToken] = React.useState<string | null>(null);
  const [isLoadingToken, setIsLoadingToken] = React.useState(true);

  React.useEffect(() => {
    const fetchToken = async () => {
      try {
        const token = await getToken();
        setAuthToken(token);
      } catch (e) {
        console.error("Failed to get auth token", e);
        // Handle token fetching error, maybe redirect or show an error message
      } finally {
        setIsLoadingToken(false);
      }
    };

    fetchToken();
  }, [getToken]);

  if (isLoadingToken) {
    return (
      <View style={styles.centered}>
        <Text>Loading authentication details...</Text>
      </View>
    );
  }

  // If you also want to protect this screen based on isSignedIn, you can add:
  // const { isSignedIn } = useAuth();
  // if (!isSignedIn) {
  //   return <Redirect href="/sign-in" />; // or some other appropriate redirect
  // }

  return (
    <>
      <Stack.Screen options={{ title: 'Scheduler Admin' }} />
      <View style={styles.container}>
        {authToken ? (
          <Scheduler apiBaseUrl={API_BASE_URL + '/api'} authToken={authToken} />
        ) : (
          <Text style={styles.centered}>Authentication token not available. Please ensure you are logged in.</Text>
        )}
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    // Add any additional styling for the container if needed
  },
  centered: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
    textAlign: 'center',
  },
}); 