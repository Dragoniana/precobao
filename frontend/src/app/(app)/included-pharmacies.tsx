import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';

import { Screen } from '../../components/Screen';
import { api } from '../../services/api';
import { radius, spacing } from '../../theme';
import { createThemedStyles, useAppTheme } from '../../context/ThemeContext';

type Pharmacy = { id: number; nome_fantasia: string; endereco_completo: string };

export default function IncludedPharmaciesScreen() {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const [pharmacies, setPharmacies] = useState<Pharmacy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    api.get<Pharmacy[]>('/catalogo/farmacias')
      .then(({ data }) => { if (active) setPharmacies(data); })
      .catch(() => { if (active) setError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  return (
    <Screen>
      <Text style={styles.title}>Farmácias inclusas</Text>
      {loading ? <ActivityIndicator color={palette.primary} /> : error ? (
        <Text style={styles.message}>Não foi possível carregar as farmácias.</Text>
      ) : pharmacies.length === 0 ? (
        <Text style={styles.message}>Nenhuma farmácia cadastrada.</Text>
      ) : (
        <View style={styles.card}>
          {pharmacies.map((pharmacy) => (
            <View key={pharmacy.id} style={styles.row}>
              <Text style={styles.name}>{pharmacy.nome_fantasia}</Text>
              {pharmacy.endereco_completo &&
              !/extração|scraper|api rest/i.test(pharmacy.endereco_completo) ? (
                <Text style={styles.address}>{pharmacy.endereco_completo}</Text>
              ) : null}
            </View>
          ))}
        </View>
      )}
    </Screen>
  );
}

const useStyles = createThemedStyles((palette) => ({
  title: {
    fontSize: 28,
    color: palette.text,
    fontWeight: '700',
    marginTop: spacing.md,
    marginBottom: spacing.lg,
  },
  card: {
    backgroundColor: palette.surface,
    borderRadius: radius.lg,
    paddingHorizontal: spacing.lg,
  },
  row: {
    paddingVertical: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
  },
  name: {
    color: palette.text,
    fontSize: 16,
    fontWeight: '600',
  },
  address: {
    color: palette.textSoft,
    fontSize: 13,
    marginTop: spacing.xs,
  },
  message: {
    color: palette.textSoft,
    fontSize: 15,
  },
}));
