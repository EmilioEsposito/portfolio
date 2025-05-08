import * as React from 'react'
import { TextInput, TouchableOpacity, View, StyleSheet, Image, KeyboardAvoidingView, Platform, useColorScheme as useReactNativeColorScheme, ScrollView } from 'react-native'
import { useSignUp, useSSO } from '@clerk/clerk-expo'
import { Link, useRouter } from 'expo-router'
import { ThemedView, Colors, useColorScheme, ThemedButton, ThemedText } from '@portfolio/ui'
import * as WebBrowser from 'expo-web-browser'
import * as Linking from 'expo-linking'

WebBrowser.maybeCompleteAuthSession()

export default function SignUpScreen() {
  const { isLoaded, signUp, setActive } = useSignUp()
  const { startSSOFlow } = useSSO()
  const router = useRouter()
  const colorScheme = useColorScheme() ?? 'light'
  const rnColorScheme = useReactNativeColorScheme() ?? 'light'

  const [emailAddress, setEmailAddress] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [pendingVerification, setPendingVerification] = React.useState(false)
  const [code, setCode] = React.useState('')
  const [errorState, setErrorState] = React.useState<string | null>(null)

  const onSignUpPress = async () => {
    if (!isLoaded) return
    setErrorState(null)
    try {
      await signUp.create({
        emailAddress,
        password,
      })
      await signUp.prepareEmailAddressVerification({ strategy: 'email_code' })
      setPendingVerification(true)
    } catch (err: any) {
      console.error("Sign Up Error:", JSON.stringify(err, null, 2))
      const firstError = err?.errors?.[0]
      setErrorState(firstError?.longMessage || firstError?.message || 'An unknown sign-up error occurred.')
    }
  }

  const onVerifyPress = async () => {
    if (!isLoaded) return
    setErrorState(null)
    try {
      const signUpAttempt = await signUp.attemptEmailAddressVerification({
        code,
      })
      if (signUpAttempt.status === 'complete') {
        await setActive({ session: signUpAttempt.createdSessionId })
        router.replace('/')
      } else {
        console.error(JSON.stringify(signUpAttempt, null, 2))
        setErrorState('Verification failed. Please check the code and try again.')
      }
    } catch (err: any) {
      console.error("Verification Error:", JSON.stringify(err, null, 2))
      const firstError = err?.errors?.[0]
      setErrorState(firstError?.longMessage || firstError?.message || 'An unknown verification error occurred.')
    }
  }

  const handleGoogleSignUp = React.useCallback(async () => {
    if (!isLoaded) return
    setErrorState(null)

    try {
      const redirectUrl = Linking.createURL('/sso-callback')

      console.log("Starting SSO (Sign Up) with redirectUrl:", redirectUrl)

      const result = await startSSOFlow({
        strategy: 'oauth_google',
        redirectUrl,
      })

      if (result.createdSessionId) {
        setActive({ session: result.createdSessionId })
        router.replace('/')
      } else {
        console.log('Google SSO flow did not create a session during sign-up:', result)
      }
    } catch (err: any) {
      console.error("Google SSO Error (Sign Up):", JSON.stringify(err, null, 2))
      const firstError = err?.errors?.[0]
      setErrorState(firstError?.longMessage || firstError?.message || 'An unknown Google Sign-Up error occurred.')
    }
  }, [isLoaded, startSSOFlow, setActive, router])

  if (pendingVerification) {
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
            <ThemedText type="title" style={styles.title}>Verify your email</ThemedText>
            {errorState && (
              <ThemedText style={styles.errorText}>{errorState}</ThemedText>
            )}
            <TextInput
              value={code}
              placeholder="Enter your verification code"
              onChangeText={(code) => { setCode(code); setErrorState(null) }}
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
              title="Verify"
              onPress={onVerifyPress}
              disabled={!isLoaded}
              type="primary"
            />
          </ScrollView>
        </KeyboardAvoidingView>
      </ThemedView>
    )
  }

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
          <ThemedText type="title" style={styles.title}>Sign up</ThemedText>
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
            onChangeText={(password) => { setPassword(password); setErrorState(null) }}
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
            title="Sign up with Email"
            onPress={onSignUpPress}
            disabled={!isLoaded}
            type="primary"
          />

          <View style={styles.orLineContainer}>
            <View style={[styles.orLine, { backgroundColor: Colors[colorScheme].icon }]} />
            <ThemedText style={styles.orText} lightColor={Colors.light.icon} darkColor={Colors.dark.icon}>or</ThemedText>
            <View style={[styles.orLine, { backgroundColor: Colors[colorScheme].icon }]} />
          </View>

          <ThemedButton
            title="Sign up with Google"
            onPress={handleGoogleSignUp}
            disabled={!isLoaded}
            type="google"
            style={styles.googleButtonCustom}
          >
            <Image source={require('@/assets/images/google_g_logo.png')} style={styles.googleLogo} />
          </ThemedButton>

          <View style={styles.linkContainer}>
            <ThemedText>Already have an account? </ThemedText>
            <Link href={{ pathname: '/sign-in' }} asChild>
              <TouchableOpacity>
                <ThemedText type="link">Sign in</ThemedText>
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
  },
  googleButtonCustom: {
    marginBottom: 20,
  },
  linkContainer: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 10,
  },
  googleLogo: {
    width: 18,
    height: 18,
    marginRight: 10,
  },
  orLineContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginVertical: 15,
  },
  orLine: {
    flex: 1,
    height: 1,
  },
  orText: {
    marginHorizontal: 10,
    fontSize: 14,
    fontWeight: '500',
  },
});