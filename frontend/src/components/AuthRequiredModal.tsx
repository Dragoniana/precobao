import { router } from 'expo-router';
import React from 'react';
import {
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { palette, radius, spacing } from '../theme';

type AuthRequiredModalProps = {
  visible: boolean;
  onClose: () => void;
};

export function AuthRequiredModal({
  visible,
  onClose,
}: AuthRequiredModalProps) {
  const isWeb = Platform.OS === 'web';

  const goToLogin = () => {
    onClose();
    router.push('/login' as any);
  };

  const goToSignup = () => {
    onClose();
    router.push('/signup' as any);
  };

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={styles.overlay}>
        <Pressable style={styles.backdrop} onPress={onClose} />

        <View style={[styles.modalCard, isWeb && styles.modalCardWeb]}>
          <Text style={styles.title}>Recurso exclusivo</Text>

          <Text style={styles.description}>
            Recurso para usuários autenticados. Deseja criar uma conta ou
            logar em uma conta existente?
          </Text>

          <Pressable style={styles.primaryButton} onPress={goToSignup}>
            <Text style={styles.primaryButtonText}>Criar conta</Text>
          </Pressable>

          <Pressable style={styles.secondaryButton} onPress={goToLogin}>
            <Text style={styles.secondaryButtonText}>Fazer login</Text>
          </Pressable>

          <Pressable style={styles.closeButton} onPress={onClose}>
            <Text style={styles.closeButtonText}>Fechar</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.32)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.lg,
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
  },
  modalCard: {
    width: '100%',
    maxWidth: 420,
    backgroundColor: palette.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: '#E7E7E7',
    padding: spacing.lg,
    zIndex: 2,
  },
  modalCardWeb: {
    maxWidth: 460,
  },
  title: {
    color: palette.primary,
    fontSize: 24,
    fontWeight: '700',
    marginBottom: spacing.md,
    textAlign: 'center',
  },
  description: {
    color: palette.text,
    fontSize: 15,
    lineHeight: 22,
    textAlign: 'center',
    marginBottom: spacing.lg,
  },
  primaryButton: {
    minHeight: 48,
    borderRadius: radius.pill,
    backgroundColor: palette.primary,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: spacing.sm,
  },
  primaryButtonText: {
    color: palette.surface,
    fontSize: 16,
    fontWeight: '700',
  },
  secondaryButton: {
    minHeight: 48,
    borderRadius: radius.pill,
    borderWidth: 1.5,
    borderColor: palette.primary,
    backgroundColor: palette.surface,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: spacing.sm,
  },
  secondaryButtonText: {
    color: palette.primary,
    fontSize: 16,
    fontWeight: '700',
  },
  closeButton: {
    minHeight: 42,
    justifyContent: 'center',
    alignItems: 'center',
  },
  closeButtonText: {
    color: palette.textSoft,
    fontSize: 14,
    fontWeight: '600',
  },
});