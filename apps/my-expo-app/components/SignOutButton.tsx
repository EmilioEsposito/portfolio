import { useClerk } from '@clerk/clerk-expo'
import * as Linking from 'expo-linking'
import { ThemedButton } from '@portfolio/ui'

export const SignOutButton = () => {
  // Use `useClerk()` to access the `signOut()` function
  const { signOut } = useClerk()

  const handleSignOut = async () => {
    try {
      await signOut()
      // Redirect to your desired page
      Linking.openURL(Linking.createURL('/'))
    } catch (err) {
      // See https://clerk.com/docs/custom-flows/error-handling
      // for more info on error handling
      console.error("Sign Out Error:", JSON.stringify(err, null, 2))
    }
  }

  return (
    <ThemedButton 
      title="Sign out" 
      onPress={handleSignOut} 
      type="primary"
    />
  )
}