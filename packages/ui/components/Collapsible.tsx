import Ionicons from '@expo/vector-icons/Ionicons';
import { PropsWithChildren, useState } from 'react';
import { StyleSheet, TouchableOpacity, Platform } from 'react-native';
import React from 'react';

import { ThemedText } from './ThemedText';
import { ThemedView } from './ThemedView';
import { Colors } from '../constants/Colors';
import { useColorScheme } from '../hooks/useColorScheme';
import { IconSymbol } from './IconSymbol';

export function Collapsible({ children, title }: PropsWithChildren & { title: React.ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const theme = useColorScheme() ?? 'light';

  return (
    <ThemedView>
      <TouchableOpacity
        style={styles.heading}
        onPress={() => setIsOpen((value) => !value)}
        activeOpacity={0.8}>
        <IconSymbol
          name="chevron.right"
          size={18}
          weight="medium"
          color={theme === 'light' ? Colors.light.icon : Colors.dark.icon}
          style={{ transform: [{ rotate: isOpen ? '90deg' : '0deg' }] }}
        />

        {typeof title === 'string' ? <ThemedText type="defaultSemiBold">{title}</ThemedText> : title}
      </TouchableOpacity>
      {isOpen && <ThemedView style={styles.content}>{children}</ThemedView>}
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  heading: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  content: {
    marginTop: 6,
    marginLeft: 24,
  },
}); 