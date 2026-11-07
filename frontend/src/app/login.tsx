import { zodResolver } from '@hookform/resolvers/zod';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import React, { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';
import { z } from 'zod';

import { Screen } from '../components/Screen';
import { useSessionContext } from '../context/AppContext';
import { palette, radius, spacing } from '../theme';

const loginSchema = z.object({
  email: z.string().email('Digite um email válido'),
  password: z.string().min(1, 'Senha é obrigatória'),
});

type LoginFormValues = z.infer<typeof loginSchema>;

function translateBackendMessage(message: string) {
  const normalized = message.toLowerCase();

  if (
    normalized.includes('valid email') ||
    normalized.includes('email address') ||
    normalized.includes('@-sign') ||
    normalized.includes('must have an @') ||
    normalized.includes('value is not a valid email') ||
    normalized.includes('input should be a valid email')
  ) {
    return 'Digite um email válido. O email precisa conter @.';
  }

  if (
    normalized.includes('field required') ||
    normalized.includes('missing') ||
    normalized.includes('required')
  ) {
    return 'Preencha todos os campos obrigatórios.';
  }

  if (
    normalized.includes('incorrect') ||
    normalized.includes('login_bad_credentials') ||
    normalized.includes('unauthorized') ||
    normalized.includes('invalid credentials') ||
    normalized.includes('invalid login') ||
    normalized.includes('invalid email or password')
  ) {
    return 'Email ou senha incorretos.';
  }

  if (normalized.includes('invalid') && normalized.includes('password')) {
    return 'Senha inválida.';
  }

  if (
    normalized.includes('user not found') ||
    normalized.includes('not found')
  ) {
    return 'Usuário não encontrado.';
  }

  if (
    normalized.includes('value error') ||
    normalized.includes('validation error')
  ) {
    return 'Confira os dados informados e tente novamente.';
  }

  return message;
}

function getReadableMessage(value: unknown, fallback: string) {
  if (!value) {
    return fallback;
  }

  if (typeof value === 'string') {
    return translateBackendMessage(value);
  }

  if (Array.isArray(value)) {
    const messages = value
      .map((item) => {
        if (typeof item === 'string') {
          return translateBackendMessage(item);
        }

        if (item && typeof item === 'object' && 'msg' in item) {
          return translateBackendMessage(String((item as { msg: unknown }).msg));
        }

        if (item && typeof item === 'object' && 'message' in item) {
          return translateBackendMessage(
            String((item as { message: unknown }).message)
          );
        }

        return fallback;
      })
      .filter(Boolean);

    return messages.length ? messages.join('\n') : fallback;
  }

  if (typeof value === 'object' && 'msg' in value) {
    return translateBackendMessage(String((value as { msg: unknown }).msg));
  }

  if (typeof value === 'object' && 'message' in value) {
    return translateBackendMessage(
      String((value as { message: unknown }).message)
    );
  }

  return fallback;
}

export default function LoginScreen() {
  const { continueAsGuest, signIn } = useSessionContext();
  const { width } = useWindowDimensions();
  const isWeb = Platform.OS === 'web';
  const isWide = isWeb && width >= 900;
  const { control, handleSubmit, setError, getValues, formState: { errors, isSubmitting } } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  });
  const [showPassword, setShowPassword] = useState(false);
  const [focused, setFocused] = useState<'email' | 'password' | null>(null);
  const [feedback, setFeedback] = useState<{ type: 'error' | 'success'; text: string } | null>(null);

  const handleLogin = async (data: LoginFormValues) => {
    setFeedback(null);
    try {
      const result = await signIn(data.email, data.password);
      if (!result.ok) {
        setFeedback({ type: 'error', text: getReadableMessage(result.message, 'Não foi possível entrar. Verifique email e senha.') });
        setError('root', { message: result.message });
        return;
      }
      setFeedback({ type: 'success', text: getReadableMessage(result.message, 'Login realizado com sucesso.') });
      router.replace('/(app)/home' as never);
    } catch {
      setFeedback({ type: 'error', text: 'Erro ao tentar entrar. Tente novamente.' });
    }
  };

  const onGuestPress = () => {
    const { email, password } = getValues();
    if (!email.trim() && !password.trim()) {
      continueAsGuest();
      router.replace('/(app)/home' as never);
    } else {
      setFeedback({ type: 'error', text: 'Para entrar como convidado, deixe os campos vazios.' });
    }
  };

  return (
    <Screen hideBottomNav scroll contentStyle={styles.screenContent}>
      <KeyboardAvoidingView style={isWeb ? styles.webKeyboardContainer : styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View
          style={[
            styles.page,
            isWide ? styles.pageWide : styles.pageNarrow,
          ]}
        >
          <View style={[styles.brandPanel, isWide ? styles.brandPanelWide : styles.brandPanelNarrow]}>
            <View style={styles.brandRow}>
              <View style={styles.logoBox}><Text style={styles.logoText}>P</Text></View>
              <Text style={styles.brandName}>Preço Bão</Text>
            </View>
            {isWide && <View style={styles.brandCopy}>
              <Text style={styles.brandHeading}>Seu medicamento, pelo melhor preço.</Text>
              <Text style={styles.brandDescription}>Pesquise preços e mantenha seus favoritos em um só lugar.</Text>
            </View>}
          </View>

          <View style={[styles.formPanel, isWide ? styles.formPanelWide : styles.formPanelNarrow]}>
            <View style={styles.headingBlock}>
              <Text style={styles.title}>Entrar na sua conta</Text>
              <Text style={styles.subtitle}>Acesse seus favoritos e acompanhe medicamentos.</Text>
            </View>

            <View style={styles.form}>
              <View style={styles.field}>
                <Text style={styles.label}>Email</Text>
                <Controller control={control} name="email" render={({ field: { onChange, onBlur, value } }) => (
                  <TextInput value={value} onChangeText={onChange} onBlur={() => { setFocused(null); onBlur(); }} onFocus={() => setFocused('email')}
                    placeholder="seu@email.com" placeholderTextColor={palette.muted} autoCapitalize="none" autoCorrect={false}
                    keyboardType="email-address" textContentType="emailAddress" accessibilityLabel="Email"
                    style={[styles.input, focused === 'email' && styles.inputFocused, errors.email && styles.inputError]} />
                )} />
                {errors.email && <Text style={styles.helperText}>{errors.email.message}</Text>}
              </View>
              <View style={styles.field}>
                <Text style={styles.label}>Senha</Text>
                <View style={[styles.passwordRow, focused === 'password' && styles.inputFocused, errors.password && styles.inputError]}>
                  <Controller control={control} name="password" render={({ field: { onChange, onBlur, value } }) => (
                    <TextInput value={value} onChangeText={onChange} onBlur={() => { setFocused(null); onBlur(); }} onFocus={() => setFocused('password')}
                      placeholder="Digite sua senha" placeholderTextColor={palette.muted} secureTextEntry={!showPassword} textContentType="password"
                      accessibilityLabel="Senha" style={styles.passwordInput} />
                  )} />
                  <Pressable onPress={() => setShowPassword((current) => !current)} style={styles.eyeButton} accessibilityRole="button" accessibilityLabel={showPassword ? 'Ocultar senha' : 'Mostrar senha'}>
                    <Ionicons name={showPassword ? 'eye-off-outline' : 'eye-outline'} size={20} color={palette.textSoft} />
                  </Pressable>
                </View>
                {errors.password && <Text style={styles.helperText}>{errors.password.message}</Text>}
              </View>
            </View>

            {feedback && <View style={[styles.feedbackBox, feedback.type === 'error' ? styles.feedbackError : styles.feedbackSuccess]} accessibilityRole="alert">
              <Text style={styles.feedbackText}>{feedback.text}</Text>
            </View>}

            <View style={styles.actions}>
              <Pressable style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, isSubmitting && styles.disabled]} onPress={handleSubmit(handleLogin)} disabled={isSubmitting} accessibilityRole="button">
                <Text style={styles.primaryButtonText}>{isSubmitting ? 'Entrando...' : 'Entrar'}</Text>
              </Pressable>
              <Pressable style={({ pressed }) => [styles.secondaryButton, pressed && styles.pressed]} onPress={() => router.push('/signup' as never)} accessibilityRole="button">
                <Text style={styles.secondaryButtonText}>Criar conta</Text>
              </Pressable>
              <Pressable style={styles.guestButton} onPress={onGuestPress} accessibilityRole="button">
                <Text style={styles.guestButtonText}>Continuar como convidado</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  webKeyboardContainer: { width: '100%' },
  screenContent: { flexGrow: 1, justifyContent: 'center', paddingVertical: spacing.xl },
  page: { width: '100%', maxWidth: 1060, alignSelf: 'center', gap: spacing.xl },
  pageNarrow: { maxWidth: 520, paddingHorizontal: spacing.lg, boxSizing: 'border-box' } as any,
  pageWide: { flexDirection: 'row', alignItems: 'stretch', gap: 0, minHeight: 570, backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.border, borderRadius: radius.xl, overflow: 'hidden' },
  brandPanel: { width: '100%' },
  brandPanelNarrow: { maxWidth: 500, alignSelf: 'center' },
  brandPanelWide: { width: '47%', padding: 44, backgroundColor: palette.surfaceBlue, justifyContent: 'space-between' },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  logoBox: { width: 42, height: 42, borderRadius: radius.md, backgroundColor: palette.primary, alignItems: 'center', justifyContent: 'center' },
  logoText: { color: palette.surface, fontSize: 24, fontWeight: '800' },
  brandName: { color: palette.text, fontSize: 19, fontWeight: '800' },
  brandCopy: { gap: spacing.lg, paddingBottom: 16 },
  brandHeading: { color: palette.text, fontSize: 34, lineHeight: 41, fontWeight: '800', maxWidth: 390 },
  brandDescription: { color: palette.textSoft, fontSize: 16, lineHeight: 24, maxWidth: 370 },
  formPanel: { flex: 1, width: 'auto', maxWidth: 500, alignSelf: 'stretch', justifyContent: 'center', gap: spacing.xl, paddingVertical: spacing.xl },
  formPanelNarrow: { width: '100%', paddingVertical: 0 },
  formPanelWide: { paddingHorizontal: 36 },
  headingBlock: { gap: spacing.xs },
  title: { color: palette.text, fontSize: 28, lineHeight: 35, fontWeight: '800' },
  subtitle: { color: palette.textSoft, fontSize: 14, lineHeight: 21 },
  form: { gap: spacing.lg },
  field: { gap: spacing.xs },
  label: { color: palette.text, fontSize: 14, fontWeight: '600' },
  input: { minHeight: 52, paddingHorizontal: spacing.lg, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface, color: palette.text, fontSize: 16, outlineStyle: 'none', boxSizing: 'border-box' } as any,
  passwordRow: { minHeight: 52, flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface },
  passwordInput: { flex: 1, minWidth: 0, height: 50, paddingHorizontal: spacing.lg, color: palette.text, fontSize: 16, outlineStyle: 'none' } as any,
  eyeButton: { width: 48, height: 48, justifyContent: 'center', alignItems: 'center' },
  inputFocused: { borderColor: palette.primary, borderWidth: 2 },
  inputError: { borderColor: palette.danger },
  helperText: { color: palette.danger, fontSize: 13 },
  feedbackBox: { padding: spacing.md, borderRadius: radius.md, borderWidth: 1 },
  feedbackError: { backgroundColor: palette.surface, borderColor: palette.danger },
  feedbackSuccess: { backgroundColor: palette.surface, borderColor: palette.success },
  feedbackText: { color: palette.text, fontSize: 14, lineHeight: 20 },
  actions: { gap: spacing.sm },
  primaryButton: { minHeight: 52, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, backgroundColor: palette.primary },
  primaryButtonText: { color: palette.surface, fontSize: 16, fontWeight: '700' },
  secondaryButton: { minHeight: 50, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, borderWidth: 1, borderColor: palette.primary },
  secondaryButtonText: { color: palette.primaryDark, fontSize: 15, fontWeight: '700' },
  guestButton: { minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  guestButtonText: { color: palette.textSoft, fontSize: 14, fontWeight: '600' },
  pressed: { opacity: 0.82 },
  disabled: { opacity: 0.55 },
});
