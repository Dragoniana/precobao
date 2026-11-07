import Ionicons from '@expo/vector-icons/Ionicons';
import React, { useEffect, useState } from 'react';
import { Image, ImageStyle, StyleProp, Text, View, ViewStyle } from 'react-native';

import { resolveMedicineImages } from '../services/catalog';

import { createThemedStyles, useAppTheme } from '../context/ThemeContext';
import { Medicamento } from '../types/api';

type MedicineImageProps = {
  medicine: Medicamento;
  style?: StyleProp<ViewStyle>;
  imageStyle?: StyleProp<ImageStyle>;
  label?: string;
  onLoad?: () => void;
};

export function MedicineImage({ medicine, style, imageStyle, label = 'Imagem indispon\u00edvel', onLoad }: MedicineImageProps) {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const imageUrls = resolveMedicineImages(medicine);
  const imageKey = imageUrls.join('|');
  const [failedIndex, setFailedIndex] = useState(0);
  const [imageLoaded, setImageLoaded] = useState(false);
  const visibleUrl = imageUrls[failedIndex];

  useEffect(() => {
    setFailedIndex(0);
    setImageLoaded(false);
  }, [imageKey]);

  return (
    <View testID="medicine-image-frame" style={[styles.container, style]}>
      {visibleUrl ? (
        <Image
          key={visibleUrl}
          testID="medicine-image"
          accessibilityLabel={`Embalagem de ${medicine.nome}`}
          source={{ uri: visibleUrl }}
          resizeMode="contain"
          onLoad={() => { setImageLoaded(true); onLoad?.(); }}
          onError={() => {
            setImageLoaded(false);
            setFailedIndex((index) => index + 1);
          }}
          style={[styles.image, imageStyle]}
        />
      ) : null}
      {(!visibleUrl || !imageLoaded) && (
        <View pointerEvents="none" style={styles.placeholder}>
          <Ionicons name="medical-outline" size={23} color={palette.muted} />
          <Text style={styles.placeholderText}>
            {visibleUrl ? 'Carregando imagem…' : label}
          </Text>
        </View>
      )}
    </View>
  );
}

const useStyles = createThemedStyles((palette) => ({
  container: { overflow: 'hidden', alignItems: 'center', justifyContent: 'center', backgroundColor: palette.imageSurface },
  image: { width: '100%', height: '100%' },
  placeholder: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, alignItems: 'center', justifyContent: 'center', gap: 6, backgroundColor: palette.imageSurface },
  placeholderText: { color: palette.muted, fontSize: 11, lineHeight: 15, textAlign: 'center' },
}));
