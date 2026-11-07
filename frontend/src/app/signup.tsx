import { zodResolver } from '@hookform/resolvers/zod';
import Ionicons from '@expo/vector-icons/Ionicons';
import { router } from 'expo-router';
import React, { useEffect, useRef, useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import {
  KeyboardAvoidingView,
  Keyboard,
  Platform,
  Pressable,
  Text,
  TextInput,
  ScrollView,
  StatusBar,
  useWindowDimensions,
  View,
} from 'react-native';
import { z } from 'zod';

import { Screen } from '../components/Screen';
import { useSessionContext } from '../context/AppContext';
import { radius, spacing } from '../theme';
import { createThemedStyles, useAppTheme } from '../context/ThemeContext';

const signupSchema = z
  .object({
    name: z.string().trim().min(2, 'O nome deve ter no mínimo 2 caracteres'),
    email: z.string().email('Digite um email válido'),
    password: z.string().min(8, 'A senha deve ter no mínimo 8 caracteres'),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: 'As senhas não coincidem',
    path: ['confirmPassword'],
  });

type SignupFormValues = z.infer<typeof signupSchema>;

type FocusedField = 'name' | 'email' | 'password' | 'confirmPassword' | null;

export default function SignupScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const { registerUser, signIn } = useSessionContext();

  const { width } = useWindowDimensions();

  const isWeb = Platform.OS === 'web';
  const isWide = isWeb && width >= 900;

  const {
    control,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<SignupFormValues>({
    resolver: zodResolver(signupSchema),
    defaultValues: {
      name: '',
      email: '',
      password: '',
      confirmPassword: '',
    },
  });

  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [focusedField, setFocusedField] = useState<FocusedField>(null);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const keyboardTopRef = useRef<number | null>(null);
  const scrollOffsetRef = useRef(0);
  const focusedInputRef = useRef<TextInput | null>(null);
  const [message, setMessage] = useState('');
  const [messageType, setMessageType] = useState<'error' | 'success'>('error');
  const formScrollRef = useRef<ScrollView>(null);
  const nameInputRef = useRef<TextInput>(null);
  const emailInputRef = useRef<TextInput>(null);
  const passwordInputRef = useRef<TextInput>(null);
  const confirmPasswordInputRef = useRef<TextInput>(null);

  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', (event) => {
      keyboardTopRef.current = event.endCoordinates.screenY;
      if (__DEV__) console.info(`[keyboard-layout] keyboard screenY=${event.endCoordinates.screenY} height=${event.endCoordinates.height}`);
      setKeyboardVisible(true);
    });
    const hide = Keyboard.addListener('keyboardDidHide', () => {
      keyboardTopRef.current = null;
      setKeyboardVisible(false);
    });
    return () => { show.remove(); hide.remove(); };
  }, []);

  const keepInputVisible = (getInput: () => TextInput | null) => {
    if (Platform.OS === 'web') return;
    const attemptScroll = () => {
      const input = getInput();
      focusedInputRef.current = input;
      const keyboardTop = keyboardTopRef.current;
      if (__DEV__) console.info(`[keyboard-layout] focus target=${Boolean(input)} keyboard=${keyboardTop !== null}`);
      if (!input || keyboardTop === null) return;
      input.measureInWindow((_x, y, _width, height) => {
        const overlap = y + height + (StatusBar.currentHeight ?? 0) + spacing.md - keyboardTop;
        if (__DEV__) console.info(`[keyboard-layout] inputBottom=${y + height} keyboardTop=${keyboardTop} overlap=${overlap}`);
        if (overlap > 0) {
          formScrollRef.current?.scrollTo({ y: scrollOffsetRef.current + overlap, animated: true });
        }
      });
    };
    setTimeout(attemptScroll, 120);
    setTimeout(attemptScroll, 280);
  };

  const onSubmit = async (data: SignupFormValues) => {
    setMessage('');
    setMessageType('error');

    const result = await registerUser({
      name: data.name,
      email: data.email,
      password: data.password,
      confirmPassword: data.confirmPassword,
    });

    if (!result.ok) {
      setMessageType('error');
      setMessage(result.message);

      setError('root', {
        message: result.message,
      });

      return;
    }

    setMessageType('success');
    setMessage('Cadastro realizado. Entrando na sua conta...');

    const loginResult = await signIn(data.email, data.password);

    if (!loginResult.ok) {
      const errorMessage =
        'Cadastro realizado, mas não foi possível entrar automaticamente. Faça login manualmente.';

      setMessageType('error');
      setMessage(errorMessage);

      setError('root', {
        message: errorMessage,
      });

      return;
    }

    router.replace('/(app)/home' as never);
  };

  return (
    <Screen hideBottomNav scroll={false} contentStyle={styles.flex}>
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : Platform.OS === 'android' ? 'height' : undefined}>
        <ScrollView ref={formScrollRef} style={styles.flex} contentContainerStyle={[styles.screenContent, keyboardVisible && styles.screenContentKeyboard]} keyboardShouldPersistTaps="handled" keyboardDismissMode="none" showsVerticalScrollIndicator={false} scrollEventThrottle={16} onScroll={(event) => { scrollOffsetRef.current = event.nativeEvent.contentOffset.y; }}>
        <View style={[styles.page, isWide && styles.pageWide]}>
          <View style={[styles.brandPanel, isWide ? styles.brandPanelWide : styles.brandPanelNarrow]}>
            <View style={styles.brandTop}>
            <Pressable style={styles.backLink} onPress={() => router.replace('/login' as never)} accessibilityRole="button" accessibilityLabel="Voltar para entrar">
              <Text style={styles.backText}>Voltar para entrar</Text>
            </Pressable>
            <View style={styles.brandRow}><View style={styles.logoBox}><Text style={styles.logoText}>P</Text></View><Text style={styles.brandName}>Preço Bão</Text></View>
            </View>
            {isWide && <View style={styles.brandCopy}><Text style={styles.brandHeading}>Seus favoritos, sempre com você.</Text><Text style={styles.brandDescription}>Crie sua conta para guardar medicamentos e comparar preços quando precisar.</Text></View>}
          </View>
          <View style={[styles.formPanel, isWide && styles.formPanelWide]}>
            <View style={styles.headingBlock}><Text style={styles.title}>Criar conta</Text><Text style={styles.subtitle}>Preencha seus dados para começar.</Text></View>
            <View style={styles.form}>
              <View style={styles.field}>
                <Text style={styles.label}>Nome</Text>
                <Controller control={control} name="name" render={({ field: { onChange, onBlur, value, ref } }) => (
                  <TextInput ref={(node) => { ref(node); nameInputRef.current = node; }} value={value} onChangeText={onChange} onBlur={() => { setFocusedField(null); onBlur(); }} onFocus={() => { setFocusedField('name'); keepInputVisible(() => nameInputRef.current); }}
                    placeholder="Seu nome" placeholderTextColor={palette.muted} autoCapitalize="words" textContentType="name" accessibilityLabel="Nome"
                    returnKeyType="next" blurOnSubmit={false} onSubmitEditing={() => emailInputRef.current?.focus()}
                    style={[styles.input, focusedField === 'name' && styles.inputFocused, errors.name && styles.inputError]} />
                )} />
                {errors.name && <Text style={styles.helperText}>{errors.name.message}</Text>}
              </View>
              <View style={styles.field}>
                <Text style={styles.label}>Email</Text>
                <Controller control={control} name="email" render={({ field: { onChange, onBlur, value, ref } }) => (
                  <TextInput ref={(node) => { ref(node); emailInputRef.current = node; }} value={value} onChangeText={onChange} onBlur={() => { setFocusedField(null); onBlur(); }} onFocus={() => { setFocusedField('email'); keepInputVisible(() => emailInputRef.current); }}
                    placeholder="seu@email.com" placeholderTextColor={palette.muted} autoCapitalize="none" autoCorrect={false} keyboardType="email-address"
                    textContentType="emailAddress" accessibilityLabel="Email" returnKeyType="next" blurOnSubmit={false} onSubmitEditing={() => passwordInputRef.current?.focus()}
                    style={[styles.input, focusedField === 'email' && styles.inputFocused, errors.email && styles.inputError]} />
                )} />
                {errors.email && <Text style={styles.helperText}>{errors.email.message}</Text>}
              </View>
              <View style={styles.field}>
                <Text style={styles.label}>Senha</Text>
                <View style={[styles.passwordRow, focusedField === 'password' && styles.inputFocused, errors.password && styles.inputError]}>
                  <Controller control={control} name="password" render={({ field: { onChange, onBlur, value, ref } }) => (
                    <TextInput ref={(node) => { ref(node); passwordInputRef.current = node; }} value={value} onChangeText={onChange} onBlur={() => { setFocusedField(null); onBlur(); }} onFocus={() => { setFocusedField('password'); keepInputVisible(() => passwordInputRef.current); }}
                      placeholder="No mínimo 8 caracteres" placeholderTextColor={palette.muted} secureTextEntry={!showPassword} textContentType="newPassword"
                      accessibilityLabel="Senha" returnKeyType="next" blurOnSubmit={false} onSubmitEditing={() => confirmPasswordInputRef.current?.focus()} style={styles.passwordInput} />
                  )} />
                  <Pressable style={styles.eyeButton} onPress={() => setShowPassword((current) => !current)} accessibilityRole="button" accessibilityLabel={showPassword ? 'Ocultar senha' : 'Mostrar senha'}>
                    <Ionicons name={showPassword ? 'eye-off-outline' : 'eye-outline'} size={20} color={palette.textSoft} />
                  </Pressable>
                </View>
                {errors.password && <Text style={styles.helperText}>{errors.password.message}</Text>}
              </View>
              <View style={styles.field}>
                <Text style={styles.label}>Confirmar senha</Text>
                <View style={[styles.passwordRow, focusedField === 'confirmPassword' && styles.inputFocused, errors.confirmPassword && styles.inputError]}>
                  <Controller control={control} name="confirmPassword" render={({ field: { onChange, onBlur, value, ref } }) => (
                    <TextInput ref={(node) => { ref(node); confirmPasswordInputRef.current = node; }} value={value} onChangeText={onChange} onBlur={() => { setFocusedField(null); onBlur(); }} onFocus={() => { setFocusedField('confirmPassword'); keepInputVisible(() => confirmPasswordInputRef.current); }}
                      placeholder="Repita a senha" placeholderTextColor={palette.muted} secureTextEntry={!showConfirmPassword} textContentType="newPassword"
                      accessibilityLabel="Confirmar senha" returnKeyType="done" onSubmitEditing={() => Keyboard.dismiss()} style={styles.passwordInput} />
                  )} />
                  <Pressable style={styles.eyeButton} onPress={() => setShowConfirmPassword((current) => !current)} accessibilityRole="button" accessibilityLabel={showConfirmPassword ? 'Ocultar confirmação de senha' : 'Mostrar confirmação de senha'}>
                    <Ionicons name={showConfirmPassword ? 'eye-off-outline' : 'eye-outline'} size={20} color={palette.textSoft} />
                  </Pressable>
                </View>
                {errors.confirmPassword && <Text style={styles.helperText}>{errors.confirmPassword.message}</Text>}
              </View>
            </View>
            {message ? <View style={[styles.messageBox, messageType === 'error' ? styles.messageError : styles.messageSuccess]} accessibilityRole="alert"><Text style={styles.messageText}>{message}</Text></View> : null}
            <View style={styles.actions}>
              <Pressable style={({ pressed }) => [styles.primaryButton, isSubmitting && styles.disabled, pressed && styles.pressed]} disabled={isSubmitting} onPress={handleSubmit(onSubmit)} accessibilityRole="button"><Text style={styles.primaryButtonText}>{isSubmitting ? 'Cadastrando...' : 'Cadastrar'}</Text></Pressable>
              <Pressable style={styles.loginButton} onPress={() => router.replace('/login' as never)} accessibilityRole="button"><Text style={styles.loginButtonText}>Já tenho conta</Text></Pressable>
            </View>
          </View>
        </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

const useStyles = createThemedStyles((palette) => ({
  flex: { flex: 1 },
  screenContent: { width: '100%', flexGrow: 1, alignItems: 'stretch', justifyContent: 'center', paddingVertical: spacing.xl, boxSizing: 'border-box' } as any,
  screenContentKeyboard: { justifyContent: 'flex-start', paddingTop: spacing.md, paddingBottom: 80 },
  page: { width: '100%', minWidth: 0, maxWidth: 1060, alignSelf: 'center', gap: spacing.xl, paddingHorizontal: spacing.lg, boxSizing: 'border-box' } as any,
  pageWide: { flexDirection: 'row', alignItems: 'stretch', gap: 0, minHeight: 660, backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.border, borderRadius: radius.xl, overflow: 'hidden' },
  brandPanel: { width: '100%', gap: spacing.lg },
  brandPanelNarrow: { maxWidth: 500, alignSelf: 'center' },
  brandPanelWide: { width: '47%', padding: 44, backgroundColor: palette.surfaceBlue, justifyContent: 'space-between' },
  brandTop: { gap: spacing.md },
  backLink: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, minHeight: 44, alignSelf: 'flex-start' },
  backText: { color: palette.primaryDark, fontSize: 14, fontWeight: '600' },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  logoBox: { width: 42, height: 42, borderRadius: radius.md, backgroundColor: palette.primary, alignItems: 'center', justifyContent: 'center' },
  logoText: { color: palette.onPrimary, fontSize: 24, fontWeight: '800' },
  brandName: { color: palette.text, fontSize: 19, fontWeight: '800' },
  brandCopy: { gap: spacing.lg, paddingBottom: 16 },
  brandHeading: { color: palette.text, fontSize: 34, lineHeight: 41, fontWeight: '800', maxWidth: 390 },
  brandDescription: { color: palette.textSoft, fontSize: 16, lineHeight: 24, maxWidth: 370 },
  formPanel: { flex: 1, width: '100%', maxWidth: 500, minWidth: 0, alignSelf: 'center', justifyContent: 'center', gap: spacing.lg, paddingVertical: spacing.xl },
  formPanelWide: { paddingHorizontal: 36 },
  headingBlock: { gap: spacing.xs },
  title: { color: palette.text, fontSize: 28, lineHeight: 35, fontWeight: '800' },
  subtitle: { color: palette.textSoft, fontSize: 14, lineHeight: 21 },
  form: { gap: spacing.md },
  field: { gap: spacing.xs },
  label: { color: palette.text, fontSize: 14, fontWeight: '600' },
  input: { width: '100%', minWidth: 0, minHeight: 52, paddingHorizontal: spacing.lg, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface, color: palette.text, fontSize: 16, outlineStyle: 'none', boxSizing: 'border-box' } as any,
  passwordRow: { width: '100%', minWidth: 0, minHeight: 52, flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface },
  passwordInput: { flex: 1, minWidth: 0, height: 50, paddingHorizontal: spacing.lg, color: palette.text, fontSize: 16, outlineStyle: 'none' } as any,
  eyeButton: { width: 48, height: 48, justifyContent: 'center', alignItems: 'center' },
  inputFocused: { borderColor: palette.primary, borderWidth: 2 },
  inputError: { borderColor: palette.danger },
  helperText: { color: palette.danger, fontSize: 13 },
  messageBox: { padding: spacing.md, borderRadius: radius.md, borderWidth: 1 },
  messageError: { backgroundColor: palette.surface, borderColor: palette.danger },
  messageSuccess: { backgroundColor: palette.surface, borderColor: palette.success },
  messageText: { color: palette.text, fontSize: 14, lineHeight: 20 },
  actions: { gap: spacing.sm },
  primaryButton: { width: '100%', minHeight: 52, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, backgroundColor: palette.primary },
  primaryButtonText: { color: palette.onPrimary, fontSize: 16, fontWeight: '700' },
  loginButton: { width: '100%', minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  loginButtonText: { color: palette.primaryDark, fontSize: 14, fontWeight: '700' },
  pressed: { opacity: 0.82 },
  disabled: { opacity: 0.55 },
}));
