import Ionicons from '@expo/vector-icons/Ionicons';
import React, { useEffect, useState } from 'react';
import { Image, ImageStyle, StyleProp, Text, View, ViewStyle } from 'react-native';

import { isGenericOfferImage, resolveMedicineImages } from '../services/catalog';

import { createThemedStyles, useAppTheme } from '../context/ThemeContext';
import { Medicamento } from '../types/api';

const IMAGE_REQUEST_TIMEOUT_MS = 12_000;

function selectedPharmacyName(medicine: Medicamento, preferredOfferId?: number) {
  const offer = preferredOfferId !== undefined
    ? medicine.ofertas.find((candidate) => candidate.id === preferredOfferId)
    : [...medicine.ofertas]
      .filter((candidate) => candidate.disponivel !== false)
      .sort((a, b) => Number(a.preco) - Number(b.preco) || a.id - b.id)[0];
  const rawName = offer?.farmacia_nome
    || offer?.farmacia?.nome_fantasia
    || offer?.farmacia?.razao_social
    || '';
  const normalized = rawName.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  if (normalized.includes('indiana')) return 'Farmácia Indiana';
  if (normalized.includes('pacheco')) return 'Drogarias Pacheco';
  if (normalized.includes('araujo')) return 'Drogaria Araújo';
  try {
    const hostname = new URL(offer?.url_origem || '').hostname.toLowerCase();
    if (hostname === 'farmaciaindiana.com.br' || hostname.endsWith('.farmaciaindiana.com.br')) return 'Farmácia Indiana';
    if (hostname === 'drogariaspacheco.com.br' || hostname.endsWith('.drogariaspacheco.com.br')) return 'Drogarias Pacheco';
    if (hostname === 'araujo.com.br' || hostname.endsWith('.araujo.com.br')) return 'Drogaria Araújo';
  } catch {
    return undefined;
  }
  return undefined;
}

type MedicineImageProps = {
  medicine: Medicamento;
  style?: StyleProp<ViewStyle>;
  imageStyle?: StyleProp<ImageStyle>;
  label?: string;
  onLoad?: () => void;
  preferredOfferId?: number;
};

export function MedicineImage({ medicine, style, imageStyle, label = 'Imagem indispon\u00edvel', onLoad, preferredOfferId }: MedicineImageProps) {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const imageUrls = resolveMedicineImages(medicine, preferredOfferId);
  const imageKey = imageUrls.join('|');
  const pharmacyName = selectedPharmacyName(medicine, preferredOfferId);
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 });
  const largePlaceholder = frameSize.width >= 130 && frameSize.height >= 110;
  const [failedIndex, setFailedIndex] = useState(0);
  const [imageLoaded, setImageLoaded] = useState(false);
  const visibleUrl = imageUrls[failedIndex];
  const genericFallback = isGenericOfferImage(medicine, visibleUrl);

  useEffect(() => {
    setFailedIndex(0);
    setImageLoaded(false);
  }, [imageKey]);

  useEffect(() => {
    if (!visibleUrl || imageLoaded) return undefined;
    const timeout = setTimeout(() => {
      setFailedIndex((index) => index + 1);
    }, IMAGE_REQUEST_TIMEOUT_MS);
    return () => clearTimeout(timeout);
  }, [imageLoaded, visibleUrl]);

  return (
    <View
      testID="medicine-image-frame"
      style={[styles.container, style]}
      onLayout={({ nativeEvent: { layout } }) => {
        setFrameSize((current) => current.width === layout.width && current.height === layout.height
          ? current
          : { width: layout.width, height: layout.height });
      }}
    >
      {visibleUrl ? (
        <Image
          key={visibleUrl}
          testID="medicine-image"
          accessibilityLabel={genericFallback
            ? `Imagem ilustrativa fornecida pela farmácia para ${medicine.nome}`
            : `Embalagem de ${medicine.nome}`}
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
      {visibleUrl && imageLoaded && genericFallback ? (
        <View pointerEvents="none" style={styles.genericBadge}>
          <Text style={styles.genericBadgeText}>Imagem ilustrativa</Text>
        </View>
      ) : null}
      {(!visibleUrl || !imageLoaded) && (
        <View
          pointerEvents="none"
          style={[
            pharmacyName && !visibleUrl ? styles.pharmacyPlaceholder : styles.placeholder,
            pharmacyName && !visibleUrl && largePlaceholder && styles.pharmacyPlaceholderLarge,
          ]}
          accessible={!visibleUrl}
          accessibilityLabel={!visibleUrl && pharmacyName
            ? `${pharmacyName}. Imagem ilustrativa do Preço Bão; não representa a embalagem de ${medicine.nome}.`
            : undefined}
        >
          {visibleUrl ? (
            <>
              <Ionicons name="medical-outline" size={23} color={palette.muted} />
              <Text style={styles.placeholderText}>Carregando imagem…</Text>
            </>
          ) : pharmacyName ? (
            <>
              <View style={[styles.illustrationIcon, largePlaceholder && styles.illustrationIconLarge]}>
                <Ionicons name="medical-outline" size={largePlaceholder ? 32 : 16} color={palette.primary} />
              </View>
              <Text style={[styles.illustrationTitle, largePlaceholder && styles.illustrationTitleLarge]}>Imagem ilustrativa</Text>
              <Text style={[styles.pharmacyLabel, largePlaceholder && styles.pharmacyLabelLarge]}>{pharmacyName}</Text>
            </>
          ) : (
            <>
              <Ionicons name="medical-outline" size={23} color={palette.muted} />
              <Text style={styles.placeholderText}>{label}</Text>
            </>
          )}
      </View>
      )}
    </View>
  );
}

const useStyles = createThemedStyles((palette) => ({
  container: { overflow: 'hidden', alignItems: 'center', justifyContent: 'center', backgroundColor: palette.imageSurface },
  image: { width: '100%', height: '100%' },
  genericBadge: { position: 'absolute', right: 5, bottom: 5, borderRadius: 5, paddingHorizontal: 6, paddingVertical: 3, backgroundColor: palette.surface, opacity: 0.88 },
  genericBadgeText: { color: palette.muted, fontSize: 9, lineHeight: 12, fontWeight: '600' },
  placeholder: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, alignItems: 'center', justifyContent: 'center', gap: 6, padding: 8, backgroundColor: palette.imageSurface },
  pharmacyPlaceholder: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, alignItems: 'center', justifyContent: 'center', gap: 2, padding: 3, backgroundColor: palette.imageSurface },
  pharmacyPlaceholderLarge: { gap: 8, padding: 12 },
  illustrationIcon: { width: 24, height: 24, borderRadius: 8, alignItems: 'center', justifyContent: 'center', backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.border },
  illustrationIconLarge: { width: 56, height: 56, borderRadius: 18 },
  illustrationTitle: { color: palette.text, fontSize: 9, fontWeight: '700', lineHeight: 11, textAlign: 'center' },
  illustrationTitleLarge: { fontSize: 15, lineHeight: 20 },
  pharmacyLabel: { color: palette.muted, fontSize: 9, lineHeight: 11, textAlign: 'center' },
  pharmacyLabelLarge: { fontSize: 13, lineHeight: 17 },
  placeholderText: { color: palette.muted, fontSize: 11, lineHeight: 15, textAlign: 'center' },
}));
