import Ionicons from '@expo/vector-icons/Ionicons';
import { router, useLocalSearchParams } from 'expo-router';
import React, { useEffect, useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, Text, TextInput, View } from 'react-native';

import { Screen } from '../components/Screen';
import { api } from '../services/api';
import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';

export default function ResetPasswordScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const params = useLocalSearchParams<{ token?: string | string[] }>();
  const routeToken = Array.isArray(params.token) ? params.token[0] : params.token;
  const [token, setToken] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [completed, setCompleted] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!routeToken) return;
    setToken(routeToken);
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      window.history.replaceState(window.history.state, '', window.location.pathname);
    }
  }, [routeToken]);

  const savePassword = async () => {
    setMessage('');
    if (!token) { setMessage('Este link não é válido. Solicite um novo link.'); return; }
    if (password.length < 8) { setMessage('A nova senha deve ter pelo menos 8 caracteres.'); return; }
    if (password !== confirmation) { setMessage('As senhas não coincidem.'); return; }
    setIsSubmitting(true);
    try {
      await api.post('/auth/password-reset/confirm', {
        token,
        new_password: password,
        confirm_new_password: confirmation,
      });
      setToken('');
      setPassword('');
      setConfirmation('');
      setCompleted(true);
    } catch (error: any) {
      setMessage(error.response?.status === 429
        ? 'Muitas tentativas. Aguarde antes de tentar novamente.'
        : 'Este link não é válido ou expirou. Solicite um novo link.');
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
              <Text style={styles.title}>Criar nova senha</Text>
              <Text style={styles.subtitle}>Escolha uma senha segura para sua conta.</Text>
            </View>
            {completed ? (
              <>
                <Text style={styles.successMessage} accessibilityRole="alert">Senha alterada. Entre com sua nova senha.</Text>
                <Pressable style={styles.primaryButton} onPress={() => router.replace('/login' as never)} accessibilityRole="button">
                  <Text style={styles.primaryButtonText}>Ir para o login</Text>
                </Pressable>
              </>
            ) : (
              <>
                <View style={styles.field}>
                  <Text style={styles.label}>Nova senha</Text>
                  <View style={styles.passwordRow}>
                    <TextInput testID="reset-new-password" value={password} onChangeText={setPassword}
                      placeholder="Pelo menos 8 caracteres" placeholderTextColor={palette.muted}
                      secureTextEntry={!showPassword} textContentType="newPassword" autoComplete="new-password"
                      accessibilityLabel="Nova senha" style={styles.passwordInput} />
                    <Pressable onPress={() => setShowPassword((value) => !value)} accessibilityRole="button"
                      accessibilityLabel={showPassword ? 'Ocultar senha' : 'Mostrar senha'} style={styles.eyeButton}>
                      <Ionicons name={showPassword ? 'eye-off-outline' : 'eye-outline'} size={20} color={palette.textSoft} />
                    </Pressable>
                  </View>
                </View>
                <View style={styles.field}>
                  <Text style={styles.label}>Confirmar nova senha</Text>
                  <View style={styles.passwordRow}>
                    <TextInput testID="reset-confirm-password" value={confirmation} onChangeText={setConfirmation}
                      placeholder="Digite a nova senha novamente" placeholderTextColor={palette.muted}
                      secureTextEntry={!showConfirmation} textContentType="newPassword" autoComplete="new-password"
                      accessibilityLabel="Confirmar nova senha" style={styles.passwordInput} />
                    <Pressable onPress={() => setShowConfirmation((value) => !value)} accessibilityRole="button"
                      accessibilityLabel={showConfirmation ? 'Ocultar confirmação' : 'Mostrar confirmação'} style={styles.eyeButton}>
                      <Ionicons name={showConfirmation ? 'eye-off-outline' : 'eye-outline'} size={20} color={palette.textSoft} />
                    </Pressable>
                  </View>
                </View>
                {message ? <Text style={styles.errorMessage} accessibilityRole="alert">{message}</Text> : null}
                <Pressable testID="save-reset-password" style={[styles.primaryButton, isSubmitting && styles.disabled]}
                  onPress={savePassword} disabled={isSubmitting} accessibilityRole="button">
                  <Text style={styles.primaryButtonText}>{isSubmitting ? 'Salvando...' : 'Salvar nova senha'}</Text>
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
  scrollContent: { flexGrow: 1, width: '100%', justifyContent: 'center', alignItems: 'center', padding: spacing.lg },
  card: { width: '100%', maxWidth: 520, padding: spacing.xl, gap: spacing.lg, backgroundColor: palette.surface, borderColor: palette.border, borderWidth: 1, borderRadius: radius.lg },
  backLink: { alignSelf: 'flex-start', minHeight: 44, justifyContent: 'center' },
  backText: { color: palette.primaryDark, fontSize: 14, fontWeight: '600' },
  heading: { gap: spacing.xs },
  title: { color: palette.text, fontSize: 28, lineHeight: 35, fontWeight: '800' },
  subtitle: { color: palette.textSoft, fontSize: 14, lineHeight: 21 },
  field: { gap: spacing.xs },
  label: { color: palette.text, fontSize: 14, fontWeight: '600' },
  passwordRow: { minHeight: 52, flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface },
  passwordInput: { minWidth: 0, flex: 1, minHeight: 50, paddingHorizontal: spacing.lg, color: palette.text, fontSize: 16 },
  eyeButton: { width: 48, minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  primaryButton: { width: '100%', minHeight: 50, alignItems: 'center', justifyContent: 'center', backgroundColor: palette.primary, borderRadius: radius.md },
  primaryButtonText: { color: palette.onPrimary, fontSize: 15, fontWeight: '700' },
  disabled: { opacity: 0.6 },
  errorMessage: { color: palette.danger, fontSize: 14, lineHeight: 20 },
  successMessage: { color: palette.success, fontSize: 14, lineHeight: 20 },
}));
