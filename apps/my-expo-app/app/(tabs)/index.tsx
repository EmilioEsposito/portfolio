import { Image, StyleSheet, Platform, View, Text, TouchableOpacity } from 'react-native';
import { SignedIn, SignedOut, useUser } from '@clerk/clerk-expo';
import { Link, useRouter } from 'expo-router';

import { HelloWave } from '@/components/HelloWave';
import ParallaxScrollView from '@/components/ParallaxScrollView';
import { ThemedText, ThemedView, ThemedButton } from '@portfolio/ui';
import { SignOutButton } from '@/components/SignOutButton';

export default function HomeScreen() {
  const { user } = useUser();
  const router = useRouter();

  return (
    <View style={{ flex: 1 }}>
      <SignedIn>
        <ParallaxScrollView
          headerBackgroundColor={{ light: '#A1CEDC', dark: '#1D3D47' }}
          headerImage={
            <Image
              source={require('@/assets/images/partial-react-logo.png')}
              style={styles.reactLogo}
            />
          }>
          <ThemedView style={styles.titleContainer}>
            <ThemedText type="title">
              Welcome {user?.emailAddresses[0]?.emailAddress ?? 'User'}!
            </ThemedText>
            <HelloWave />
          </ThemedView>
          <View style={styles.signOutContainer}>
            <SignOutButton />
          </View>
          <ThemedView style={styles.stepContainer}>
            <ThemedText type="subtitle">Step 1: Try it</ThemedText>
            <ThemedText>
              Edit <ThemedText type="defaultSemiBold">app/(tabs)/index.tsx</ThemedText> to see changes.
              Press{' '}
              <ThemedText type="defaultSemiBold">
                {Platform.select({
                  ios: 'cmd + d',
                  android: 'cmd + m',
                  web: 'F12',
                })}
              </ThemedText>{' '}
              to open developer tools.
            </ThemedText>
          </ThemedView>
          <ThemedView style={styles.stepContainer}>
            <ThemedText type="subtitle">Step 2: Explore</ThemedText>
            <ThemedText>
              Tap the Explore tab to learn more about what's included in this starter app.
            </ThemedText>
          </ThemedView>
          <ThemedView style={styles.stepContainer}>
            <ThemedText type="subtitle">Step 3: Get a fresh start</ThemedText>
            <ThemedText>
              When you're ready, run <ThemedText type="defaultSemiBold">npm run reset-project</ThemedText> to get a fresh <ThemedText type="defaultSemiBold">app</ThemedText> directory. This will move the current <ThemedText type="defaultSemiBold">app</ThemedText> to <ThemedText type="defaultSemiBold">app-example</ThemedText>.
            </ThemedText>
          </ThemedView>
        </ParallaxScrollView>
      </SignedIn>
      <SignedOut>
        <View style={styles.signedOutContainer}>
          <ThemedText type="title">Welcome!</ThemedText>
          <ThemedText style={{ marginBottom: 20 }}>Please sign in or sign up to continue.</ThemedText>
          <Link href="/(auth)/sign-in" asChild>
            <ThemedButton title="Sign in" type="primary" />
          </Link>
          <Link href="/(auth)/sign-up" asChild>
            <ThemedButton title="Sign up" type="primary" />
          </Link>
        </View>
      </SignedOut>
    </View>
  );
}

const styles = StyleSheet.create({
  titleContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  stepContainer: {
    gap: 8,
    marginBottom: 8,
  },
  reactLogo: {
    height: 178,
    width: 290,
    bottom: 0,
    left: 0,
    position: 'absolute',
  },
  signOutContainer: {
    padding: 10,
    alignItems: 'flex-start',
  },
  signedOutContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
    backgroundColor: 'black',
  },
});
