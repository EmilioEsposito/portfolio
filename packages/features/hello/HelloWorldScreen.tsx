import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

export function HelloWorldScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.text}>Hello from Shared Feature!</Text>
      <Text style={styles.text}>This screen is defined in packages/features.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 20,
    backgroundColor: '#f0f0f0', // A light background for visibility
  },
  text: {
    fontSize: 18,
    textAlign: 'center',
    marginVertical: 5,
  },
}); 