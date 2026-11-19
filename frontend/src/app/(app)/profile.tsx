import Ionicons from '@expo/vector-icons/Ionicons';
import { router } from 'expo-router';
import React, { useEffect, useState } from 'react';

import {
  Modal,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';

import { Screen } from '../../components/Screen';
import { useFavoritesContext, useSessionContext } from '../../context/AppContext';
import { radius, spacing } from '../../theme';
import { createThemedStyles, useAppTheme } from '../../context/ThemeContext';

export default function ProfileScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const { sessionMode, currentUserName, currentUserEmail, logout, updateProfile } = useSessionContext();
  const { favoriteIds } = useFavoritesContext();

  const { width } = useWindowDimensions();

  const isWeb = Platform.OS === 'web';
  const isDesktop = isWeb && width >= 980;

  const isAuthenticated = sessionMode === 'authenticated';

  const displayName = isAuthenticated
    ? currentUserName || 'Usuário autenticado'
    : 'Convidado';

  const displayEmail = isAuthenticated
    ? currentUserEmail || 'Email não informado'
    : 'Entre ou crie uma conta para salvar seus dados';

  const initials = displayName
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join('');

  const [editModalVisible, setEditModalVisible] = useState(false);
  const [editName, setEditName] = useState(displayName);
  const [editEmail, setEditEmail] = useState(
    isAuthenticated ? currentUserEmail || '' : ''
  );
  const [editMessage, setEditMessage] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);

  useEffect(() => {
    setEditName(isAuthenticated ? currentUserName || '' : '');
    setEditEmail(isAuthenticated ? currentUserEmail || '' : '');
  }, [isAuthenticated, currentUserName, currentUserEmail]);

  const handleLogout = () => {
    logout();
    router.replace('/login');
  };

  const openEditModal = () => {
    setEditName(currentUserName || '');
    setEditEmail(currentUserEmail || '');
    setEditMessage('');
    setEditModalVisible(true);
  };

  const closeEditModal = () => {
    setEditModalVisible(false);
    setEditMessage('');
  };

  const handleSaveProfile = async () => {
    if (!editName.trim()) {
      setEditMessage('Informe o nome.');
      return;
    }

    if (!editEmail.trim()) {
      setEditMessage('Informe o email.');
      return;
    }

    if (!editEmail.includes('@')) {
      setEditMessage('Informe um email válido.');
      return;
    }

    setSavingProfile(true);
    const result = await updateProfile(editName, editEmail);
    setSavingProfile(false);
    if (result.ok) {
      setEditModalVisible(false);
      setEditMessage('');
    } else {
      setEditMessage(result.message);
    }
  };

  return (
    <>
      <Screen>
        <View style={[styles.page, isDesktop && styles.pageDesktop]}>
          <View style={styles.pageHeading}>
            <Text style={styles.eyebrow}>MINHA CONTA</Text>
            <Text style={styles.pageTitle}>Perfil</Text>
          </View>

          {isAuthenticated ? (
            <>
              <View style={styles.accountSection}>
                <View style={styles.identityRow}>
                  <View style={styles.avatar}><Text style={styles.avatarText}>{initials || 'P'}</Text></View>
                  <View style={styles.identityText}>
                    <Text style={styles.userName} numberOfLines={2}>{displayName}</Text>
                    <Text style={styles.userEmail} numberOfLines={1}>{displayEmail}</Text>
                  </View>
                </View>
                <View style={styles.sectionDivider} />
                <View style={styles.accountFooter}>
                  <Text style={styles.sectionHint}>Dados pessoais</Text>
                  <Pressable style={styles.editButton} onPress={openEditModal} accessibilityRole="button" accessibilityLabel="Editar dados pessoais">
                    <Text style={styles.editButtonText}>Editar dados</Text><Ionicons name="arrow-forward" size={16} color={palette.primaryDark} />
                  </Pressable>
                </View>
              </View>

              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Sua atividade</Text>
                <Pressable style={styles.linkRow} onPress={() => router.push('/favorites' as never)} accessibilityRole="button" accessibilityLabel={`Abrir favoritos, ${favoriteIds.length} salvos`}>
                  <View style={styles.linkText}><Text style={styles.linkTitle}>Favoritos</Text><Text style={styles.linkSubtitle}>Medicamentos salvos na sua conta</Text></View>
                  <Text style={styles.countText}>{favoriteIds.length}</Text><Ionicons name="chevron-forward" size={18} color={palette.textSoft} />
                </Pressable>
              </View>

              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Conta</Text>
                <Pressable style={styles.logoutRow} onPress={handleLogout} accessibilityRole="button"><Text style={styles.logoutText}>Sair da conta</Text><Ionicons name="log-out-outline" size={18} color={palette.danger} /></Pressable>
              </View>
            </>
          ) : (
            <View style={styles.guestSection}>
              <Text style={styles.guestTitle}>Você está navegando como convidado</Text>
              <Text style={styles.guestDescription}>Entre para salvar medicamentos favoritos e acessar seus dados em qualquer dispositivo.</Text>
              <Pressable style={styles.primaryButton} onPress={() => router.push('/login' as never)} accessibilityRole="button"><Text style={styles.primaryButtonText}>Entrar</Text></Pressable>
              <Pressable style={styles.secondaryButton} onPress={() => router.push('/signup' as never)} accessibilityRole="button"><Text style={styles.secondaryButtonText}>Criar conta</Text></Pressable>
            </View>
          )}
        </View>
      </Screen>

      <Modal transparent animationType="fade" visible={editModalVisible} onRequestClose={closeEditModal}>
        <View style={styles.modalOverlay}>
          <Pressable style={styles.modalBackdrop} onPress={closeEditModal} accessibilityLabel="Fechar edição" />
          <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.modalKeyboard}>
            <ScrollView style={styles.modalScroll} contentContainerStyle={styles.modalScrollContent} keyboardShouldPersistTaps="handled">
              <View style={styles.editModal}>
                <View style={styles.modalHeader}>
                  <View style={styles.modalHeading}><Text style={styles.modalTitle}>Editar dados</Text><Text style={styles.modalSubtitle}>Atualize as informações da sua conta.</Text></View>
                  <Pressable style={styles.modalCloseButton} onPress={closeEditModal} accessibilityRole="button" accessibilityLabel="Fechar"><Ionicons name="close" size={22} color={palette.text} /></Pressable>
                </View>
                <View style={styles.modalForm}>
                  <View style={styles.inputGroup}><Text style={styles.inputLabel}>Nome</Text><TextInput value={editName} onChangeText={setEditName} placeholder="Seu nome" placeholderTextColor={palette.muted} autoCapitalize="words" textContentType="name" accessibilityLabel="Nome" style={styles.input} /></View>
                  <View style={styles.inputGroup}><Text style={styles.inputLabel}>Email</Text><TextInput value={editEmail} onChangeText={setEditEmail} placeholder="seu@email.com" placeholderTextColor={palette.muted} autoCapitalize="none" autoCorrect={false} keyboardType="email-address" textContentType="emailAddress" accessibilityLabel="Email" style={styles.input} /></View>
                </View>
                {editMessage ? <Text style={styles.editMessage} accessibilityRole="alert">{editMessage}</Text> : null}
                <View style={styles.modalActions}>
                  <Pressable style={styles.cancelButton} onPress={closeEditModal} accessibilityRole="button"><Text style={styles.cancelButtonText}>Cancelar</Text></Pressable>
                  <Pressable style={[styles.saveButton, savingProfile && styles.disabled]} onPress={handleSaveProfile} disabled={savingProfile} accessibilityRole="button"><Text style={styles.saveButtonText}>{savingProfile ? 'Salvando...' : 'Salvar alterações'}</Text></Pressable>
                </View>
              </View>
            </ScrollView>
          </KeyboardAvoidingView>
        </View>
      </Modal>
    </>
  );
}

const useStyles = createThemedStyles((palette) => ({
  page: { width: '100%', maxWidth: 720, alignSelf: 'center', gap: spacing.xl, paddingBottom: spacing.xl },
  pageDesktop: { paddingTop: spacing.xl },
  pageHeading: { gap: 4 },
  eyebrow: { color: palette.primaryDark, fontSize: 11, letterSpacing: 1.2, fontWeight: '700' },
  pageTitle: { color: palette.text, fontSize: 30, lineHeight: 37, fontWeight: '800' },
  accountSection: { backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.border, borderRadius: radius.lg, padding: spacing.lg },
  identityRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  avatar: { width: 56, height: 56, borderRadius: radius.pill, backgroundColor: palette.primarySoft, justifyContent: 'center', alignItems: 'center' },
  avatarText: { color: palette.primaryDark, fontSize: 20, fontWeight: '800' },
  identityText: { flex: 1, minWidth: 0, gap: 2 },
  userName: { color: palette.text, fontSize: 20, lineHeight: 26, fontWeight: '700' },
  userEmail: { color: palette.textSoft, fontSize: 14, lineHeight: 20 },
  sectionDivider: { height: 1, backgroundColor: palette.border, marginVertical: spacing.lg },
  accountFooter: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  sectionHint: { color: palette.textSoft, fontSize: 13 },
  editButton: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingHorizontal: spacing.sm },
  editButtonText: { color: palette.primaryDark, fontSize: 14, fontWeight: '700' },
  section: { gap: spacing.sm },
  sectionTitle: { color: palette.text, fontSize: 16, fontWeight: '700' },
  linkRow: { flexDirection: 'row', alignItems: 'center', minHeight: 72, gap: spacing.md, backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, paddingHorizontal: spacing.lg },
  linkText: { flex: 1, minWidth: 0, gap: 2 },
  linkTitle: { color: palette.text, fontSize: 15, fontWeight: '600' },
  linkSubtitle: { color: palette.textSoft, fontSize: 13 },
  countText: { color: palette.primaryDark, fontSize: 15, fontWeight: '700' },
  logoutRow: { minHeight: 56, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: spacing.lg, backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md },
  logoutText: { color: palette.danger, fontSize: 14, fontWeight: '600' },
  guestSection: { backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.border, borderRadius: radius.lg, padding: spacing.xl, gap: spacing.md },
  guestTitle: { color: palette.text, fontSize: 20, lineHeight: 27, fontWeight: '700' },
  guestDescription: { color: palette.textSoft, fontSize: 14, lineHeight: 21, marginBottom: spacing.sm },
  primaryButton: { minHeight: 50, backgroundColor: palette.primary, borderRadius: radius.md, justifyContent: 'center', alignItems: 'center' },
  primaryButtonText: { color: palette.onPrimary, fontSize: 15, fontWeight: '700' },
  secondaryButton: { minHeight: 50, borderWidth: 1, borderColor: palette.primary, borderRadius: radius.md, justifyContent: 'center', alignItems: 'center' },
  secondaryButtonText: { color: palette.primaryDark, fontSize: 15, fontWeight: '700' },
  modalOverlay: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  modalBackdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: palette.overlay },
  modalKeyboard: { width: '100%', maxWidth: 520, maxHeight: '100%' },
  modalScroll: { width: '100%' },
  modalScrollContent: { flexGrow: 1, justifyContent: 'center', padding: spacing.lg },
  editModal: { width: '100%', backgroundColor: palette.surface, borderRadius: radius.lg, padding: spacing.xl, gap: spacing.xl },
  modalHeader: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  modalHeading: { flex: 1, minWidth: 0, gap: 4 },
  modalTitle: { color: palette.text, fontSize: 22, fontWeight: '800' },
  modalSubtitle: { color: palette.textSoft, fontSize: 14, lineHeight: 20 },
  modalCloseButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center', marginTop: -8, marginRight: -8 },
  modalForm: { gap: spacing.lg },
  inputGroup: { gap: spacing.xs },
  inputLabel: { color: palette.text, fontSize: 14, fontWeight: '600' },
  input: { minHeight: 52, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, paddingHorizontal: spacing.lg, color: palette.text, fontSize: 16, backgroundColor: palette.surface },
  editMessage: { color: palette.danger, fontSize: 13, lineHeight: 19 },
  modalActions: { flexDirection: 'row', gap: spacing.sm },
  cancelButton: { minHeight: 50, flex: 1, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: palette.border, borderRadius: radius.md },
  cancelButtonText: { color: palette.text, fontSize: 14, fontWeight: '600' },
  saveButton: { minHeight: 50, flex: 1.5, alignItems: 'center', justifyContent: 'center', backgroundColor: palette.primary, borderRadius: radius.md },
  saveButtonText: { color: palette.onPrimary, fontSize: 14, fontWeight: '700' },
  disabled: { opacity: 0.55 },
}));
