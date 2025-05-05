import { useState, useEffect, useRef } from 'react';
import { Text, View, Button, Platform } from 'react-native';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import Constants from 'expo-constants';
import { isDevice } from 'expo-device'; // Correct import

// Configures notification handling when the app is in the foreground
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: false,
  }),
});

// Function to register for push notifications and get the token
export async function registerForPushNotificationsAsync(): Promise<string | null> {
  // Explicitly return null for web platform as Expo push tokens aren't applicable/retrievable here
  if (Platform.OS === 'web') {
    console.log('Skipping push notification registration on web platform.');
    return null;
  }

  let token: string | null = null;

  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('default', {
      name: 'default',
      importance: Notifications.AndroidImportance.MAX,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: '#FF231F7C',
    });
  }

  if (isDevice) { // Use isDevice check
    const { status: existingStatus } = await Notifications.getPermissionsAsync();
    let finalStatus = existingStatus;
    if (existingStatus !== 'granted') {
      const { status } = await Notifications.requestPermissionsAsync();
      finalStatus = status;
    }
    if (finalStatus !== 'granted') {
      alert('Failed to get push token for push notification! Permission not granted.');
      console.error('Notification permissions not granted.');
      return null;
    }
    // Learn more about projectId:
    // https://docs.expo.dev/push-notifications/push-notifications-setup/#configure-projectid
    // Potentially retrieve projectId from Constants
    const projectId = Constants.expoConfig?.extra?.eas?.projectId;
    if (!projectId) {
      alert('Failed to get push token: Missing EAS project ID in app.config.js extras.');
      console.error('Missing EAS project ID.');
      return null;
    }
    try {
        token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
        console.log('Expo Push Token:', token);
    } catch(e: any) {
        alert(`Failed to get push token: ${e.message}`);
        console.error("Error getting push token:", e);
    }
  } else {
    alert('Must use physical device for Push Notifications');
    console.log('Push notifications require a physical device, not supported in simulator/emulator without config.');
  }

  return token;
}

// Optional: Function to send the token to your backend
export async function sendTokenToBackend(pushToken: string, sessionToken: string) {
  console.log('Attempting to send token to backend:', pushToken.slice(0, 15) + '...');
  // Get the API base URL from Constants.expoConfig.extra
  // It's expected to be defined via app.config.js
  const apiBaseUrl = Constants.expoConfig?.extra?.apiBaseUrl as string;

  // Check if apiBaseUrl was actually loaded (it should have been validated in app.config.js)
  if (!apiBaseUrl) {
    console.error('CRITICAL: apiBaseUrl not found in Constants.expoConfig.extra. Check app.config.js and environment variables.');
    // Depending on requirements, you might want to alert the user or just log
    alert('Configuration error: Cannot determine backend server address.');
    return; // Stop execution if URL is missing
  }

  const registerUrl = `${apiBaseUrl}/api/push/register`;

  console.log(`Sending token to: ${registerUrl}`); // Log the actual URL being used

  try {
    const response = await fetch(registerUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        // Include the Clerk session token in the Authorization header
        'Authorization': `Bearer ${sessionToken}`,
      },
      // Ensure the body matches what the backend expects
      // FastAPI endpoint uses Body(embed=True) for the 'token_body' parameter
      body: JSON.stringify({ token_body: { token: pushToken } }),
    });

    if (!response.ok) {
      const errorBody = await response.text(); // Read error body for more details
      console.error(`Failed to send token: ${response.status} ${response.statusText}`, errorBody);
      throw new Error(`Failed to send token: ${response.statusText} - ${errorBody}`);
    }

    const responseData = await response.json();
    console.log('Token sent to backend successfully:', responseData);

  } catch (error) {
    console.error('Error sending token to backend:', error);
    // Optional: Implement retry logic or more robust error handling
  }
} 