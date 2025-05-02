import * as React from 'react'
import { TextInput, TouchableOpacity, View, StyleSheet, Image } from 'react-native'
import { useSignUp, useSSO } from '@clerk/clerk-expo'
import { Link, useRouter } from 'expo-router'
import { ThemedText } from '@/components/ThemedText'
import { ThemedView } from '@/components/ThemedView'
import { useColorScheme } from '@/hooks/useColorScheme'
import { Colors } from '@/constants/Colors'
import * as WebBrowser from 'expo-web-browser'

WebBrowser.maybeCompleteAuthSession()

export default function SignUpScreen() {
  const { isLoaded, signUp, setActive } = useSignUp()
  const { startSSOFlow } = useSSO()
  const router = useRouter()
  const colorScheme = useColorScheme()

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
      const redirectUrl = '/';

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
      <ThemedView style={styles.container}>
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
              borderColor: Colors[colorScheme ?? 'light'].icon,
              color: Colors[colorScheme ?? 'light'].text
            }
          ]}
          placeholderTextColor={Colors[colorScheme ?? 'light'].icon}
        />
        <TouchableOpacity onPress={onVerifyPress} style={styles.button} disabled={!isLoaded}>
          <ThemedText style={styles.buttonText}>Verify</ThemedText>
        </TouchableOpacity>
      </ThemedView>
    )
  }

  return (
    <ThemedView style={styles.container}>
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
        onChangeText={(password) => { setPassword(password); setErrorState(null) }}
        style={[
          styles.input,
          {
            borderColor: Colors[colorScheme ?? 'light'].icon,
            color: Colors[colorScheme ?? 'light'].text
          }
        ]}
        placeholderTextColor={Colors[colorScheme ?? 'light'].icon}
      />
      <TouchableOpacity onPress={onSignUpPress} style={styles.button} disabled={!isLoaded}>
        <ThemedText style={styles.buttonText}>Sign up with Email</ThemedText>
      </TouchableOpacity>

      {/*horizontal line with "OR" text in the middle*/}
      <View style={styles.orLineContainer}>
        <View style={[styles.orLine, { backgroundColor: Colors[colorScheme ?? 'light'].icon }]} />
        <ThemedText style={styles.orText}>or</ThemedText>
        <View style={[styles.orLine, { backgroundColor: Colors[colorScheme ?? 'light'].icon }]} />
      </View>

      <TouchableOpacity onPress={handleGoogleSignUp} style={[styles.button, styles.googleButton]} disabled={!isLoaded}>
        <Image source={require('@/assets/images/google_g_logo.svg')} style={styles.googleLogo} />
        <ThemedText style={[styles.buttonText, styles.googleButtonText]}>Sign up with Google</ThemedText>
      </TouchableOpacity>

      <View style={styles.linkContainer}>
        <ThemedText>Already have an account? </ThemedText>
        <Link href={{ pathname: '/sign-in' }} asChild>
          <TouchableOpacity>
            <ThemedText type="link">Sign in</ThemedText>
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
  googleLogo: {
    width: 18,
    height: 18,
    marginRight: 10,
  },
  googleButtonText: {
    color: '#3C4043',
    fontWeight: '500',
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
    color: Colors.light.icon, // Use a subtle color, adjust if needed for dark mode
  },
});