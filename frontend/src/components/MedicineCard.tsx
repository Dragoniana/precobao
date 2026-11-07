import Ionicons from '@expo/vector-icons/Ionicons';
import React, { useMemo, useState } from 'react';
import { GestureResponderEvent, Platform, Pressable, Text, useWindowDimensions, View } from 'react-native';

import { useFavoritesContext, useSessionContext } from '../context/AppContext';
import { getLaboratoryDisplayName } from '../services/catalog';
import { MedicineImage } from './MedicineImage';

import { createThemedStyles, useAppTheme } from '../context/ThemeContext';
import { Medicamento } from '../types/api';

type MedicineCardProps = { medicine: Medicamento; onPress: () => void; compact?: boolean; wide?: boolean; columns?: 2 | 3 | 4 };

function formatPrice(value: number) {
  return value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function usefulText(value?: string | null) {
  const text = value?.trim();
  if (!text || /^(não informado|nao informado|pendente|indeterminado|[-—])$/i.test(text)) return undefined;
  return text;
}

function getPresentation(name: string) {
  const dosage = name.match(/\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|g|ml|ui|%)\b/i)?.[0];
  const packageSize = name.match(/\b(?:caixa\s+com\s+)?\d+\s+(?:comprimidos?|cápsulas?|capsulas?|ml|g|unidades?|sachês?|doses?)\b/i)?.[0];
  return [dosage, packageSize].filter(Boolean).join(' · ');
}

export function MedicineCard({ medicine, onPress, compact = false, wide = false, columns }: MedicineCardProps) {
  const styles = useStyles();
  const { palette } = useAppTheme();
  const { favoriteIds, toggleFavorite } = useFavoritesContext();
  const { sessionMode } = useSessionContext();
  const { width, fontScale } = useWindowDimensions();
  const [focused, setFocused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const isCompactList = compact && width <= 480;
  const isNarrowCompactList = isCompactList && width <= 360;
  const isFavorite = favoriteIds.includes(medicine.id);
  const lowestOffer = useMemo(() => {
    const offers = medicine.ofertas?.filter((offer) => offer.disponivel !== false) ?? [];
    return offers.reduce<(typeof offers)[number] | null>((lowest, current) => !lowest || Number(current.preco) < Number(lowest.preco) ? current : lowest, null);
  }, [medicine.ofertas]);
  const laboratory = getLaboratoryDisplayName(medicine.laboratorio);
  const presentation = getPresentation(medicine.nome);
  const pharmacy = usefulText(lowestOffer?.farmacia_nome || lowestOffer?.farmacia?.nome_fantasia || lowestOffer?.farmacia?.razao_social);
  const cardWidth = wide || isCompactList ? '100%' : columns === 2 ? '48.5%' : columns === 3 ? '32%' : columns === 4 ? '23.5%' : !compact ? '100%' : width >= 1120 ? '32%' : width >= 680 ? '48.5%' : '100%';

  const handleFavoritePress = (event: GestureResponderEvent) => {
    event.stopPropagation();
    if (sessionMode === 'authenticated') toggleFavorite(medicine.id);
  };

  const handleComparePress = (event: GestureResponderEvent) => {
    event.stopPropagation();
    onPress();
  };

  const handleCardKeyDown = (event: any) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onPress();
    }
  };

  return (
    <Pressable
      testID="medicine-card"
      role="group"
      {...({ onKeyDown: handleCardKeyDown } as any)}
      accessibilityLabel={`Abrir detalhes de ${medicine.nome}`}
      accessibilityHint="Abre os detalhes e compara os preços deste medicamento"
      onPress={onPress}
      onHoverIn={() => setHovered(true)}
      onHoverOut={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      style={({ pressed }) => [styles.card, (isCompactList || wide) && styles.cardCompactList, { width: cardWidth }, (hovered || focused) && styles.cardHighlighted, pressed && styles.cardPressed]}
    >
      <MedicineImage medicine={medicine} style={[styles.imagePanel, (isCompactList || wide) && styles.imagePanelCompactList]} label={'Imagem n\u00e3o dispon\u00edvel'} />

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={isFavorite ? `Remover ${medicine.nome} dos favoritos` : `Salvar ${medicine.nome} nos favoritos`}
        accessibilityState={{ selected: isFavorite, disabled: sessionMode !== 'authenticated' }}
        accessibilityHint={sessionMode !== 'authenticated' ? 'Entre na sua conta para salvar favoritos' : undefined}
        onPress={handleFavoritePress}
        hitSlop={7}
        testID="medicine-card-favorite"
        style={({ pressed, hovered }) => [styles.favoriteButton, isCompactList && styles.favoriteButtonCompactList, hovered && styles.favoriteHovered, pressed && styles.favoritePressed, sessionMode !== 'authenticated' && styles.favoriteDisabled]}
      >
        <Ionicons name={isFavorite ? 'heart' : 'heart-outline'} size={18} color={isFavorite ? palette.primary : palette.textSoft} />
      </Pressable>

      <View style={[styles.details, (isCompactList || wide) && styles.detailsCompactList]}>
        <Text testID="medicine-card-title" numberOfLines={isCompactList && fontScale > 1.15 ? undefined : 2} ellipsizeMode="tail" style={[styles.title, (isCompactList || wide) && styles.titleCompactList]}>{medicine.nome}</Text>
        {!!presentation && <Text numberOfLines={1} ellipsizeMode="tail" style={styles.presentation}>{presentation}</Text>}
        {!!laboratory && <Text numberOfLines={1} ellipsizeMode="tail" style={styles.laboratory}>{laboratory}</Text>}

        <View testID="medicine-card-price-section" style={[styles.priceSection, (isCompactList || wide) && styles.priceSectionCompactList, isNarrowCompactList && styles.priceSectionCompactListStacked]}>
          <View style={styles.priceHeading}>
            <Text style={styles.priceCaption}>MENOR PREÇO</Text>
            {!!pharmacy && <Text numberOfLines={1} style={styles.pharmacy}>{pharmacy}</Text>}
          </View>
          <View testID="medicine-card-price-column" style={[(isCompactList || wide) && styles.priceColumnCompactList, isNarrowCompactList && styles.priceColumnCompactListStacked]}>
            <Text testID="medicine-card-price" numberOfLines={1} ellipsizeMode="tail" style={[styles.price, (isCompactList || wide) && styles.priceCompactList]}>{lowestOffer ? formatPrice(Number(lowestOffer.preco)) : 'Consulte ofertas'}</Text>
          </View>
        </View>

        <Pressable accessibilityRole="button" accessibilityLabel={`Comparar ofertas de ${medicine.nome}`} testID="medicine-card-compare" onPress={handleComparePress} style={({ pressed }) => [styles.cardFooter, (isCompactList || wide) && styles.cardFooterCompactList, pressed && styles.cardPressed]}>
          <Text style={styles.compareLabel}>Comparar ofertas</Text>
          <View style={styles.compareIcon}><Ionicons name="arrow-forward" size={16} color={palette.surface} /></View>
        </Pressable>
      </View>
    </Pressable>
  );
}

const useStyles = createThemedStyles((palette) => ({
  card: { backgroundColor: palette.surface, borderRadius: 14, borderWidth: 1, borderColor: palette.border, overflow: 'hidden', alignSelf: 'flex-start', minHeight: 356, position: 'relative', ...Platform.select({ web: { cursor: 'pointer', userSelect: 'none', transitionProperty: 'border-color, box-shadow, transform', transitionDuration: '180ms' } as any }) },
  cardCompactList: { minHeight: 136, flexDirection: 'row', alignSelf: 'stretch', alignItems: 'center' },
  cardPressed: { opacity: 0.92 },
  cardHighlighted: { borderColor: palette.primary, shadowColor: palette.primary, shadowOpacity: 0.16, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 3, transform: [{ translateY: -2 }] },
  imagePanel: { height: 148, backgroundColor: palette.surface, borderBottomWidth: 1, borderBottomColor: palette.border, alignItems: 'center', justifyContent: 'center', position: 'relative', padding: 10 },
  imagePanelCompactList: { width: 84, height: 120, minHeight: 0, alignSelf: 'center', flexShrink: 0, borderBottomWidth: 0, borderRightWidth: 1, borderRightColor: palette.border, padding: 6 },
  favoriteButton: { position: 'absolute', top: 8, right: 8, width: 38, height: 38, borderRadius: 12, backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.border, justifyContent: 'center', alignItems: 'center', zIndex: 1 },
  favoriteButtonCompactList: { left: 48, right: 'auto' },
  favoriteHovered: { backgroundColor: palette.surfaceBlue },
  favoritePressed: { opacity: 0.7, transform: [{ scale: 0.94 }] },
  favoriteDisabled: { opacity: 0.82 },
  details: { flex: 1, paddingHorizontal: 13, paddingTop: 11, paddingBottom: 12 },
  detailsCompactList: { flex: 1, minWidth: 0, justifyContent: 'flex-start', paddingHorizontal: 12, paddingTop: 8, paddingBottom: 8 },
  title: { minHeight: 44, color: palette.text, fontSize: 16, lineHeight: 22, fontWeight: '700' },
  titleCompactList: { minHeight: 0, fontSize: 14, lineHeight: 18, fontWeight: '700' },
  presentation: { color: palette.primaryDark, fontSize: 13, lineHeight: 18, fontWeight: '600', marginTop: 3 },
  laboratory: { color: palette.textSoft, fontSize: 13, lineHeight: 18, marginTop: 3 },
  priceSection: { marginTop: 'auto', paddingTop: 12 },
  priceSectionCompactList: { minWidth: 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 6, paddingTop: 0 },
  priceSectionCompactListStacked: { flexDirection: 'column', alignItems: 'flex-start', justifyContent: 'flex-end', gap: 1 },
  priceColumnCompactList: { minWidth: 100, maxWidth: 112, flexShrink: 0, alignItems: 'flex-end' },
  priceColumnCompactListStacked: { width: '100%', minWidth: 0, maxWidth: '100%', alignItems: 'flex-start' },
  priceHeading: { minHeight: 30, justifyContent: 'flex-end' },
  priceCaption: { color: palette.textSoft, fontSize: 11, lineHeight: 15, fontWeight: '700', letterSpacing: 0.5 },
  pharmacy: { color: palette.textSoft, fontSize: 12, lineHeight: 16, marginTop: 2 },
  price: { color: palette.primaryDark, fontSize: 24, lineHeight: 30, fontWeight: '700', marginTop: 2 },
  priceCompactList: { fontSize: 16, lineHeight: 21, marginTop: 0 },
  cardFooter: { borderTopWidth: 1, borderTopColor: palette.border, marginTop: 10, paddingTop: 9, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderRadius: 4 },
  cardFooterCompactList: { minHeight: 44, marginTop: 7, paddingTop: 6 },
  compareLabel: { color: palette.primaryDark, fontSize: 14, fontWeight: '700' },
  compareIcon: { width: 27, height: 27, borderRadius: 8, backgroundColor: palette.primary, alignItems: 'center', justifyContent: 'center' },
}));
