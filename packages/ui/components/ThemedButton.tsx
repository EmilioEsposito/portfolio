import React from 'react';
import {
  TouchableOpacity,
  Text,
  StyleSheet,
  type TouchableOpacityProps,
  type StyleProp,
  type ViewStyle,
  type TextStyle,
} from 'react-native';
import { Colors } from '../constants/Colors';
import { useColorScheme } from '../hooks/useColorScheme';

interface ThemedButtonProps extends TouchableOpacityProps {
  title?: string;
  children?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
  type?: 'primary' | 'google' | 'default';
}

export function ThemedButton({
  title,
  children,
  style,
  textStyle,
  disabled,
  type = 'primary',
  onPress,
  ...rest
}: ThemedButtonProps) {
  // const colorScheme = useColorScheme() ?? 'light'; // No longer needed for primary if its color is fixed

  const getButtonStyles = (): StyleProp<ViewStyle> => {
    switch (type) {
      case 'google':
        return {
          backgroundColor: '#FFFFFF',
          borderColor: '#DADCE0',
          borderWidth: 1,
        };
      case 'primary':
      default:
        return {
          backgroundColor: '#007AFF',
        };
    }
  };

  const getTextStyles = (): StyleProp<TextStyle> => {
    const colorScheme = useColorScheme() ?? 'light'; // Keep for non-primary types if they depend on theme
    switch (type) {
      case 'google':
        return {
          color: '#3C4043', // Google's standard text color
          fontWeight: '500',
        };
      case 'primary':
      default:
        return {
          color: '#fff', // White text for the blue button
          fontWeight: 'bold',
        };
    }
  };

  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      {...rest}
      style={[
        styles.button,
        getButtonStyles(),
        disabled && styles.disabledButton,
        style,
      ]}
    >
      {children}
      {title && (
        <Text style={[styles.text, getTextStyles(), textStyle]}>
          {title}
        </Text>
      )}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  button: {
    paddingVertical: 10,      // Updated to match index.tsx
    paddingHorizontal: 20,    // Added to match index.tsx
    borderRadius: 5,          // Updated to match index.tsx
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 10,         // Updated to match index.tsx
    flexDirection: 'row',
  },
  text: {
    fontSize: 16,
    // textAlign: 'center', // Handled by alignItems/justifyContent on button
  },
  disabledButton: {
    opacity: 0.5,
  },
}); 