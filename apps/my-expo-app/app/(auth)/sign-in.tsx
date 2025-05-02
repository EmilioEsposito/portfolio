import { useSignIn, useSSO } from '@clerk/clerk-expo'
import { Link, useRouter } from 'expo-router'
import { TextInput, TouchableOpacity, View, StyleSheet, Image } from 'react-native'
import React from 'react'
import { ThemedText } from '@/components/ThemedText'
import { ThemedView } from '@/components/ThemedView'
import { useColorScheme } from '@/hooks/useColorScheme'
import { Colors } from '@/constants/Colors'
import * as WebBrowser from 'expo-web-browser';

// Ensure that the dismissAuthSession function is called when the component mounts
// This is necessary for the OAuth flow to work correctly on web platforms.
WebBrowser.maybeCompleteAuthSession();

export default function Page() {
  const { signIn, setActive, isLoaded } = useSignIn()
  const { startSSOFlow } = useSSO()
  const router = useRouter()
  const colorScheme = useColorScheme()

  const [emailAddress, setEmailAddress] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [errorState, setErrorState] = React.useState<string | null>(null)

  // Handle the submission of the sign-in form
  const onSignInPress = async () => {
    if (!isLoaded) return
    setErrorState(null)
    try {
      const signInAttempt = await signIn.create({
        identifier: emailAddress,
        password,
      })

      // If sign-in process is complete, set the created session as active
      // and redirect the user
      if (signInAttempt.status === 'complete') {
        await setActive({ session: signInAttempt.createdSessionId })
        router.replace('/')
      } else {
        // If the status isn't complete, check why. User might need to
        // complete further steps.
        console.error(JSON.stringify(signInAttempt, null, 2))
      }
    } catch (err: any) {
      console.error("Sign In Error:", JSON.stringify(err, null, 2))
      // Extract user-friendly error message from Clerk
      const firstError = err?.errors?.[0]
      setErrorState(firstError?.longMessage || firstError?.message || 'An unknown sign-in error occurred.')
    }
  }

  // Handle Google Sign In
  const handleGoogleSignIn = React.useCallback(async () => {
    if (!isLoaded) return;
    setErrorState(null); // Clear previous errors

    try {
      // The redirectUrl needs to be the same as the one configured in your Clerk dashboard.
      // For Expo development, it's often recommended to use a deep link scheme.
      // Let's start with a basic redirect back to the app root for now.
      // You might need to adjust this based on your Clerk OAuth Callback URL settings and Expo linking config.
      const redirectUrl = '/'; // Or your specific callback route if you have one

      const result = await startSSOFlow({
        strategy: 'oauth_google',
        redirectUrl,
      });

      if (result.createdSessionId) {
        // If sign-in is successful, set the active session
        setActive({ session: result.createdSessionId });
        router.replace('/'); // Redirect to home or desired route
      } else {
        // Handle cases where sign-in needs further steps (e.g., MFA)
        // For OAuth, this usually means the flow was cancelled or failed externally.
        console.log('Google SSO flow did not create a session:', result);
        // Optionally update the UI or state if needed
      }
    } catch (err: any) {
      console.error("Google SSO Error:", JSON.stringify(err, null, 2));
      const firstError = err?.errors?.[0];
      setErrorState(firstError?.longMessage || firstError?.message || 'An unknown Google Sign-In error occurred.');
    }
  }, [isLoaded, startSSOFlow, setActive, router]);

  return (
    <ThemedView style={styles.container}>
      <ThemedText type="title" style={styles.title}>Sign in</ThemedText>
      
      {errorState && (
        <ThemedText style={styles.errorText}>{errorState}</ThemedText>
      )}

      <TextInput
        autoCapitalize="none"
        value={emailAddress}
        placeholder="Enter email"
        onChangeText={(email) => { setEmailAddress(email); setErrorState(null) }}
        style={[
          styles.input,
          { 
            borderColor: Colors[colorScheme ?? 'light'].icon, 
            color: Colors[colorScheme ?? 'light'].text 
          }
        ]}
        placeholderTextColor={Colors[colorScheme ?? 'light'].icon}
      />
      
      <TextInput
        value={password}
        placeholder="Enter password"
        secureTextEntry={true}
        onChangeText={(pass) => { setPassword(pass); setErrorState(null) }}
        style={[
          styles.input, 
          { 
            borderColor: Colors[colorScheme ?? 'light'].icon, 
            color: Colors[colorScheme ?? 'light'].text 
          }
        ]}
        placeholderTextColor={Colors[colorScheme ?? 'light'].icon}
      />
      
      <TouchableOpacity onPress={onSignInPress} style={styles.button} disabled={!isLoaded}>
        <ThemedText style={styles.buttonText}>Sign in</ThemedText>
      </TouchableOpacity>

      {/*horizontal line with "OR" text in the middle*/}
      <View style={styles.orLineContainer}>
        <View style={[styles.orLine, { backgroundColor: Colors[colorScheme ?? 'light'].icon }]} />
        <ThemedText style={styles.orText}>or</ThemedText>
        <View style={[styles.orLine, { backgroundColor: Colors[colorScheme ?? 'light'].icon }]} />
      </View>

      {/* Add Google Sign In Button */}
      <TouchableOpacity onPress={handleGoogleSignIn} style={[styles.button, styles.googleButton]} disabled={!isLoaded}>
        <Image source={require('@/assets/images/google_g_logo.svg')} style={styles.googleLogo} />
        <ThemedText style={[styles.buttonText, styles.googleButtonText]}>Sign in with Google</ThemedText>
      </TouchableOpacity>
      
      <View style={styles.linkContainer}>
        <ThemedText>Don't have an account? </ThemedText>
        <Link href={{ pathname: '/sign-up' }} asChild> 
          <TouchableOpacity>
            <ThemedText type="link">Sign up</ThemedText>
          </TouchableOpacity>
        </Link>
      </View>
    </ThemedView>
  )
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    padding: 20,
  },
  title: {
    marginBottom: 20,
    textAlign: 'center',
  },
  errorText: {
    color: 'red',
    textAlign: 'center',
    marginBottom: 15,
    fontSize: 14,
  },
  input: {
    height: 50,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 15,
    marginBottom: 15,
    fontSize: 16,
  },
  button: {
    backgroundColor: Colors.light.tint,
    paddingVertical: 15,
    borderRadius: 8,
    alignItems: 'center',
    marginBottom: 15,
  },
  googleButton: {
    backgroundColor: '#FFFFFF',
    marginBottom: 20,
    borderWidth: 1,
    borderColor: '#DADCE0',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: 'bold',
  },
  linkContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 10,
  },
  // Styles for the 'or' divider
  orLineContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginVertical: 15, // Added vertical margin
  },
  orLine: {
    flex: 1,
    height: 1,
    // backgroundColor will be set dynamically based on colorScheme
  },
  orText: {
    marginHorizontal: 10,
    fontSize: 14,
    fontWeight: '500', // Adjusted weight
    color: Colors.light.icon, // Use a subtle color, adjust if needed for dark mode
  },
  // Style for Google logo
  googleLogo: {
    width: 18,
    height: 18,
    marginRight: 10,
  },
  // Style for Google button text
  googleButtonText: {
    color: '#3C4043',
    fontWeight: '500',
  },
})