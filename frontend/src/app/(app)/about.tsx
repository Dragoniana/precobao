import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Screen } from '../../components/Screen';
import { palette, radius, spacing } from '../../theme';

const features = [
  'Compare ofertas disponíveis por farmácia.',
  'Pesquise por medicamento, princípio ativo ou laboratório.',
  'Salve favoritos vinculados à sua conta.',
];

export default function AboutScreen() {
  return (
    <Screen>
      <View style={styles.header}>
        <View style={styles.mark}><Ionicons name="medical" size={24} color={palette.surface} /></View>
        <View>
          <Text style={styles.eyebrow}>PREÇO BÃO</Text>
          <Text style={styles.title}>Saiba mais</Text>
        </View>
      </View>
      <Text style={styles.intro}>
        Encontre opções de preço para medicamentos e escolha onde comprar com mais informação.
      </Text>
      <View style={styles.list}>
        {features.map((feature, index) => (
          <View key={feature} style={styles.row}>
            <Text style={styles.number}>0{index + 1}</Text>
            <Text style={styles.feature}>{feature}</Text>
          </View>
        ))}
      </View>
      <Text style={styles.note}>
        Os preços e a disponibilidade são informados pelas farmácias participantes e podem mudar. Confirme as condições com a loja antes da compra.
      </Text>
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, marginTop: spacing.md },
  mark: { width: 48, height: 48, borderRadius: radius.md, backgroundColor: palette.primary, alignItems: 'center', justifyContent: 'center' },
  eyebrow: { color: palette.primaryDark, fontSize: 11, fontWeight: '800', letterSpacing: 1.2 },
  title: { color: palette.text, fontSize: 26, fontWeight: '800', marginTop: 2 },
  intro: { color: palette.textSoft, fontSize: 16, lineHeight: 24, marginTop: spacing.xl, maxWidth: 680 },
  list: { marginTop: spacing.lg, backgroundColor: palette.surface, borderRadius: radius.lg, paddingHorizontal: spacing.lg },
  row: { minHeight: 68, flexDirection: 'row', alignItems: 'center', gap: spacing.md, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: palette.border },
  number: { color: palette.primary, fontSize: 13, fontWeight: '800' },
  feature: { flex: 1, color: palette.text, fontSize: 15, lineHeight: 21 },
  note: { color: palette.textSoft, fontSize: 13, lineHeight: 19, marginTop: spacing.lg, maxWidth: 680 },
});
