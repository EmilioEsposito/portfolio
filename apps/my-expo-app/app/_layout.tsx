import { ClerkProvider, useAuth } from '@clerk/clerk-expo';
import { DarkTheme, DefaultTheme, ThemeProvider } from '@react-navigation/native';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import Constants from 'expo-constants';
import 'react-native-reanimated';
import { tokenCache } from '@clerk/clerk-expo/token-cache';
import { SignedIn, SignedOut, useUser } from '@clerk/clerk-expo';

import { useColorScheme } from '@/hooks/useColorScheme';

// Get Clerk Publishable Key from expo constants
const clerkPublishableKey = Constants.expoConfig?.extra?.clerkPublishableKey as string;

if (!clerkPublishableKey) {
  throw new Error('Missing Clerk Publishable Key. Ensure EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY is set in .env file and loaded in app.config.js extra key.');
}

function InitialLayout() {
  const colorScheme = useColorScheme();
  const [loaded] = useFonts({
    SpaceMono: require('../assets/fonts/SpaceMono-Regular.ttf'),
  });

  if (!loaded) {
    // Async font loading only occurs in development.
    return null;
  }

  return (
    <ThemeProvider value={colorScheme === 'dark' ? DarkTheme : DefaultTheme}>
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="(auth)" options={{ headerShown: false }} />
        <Stack.Screen name="+not-found" />
      </Stack>
      <StatusBar style="auto" />
    </ThemeProvider>
  );
}

export default function RootLayout() {
  return (
    <ClerkProvider 
      tokenCache={tokenCache} 
      publishableKey={clerkPublishableKey}
    >
      <InitialLayout />
    </ClerkProvider>
  );
}
