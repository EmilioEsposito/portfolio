import * as React from 'react'
import { TextInput, TouchableOpacity, View, StyleSheet } from 'react-native'
import { useSignUp } from '@clerk/clerk-expo'
import { Link, useRouter } from 'expo-router'
import { ThemedText } from '@/components/ThemedText'
import { ThemedView } from '@/components/ThemedView'
import { useColorScheme } from '@/hooks/useColorScheme'
import { Colors } from '@/constants/Colors'

export default function SignUpScreen() {
  const { isLoaded, signUp, setActive } = useSignUp()
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
        <ThemedText style={styles.buttonText}>Continue</ThemedText>
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
    marginBottom: 20,
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
});