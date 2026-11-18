import { router } from 'expo-router';
import React, { useState } from 'react';
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
import { z } from 'zod';

import { Screen } from '../components/Screen';
import { api } from '../services/api';
import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';

const emailSchema = z.string().trim().email();

export default function ForgotPasswordScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const { width } = useWindowDimensions();
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showResetFields, setShowResetFields] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState<'error' | 'success'>('error');

  const handleContinue = () => {
    setMessage('');
    if (!emailSchema.safeParse(email).success) {
      setMessage('Digite um email válido.');
      setMessageType('error');
      return;
    }
    setShowResetFields(true);
  };

  const handleReset = async () => {
    setMessage('');
    if (!code.trim()) {
      setMessage('Informe o código de recuperação.');
      setMessageType('error');
      return;
    }
    if (newPassword.length < 8) {
      setMessage('A nova senha deve ter pelo menos 8 caracteres.');
      setMessageType('error');
      return;
    }
    if (newPassword !== confirmPassword) {
      setMessage('As senhas não coincidem.');
      setMessageType('error');
      return;
    }

    setIsSubmitting(true);
    try {
      await api.post('/auth/password-recovery/reset', {
        email: email.trim(),
        code: code.trim(),
        new_password: newPassword,
        confirm_new_password: confirmPassword,
      });
      setCompleted(true);
      setMessageType('success');
      setMessage('Senha alterada. Entre com sua nova senha.');
      setCode('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (error: any) {
      setMessage(error.response?.status === 429
        ? 'Muitas tentativas. Aguarde antes de tentar novamente.'
        : 'Não foi possível validar os dados. Confira o email e o código de recuperação.');
      setMessageType('error');
    } finally {
      setIsSubmitting(false);
    }
  };

  const isWide = width >= 700;

  return (
    <Screen hideBottomNav>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
        >
          <View testID="recovery-card" style={[styles.card, isWide && styles.cardWide]}>
            <Pressable
              onPress={() => router.replace('/login' as never)}
              accessibilityRole="button"
              style={styles.backLink}
            >
              <Text style={styles.backText}>‹  Voltar para o login</Text>
            </Pressable>

            <View style={styles.heading}>
              <Text style={styles.title}>Recuperar senha</Text>
              <Text style={styles.subtitle}>
                Não enviamos email. Para recuperar a conta, você precisa do código
                de recuperação salvo anteriormente.
              </Text>
            </View>

            {!completed ? (
              <>
                <View style={styles.field}>
                  <Text style={styles.label}>Email da conta</Text>
                  <TextInput
                    testID="recovery-email"
                    value={email}
                    onChangeText={setEmail}
                    onSubmitEditing={handleContinue}
                    placeholder="seu@email.com"
                    placeholderTextColor={palette.muted}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="email-address"
                    textContentType="emailAddress"
                    accessibilityLabel="Email da conta"
                    style={styles.input}
                    returnKeyType="continue"
                  />
                </View>

                {!showResetFields ? (
                  <Pressable
                    style={styles.primaryButton}
                    onPress={handleContinue}
                    accessibilityRole="button"
                  >
                    <Text style={styles.primaryButtonText}>Continuar</Text>
                  </Pressable>
                ) : (
                  <>
                    <View style={styles.field}>
                      <Text style={styles.label}>Código de recuperação</Text>
                      <TextInput
                        testID="recovery-code"
                        value={code}
                        onChangeText={setCode}
                        placeholder="Cole o código salvo"
                        placeholderTextColor={palette.muted}
                        autoCapitalize="none"
                        autoCorrect={false}
                        accessibilityLabel="Código de recuperação"
                        style={styles.input}
                      />
                    </View>
                    <View style={styles.field}>
                      <Text style={styles.label}>Nova senha</Text>
                      <TextInput
                        testID="recovery-new-password"
                        value={newPassword}
                        onChangeText={setNewPassword}
                        placeholder="Pelo menos 8 caracteres"
                        placeholderTextColor={palette.muted}
                        secureTextEntry
                        textContentType="newPassword"
                        autoComplete="new-password"
                        accessibilityLabel="Nova senha"
                        style={styles.input}
                      />
                    </View>
                    <View style={styles.field}>
                      <Text style={styles.label}>Confirmar nova senha</Text>
                      <TextInput
                        testID="recovery-confirm-password"
                        value={confirmPassword}
                        onChangeText={setConfirmPassword}
                        onSubmitEditing={handleReset}
                        placeholder="Digite a nova senha novamente"
                        placeholderTextColor={palette.muted}
                        secureTextEntry
                        textContentType="newPassword"
                        autoComplete="new-password"
                        accessibilityLabel="Confirmar nova senha"
                        style={styles.input}
                        returnKeyType="done"
                      />
                    </View>
                    <Pressable
                      style={[styles.primaryButton, isSubmitting && styles.disabled]}
                      onPress={handleReset}
                      disabled={isSubmitting}
                      accessibilityRole="button"
                    >
                      <Text style={styles.primaryButtonText}>
                        {isSubmitting ? 'Salvando...' : 'Salvar nova senha'}
                      </Text>
                    </Pressable>
                  </>
                )}
              </>
            ) : null}

            {message ? (
              <Text
                style={messageType === 'success' ? styles.successMessage : styles.errorMessage}
                accessibilityRole="alert"
              >
                {message}
              </Text>
            ) : null}

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
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.lg,
  },
  card: {
    width: '100%',
    maxWidth: 520,
    padding: spacing.xl,
    gap: spacing.lg,
    backgroundColor: palette.surface,
    borderColor: palette.border,
    borderWidth: 1,
    borderRadius: radius.lg,
  },
  cardWide: { padding: spacing.xxl },
  backLink: { alignSelf: 'flex-start', minHeight: 44, justifyContent: 'center' },
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
  successMessage: { color: palette.success, fontSize: 14, lineHeight: 20 },
}));
