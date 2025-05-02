import { useSignIn } from '@clerk/clerk-expo'
import { Link, useRouter } from 'expo-router'
import { TextInput, TouchableOpacity, View, StyleSheet } from 'react-native'
import React from 'react'
import { ThemedText } from '@/components/ThemedText'
import { ThemedView } from '@/components/ThemedView'
import { useColorScheme } from '@/hooks/useColorScheme'
import { Colors } from '@/constants/Colors'

export default function Page() {
  const { signIn, setActive, isLoaded } = useSignIn()
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
        <ThemedText style={styles.buttonText}>Continue</ThemedText>
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
})