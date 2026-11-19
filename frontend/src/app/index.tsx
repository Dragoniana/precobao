import { useRouter } from 'expo-router';
import { useEffect } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';

import { useAppTheme } from '../context/ThemeContext';

export default function Index() {
  const router = useRouter();
  const { palette } = useAppTheme();

  useEffect(() => {
    router.replace('/login');
  }, [router]);

  return (
    <View
      accessibilityRole="progressbar"
      accessibilityLabel="Carregando Preço Bão"
      style={{
        flex: 1,
        minHeight: '100%',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 16,
        backgroundColor: palette.background,
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <View style={{
          width: 44,
          height: 44,
          borderRadius: 12,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: palette.primary,
        }}>
          <Text style={{ color: '#fff', fontSize: 24, fontWeight: '800' }}>P</Text>
        </View>
        <Text style={{ color: palette.text, fontSize: 20, fontWeight: '700' }}>Preço Bão</Text>
      </View>
      <ActivityIndicator color={palette.primary} />
    </View>
  );
}
