import { router } from 'expo-router';
import React, { useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { z } from 'zod';

import { Screen } from '../components/Screen';
import { api } from '../services/api';
import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';

const emailSchema = z.string().trim().email();

export default function ForgotPasswordScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const [email, setEmail] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [message, setMessage] = useState('');

  const requestLink = async () => {
    setMessage('');
    if (!emailSchema.safeParse(email).success) {
      setMessage('Digite um email válido.');
      return;
    }
    setIsSubmitting(true);
    try {
      await api.post('/auth/password-reset/request', { email: email.trim() });
      setSubmitted(true);
    } catch (error: any) {
      setMessage(error.response?.status === 429
        ? 'Muitas solicitações. Aguarde antes de tentar novamente.'
        : 'Não foi possível solicitar agora. Tente novamente mais tarde.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Screen hideBottomNav>
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={styles.scrollContent} keyboardShouldPersistTaps="handled">
          <View testID="recovery-card" style={styles.card}>
            <Pressable onPress={() => router.replace('/login' as never)} accessibilityRole="button" style={styles.backLink}>
              <Text style={styles.backText}>‹  Voltar para o login</Text>
            </Pressable>
            <View style={styles.heading}>
              <Text style={styles.title}>Recuperar senha</Text>
              <Text style={styles.subtitle}>Informe o email da sua conta para receber um link seguro de redefinição.</Text>
            </View>
            {!submitted ? (
              <>
                <View style={styles.field}>
                  <Text style={styles.label}>Email da conta</Text>
                  <TextInput testID="recovery-email" value={email} onChangeText={setEmail} onSubmitEditing={requestLink}
                    placeholder="seu@email.com" placeholderTextColor={palette.muted} autoCapitalize="none" autoCorrect={false}
                    keyboardType="email-address" textContentType="emailAddress" accessibilityLabel="Email da conta"
                    style={styles.input} returnKeyType="send" />
                </View>
                {message ? <Text style={styles.errorMessage} accessibilityRole="alert">{message}</Text> : null}
                <Pressable testID="send-reset-link" style={[styles.primaryButton, isSubmitting && styles.disabled]}
                  onPress={requestLink} disabled={isSubmitting} accessibilityRole="button">
                  <Text style={styles.primaryButtonText}>{isSubmitting ? 'Enviando...' : 'Enviar link de recuperação'}</Text>
                </Pressable>
              </>
            ) : (
              <Text style={styles.successMessage} accessibilityRole="alert">
                Se o endereço estiver cadastrado, enviaremos um link de recuperação. O link expira em 20 minutos.
              </Text>
            )}
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

const useStyles = createThemedStyles((palette) => ({
  flex: { flex: 1 },
  scrollContent: { flexGrow: 1, width: '100%', justifyContent: 'center', alignItems: 'center', padding: spacing.lg },
  card: { width: '100%', maxWidth: 520, padding: spacing.xl, gap: spacing.lg, backgroundColor: palette.surface, borderColor: palette.border, borderWidth: 1, borderRadius: radius.lg },
  backLink: { alignSelf: 'flex-start', minHeight: 44, justifyContent: 'center' },
  backText: { color: palette.primaryDark, fontSize: 14, fontWeight: '600' },
  heading: { gap: spacing.xs },
  title: { color: palette.text, fontSize: 28, lineHeight: 35, fontWeight: '800' },
  subtitle: { color: palette.textSoft, fontSize: 14, lineHeight: 21 },
  field: { gap: spacing.xs },
  label: { color: palette.text, fontSize: 14, fontWeight: '600' },
  input: { width: '100%', minHeight: 52, paddingHorizontal: spacing.lg, color: palette.text, backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, fontSize: 16 },
  primaryButton: { width: '100%', minHeight: 50, alignItems: 'center', justifyContent: 'center', backgroundColor: palette.primary, borderRadius: radius.md },
  primaryButtonText: { color: palette.onPrimary, fontSize: 15, fontWeight: '700' },
  disabled: { opacity: 0.6 },
  errorMessage: { color: palette.danger, fontSize: 14, lineHeight: 20 },
  successMessage: { color: palette.success, fontSize: 14, lineHeight: 20 },
}));
