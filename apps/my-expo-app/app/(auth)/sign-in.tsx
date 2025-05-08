import { useSignIn, useSSO } from '@clerk/clerk-expo'
import { Link, useRouter } from 'expo-router'
import { TextInput, TouchableOpacity, View, StyleSheet, Image, useColorScheme as useReactNativeColorScheme, KeyboardAvoidingView, ScrollView, Platform } from 'react-native'
import React from 'react'
import { ThemedView, Colors, useColorScheme, ThemedButton, ThemedText } from '@portfolio/ui'
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';

// Ensure that the dismissAuthSession function is called when the component mounts
// This is necessary for the OAuth flow to work correctly on web platforms.
WebBrowser.maybeCompleteAuthSession();

export default function Page() {
  const { signIn, setActive, isLoaded } = useSignIn()
  const { startSSOFlow } = useSSO()
  const router = useRouter()
  const colorScheme = useColorScheme() ?? 'light'
  // const rnColorScheme = useReactNativeColorScheme() ?? 'light'; // If needed for specific RN features not covered by @portfolio/ui theming

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
      // Use Linking.createURL with the explicit callback path
      const redirectUrl = Linking.createURL('/sso-callback');

      console.log("Starting SSO with redirectUrl:", redirectUrl); // Log the URL

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
    <ThemedView style={styles.outerContainer}>
      <KeyboardAvoidingView 
        behavior={Platform.OS === "ios" ? "padding" : "height"} 
        style={styles.keyboardAvoidingContainer}
      >
        <ScrollView 
          contentContainerStyle={styles.scrollContentContainer}
          keyboardShouldPersistTaps="handled"
        >
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
                borderColor: Colors[colorScheme].icon, 
                color: Colors[colorScheme].text 
              }
            ]}
            placeholderTextColor={Colors[colorScheme].icon}
          />
          
          <TextInput
            value={password}
            placeholder="Enter password"
            secureTextEntry={true}
            onChangeText={(pass) => { setPassword(pass); setErrorState(null) }}
            style={[
              styles.input, 
              { 
                borderColor: Colors[colorScheme].icon, 
                color: Colors[colorScheme].text 
              }
            ]}
            placeholderTextColor={Colors[colorScheme].icon}
          />
          
          <ThemedButton
            title="Sign in"
            onPress={onSignInPress}
            disabled={!isLoaded}
            type="primary"
          />

          {/*horizontal line with "OR" text in the middle*/}
          <View style={styles.orLineContainer}>
            <View style={[styles.orLine, { backgroundColor: Colors[colorScheme].icon }]} />
            <ThemedText style={styles.orText} lightColor={Colors.light.icon} darkColor={Colors.dark.icon}>or</ThemedText>
            <View style={[styles.orLine, { backgroundColor: Colors[colorScheme].icon }]} />
          </View>

          {/* Add Google Sign In Button */}
          <ThemedButton
            title="Sign in with Google"
            onPress={handleGoogleSignIn}
            disabled={!isLoaded}
            type="google"
            style={styles.googleButtonCustom}
          >
            <Image source={require('@/assets/images/google_g_logo.png')} style={styles.googleLogo} />
          </ThemedButton>
          
          <View style={styles.linkContainer}>
            <ThemedText>Don't have an account? </ThemedText>
            <Link href={{ pathname: '/sign-up' }} asChild> 
              <TouchableOpacity>
                <ThemedText type="link">Sign up</ThemedText>
              </TouchableOpacity>
            </Link>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </ThemedView>
  )
}

const styles = StyleSheet.create({
  outerContainer: {
    flex: 1,
  },
  keyboardAvoidingContainer: {
    flex: 1,
  },
  scrollContentContainer: {
    flexGrow: 1,
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
    // borderColor and color are dynamic
  },
  // button & buttonText are removed as ThemedButton handles this
  googleButtonCustom: { // Renamed from googleButton to avoid conflict if we only need margin
    marginBottom: 20,
    // Other styles like backgroundColor, borderColor, flexDirection are handled by ThemedButton type='google'
  },
  // googleButtonText is removed
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
    fontWeight: '500',
    // color is dynamic via ThemedText props
  },
  // Style for Google logo - keep this as it's passed as a child
  googleLogo: {
    width: 18,
    height: 18,
    marginRight: 10,
  },
  // googleButtonText is handled by ThemedButton type='google'
})