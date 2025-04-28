import React from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';

interface SharedButtonProps {
  onPress: () => void;
  text: string;
}

export const SharedButton: React.FC<SharedButtonProps> = ({ onPress, text }) => {
  return (
    <Pressable onPress={onPress} style={styles.button}>
      <View style={styles.container}>
        <Text style={styles.text}>{text}</Text>
      </View>
    </Pressable>
  );
};

const styles = StyleSheet.create({
  button: {
    // Basic styling for the pressable area
  },
  container: {
    paddingVertical: 10,
    paddingHorizontal: 20,
    backgroundColor: '#007AFF', // Blue background
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  text: {
    color: '#FFFFFF', // White text
    fontSize: 16,
    fontWeight: 'bold',
  },
});

// Optional: Export default if preferred, but named export is fine
// export default SharedButton; 