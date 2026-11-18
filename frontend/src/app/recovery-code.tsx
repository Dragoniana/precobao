import Ionicons from '@expo/vector-icons/Ionicons';
import { router, useIsFocused } from 'expo-router';
import React, { useEffect, useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';

import { Screen } from '../components/Screen';
import { useSessionContext } from '../context/AppContext';
import { api } from '../services/api';
import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';

export default function RecoveryCodeScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const { isAuthenticated } = useSessionContext();
  const isFocused = useIsFocused();
  const { width } = useWindowDimensions();
  const [currentPassword, setCurrentPassword] = useState('');
  const [recoveryCode, setRecoveryCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  useEffect(() => {
    if (!isFocused) {
      setRecoveryCode(null);
      setExpiresAt('');
      setCurrentPassword('');
    }
  }, [isFocused]);

  const createCode = async () => {
    if (!currentPassword) {
      setErrorMessage('Digite sua senha atual para confirmar sua identidade.');
      return;
    }
    setIsSubmitting(true);
    setErrorMessage('');
    try {
      const { data } = await api.post('/auth/password-recovery/codes', {
        current_password: currentPassword,
      });
      setRecoveryCode(data.recovery_code);
      setExpiresAt(data.expires_at);
      setCurrentPassword('');
    } catch (error: any) {
      setErrorMessage(error.response?.status === 429
        ? 'Muitas tentativas. Aguarde antes de tentar novamente.'
        : 'Não foi possível confirmar sua identidade. Confira a senha atual.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const expiresDate = expiresAt
    ? new Date(expiresAt).toLocaleDateString('pt-BR')
    : '';

  return (
    <Screen hideBottomNav>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView contentContainerStyle={styles.scrollContent} keyboardShouldPersistTaps="handled">
          <View style={[styles.card, width >= 700 && styles.cardWide]}>
            <Pressable
              onPress={() => router.replace('/(app)/profile' as never)}
              accessibilityRole="button"
              style={styles.backLink}
            >
              <Text style={styles.backText}>‹  Voltar ao perfil</Text>
            </Pressable>

            <View style={styles.heading}>
              <Text style={styles.title}>Código de recuperação</Text>
              <Text style={styles.subtitle}>
                Este código permite alterar a senha sem acesso ao email. Guarde-o
                em um lugar seguro; ele não poderá ser exibido novamente.
              </Text>
            </View>

            {!isAuthenticated ? (
              <Text style={styles.errorMessage} accessibilityRole="alert">
                Entre na sua conta para gerar um código de recuperação.
              </Text>
            ) : recoveryCode ? (
              <View style={styles.codePanel}>
                <View style={styles.noticeRow}>
                  <Ionicons name="warning-outline" size={22} color={palette.warning} />
                  <Text style={styles.noticeText}>
                    Copie e salve este código agora. Ao sair desta tela, ele não será mostrado novamente.
                  </Text>
                </View>
                <Text testID="one-time-recovery-code" selectable style={styles.codeText} accessibilityLabel="Código de recuperação">
                  {recoveryCode}
                </Text>
                <Text style={styles.expiryText}>Válido até {expiresDate}</Text>
                <Pressable
                  style={styles.primaryButton}
                  onPress={() => router.replace('/(app)/profile' as never)}
                  accessibilityRole="button"
                >
                  <Text style={styles.primaryButtonText}>Concluir</Text>
                </Pressable>
              </View>
            ) : (
              <>
                <View style={styles.field}>
                  <Text style={styles.label}>Senha atual</Text>
                  <TextInput
                    testID="recovery-current-password"
                    value={currentPassword}
                    onChangeText={setCurrentPassword}
                    onSubmitEditing={createCode}
                    placeholder="Confirme sua senha atual"
                    placeholderTextColor={palette.muted}
                    secureTextEntry
                    textContentType="password"
                    accessibilityLabel="Senha atual"
                    style={styles.input}
                    returnKeyType="done"
                  />
                </View>
                {errorMessage ? (
                  <Text style={styles.errorMessage} accessibilityRole="alert">
                    {errorMessage}
                  </Text>
                ) : null}
                <Pressable
                  style={[styles.primaryButton, isSubmitting && styles.disabled]}
                  onPress={createCode}
                  disabled={isSubmitting}
                  accessibilityRole="button"
                >
                  <Text style={styles.primaryButtonText}>
                    {isSubmitting ? 'Gerando...' : 'Confirmar e gerar código'}
                  </Text>
                </Pressable>
              </>
            )}
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

const useStyles = createThemedStyles((palette) => ({
  flex: { flex: 1 },
  scrollContent: {
    flexGrow: 1,
    width: '100%',
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.lg,
  },
  card: {
    width: '100%',
    maxWidth: 560,
    gap: spacing.lg,
    padding: spacing.xl,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.lg,
  },
  cardWide: { padding: spacing.xxl },
  backLink: { minHeight: 44, alignSelf: 'flex-start', justifyContent: 'center' },
  backText: { color: palette.primaryDark, fontSize: 14, fontWeight: '600' },
  heading: { gap: spacing.xs },
  title: { color: palette.text, fontSize: 28, lineHeight: 35, fontWeight: '800' },
  subtitle: { color: palette.textSoft, fontSize: 14, lineHeight: 21 },
  field: { gap: spacing.xs },
  label: { color: palette.text, fontSize: 14, fontWeight: '600' },
  input: {
    width: '100%',
    minHeight: 52,
    paddingHorizontal: spacing.lg,
    color: palette.text,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.md,
    fontSize: 16,
  },
  codePanel: {
    gap: spacing.md,
    padding: spacing.lg,
    backgroundColor: palette.warningSoft,
    borderWidth: 1,
    borderColor: palette.warningBorder,
    borderRadius: radius.md,
  },
  noticeRow: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  noticeText: { flex: 1, color: palette.text, fontSize: 14, lineHeight: 20 },
  codeText: {
    color: palette.text,
    fontSize: 18,
    lineHeight: 28,
    fontWeight: '700',
    letterSpacing: 0.5,
    textAlign: 'center',
    padding: spacing.md,
    backgroundColor: palette.surface,
    borderRadius: radius.sm,
  },
  expiryText: { color: palette.textSoft, textAlign: 'center', fontSize: 13 },
  primaryButton: {
    width: '100%',
    minHeight: 50,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.primary,
    borderRadius: radius.md,
  },
  primaryButtonText: { color: palette.onPrimary, fontSize: 15, fontWeight: '700' },
  disabled: { opacity: 0.6 },
  errorMessage: { color: palette.danger, fontSize: 14, lineHeight: 20 },
}));
