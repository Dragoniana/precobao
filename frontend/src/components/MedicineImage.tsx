import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useState } from 'react';
import { Image, ImageStyle, StyleProp, StyleSheet, Text, View, ViewStyle } from 'react-native';

import { resolveMedicineImages } from '../services/catalog';
import { palette } from '../theme';
import { Medicamento } from '../types/api';

type MedicineImageProps = {
  medicine: Medicamento;
  style?: StyleProp<ViewStyle>;
  imageStyle?: StyleProp<ImageStyle>;
  label?: string;
};

export function MedicineImage({ medicine, style, imageStyle, label = 'Imagem indispon\u00edvel' }: MedicineImageProps) {
  const imageUrls = resolveMedicineImages(medicine);
  const imageKey = imageUrls.join('|');
  const [failedIndex, setFailedIndex] = useState(0);
  const visibleUrl = imageUrls[failedIndex];

  useEffect(() => setFailedIndex(0), [imageKey]);

  return (
    <View style={[styles.container, style]}>
      {visibleUrl ? (
        <Image
          accessibilityLabel={`Embalagem de ${medicine.nome}`}
          source={{ uri: visibleUrl }}
          resizeMode="contain"
          onError={() => setFailedIndex((index) => index + 1)}
          style={[styles.image, imageStyle]}
        />
      ) : (
        <View style={styles.placeholder}>
          <Ionicons name="medical-outline" size={23} color={palette.muted} />
          <Text style={styles.placeholderText}>{label}</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { overflow: 'hidden', alignItems: 'center', justifyContent: 'center' },
  image: { width: '100%', height: '100%' },
  placeholder: { flex: 1, width: '100%', minHeight: 0, alignItems: 'center', justifyContent: 'center', gap: 6, backgroundColor: palette.surfaceAlt },
  placeholderText: { color: palette.muted, fontSize: 11, lineHeight: 15, textAlign: 'center' },
});
