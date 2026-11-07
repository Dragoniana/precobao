import { Pressable, Text, View } from 'react-native';

import { spacing } from '../theme';
import { createThemedStyles } from '../context/ThemeContext';
import { Medicamento } from '../types/api';
import { MedicineImage } from './MedicineImage';

type OfferRowProps = {
  medicine?: Medicamento;
  medicineName: string;
  laboratory?: string;
  pharmacy: string;
  price: number | string;    // Agora aceita a string do Decimal do backend
  color?: string;
  onPress?: () => void;
};

function OfferRowContent({ medicine, medicineName, laboratory, pharmacy, price, color }: OfferRowProps) {
  const styles = useStyles();
  const safePrice = Number(price);
  const priceDisplay = Number.isFinite(safePrice)
    ? safePrice.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '--';

  return (
    <>
      {medicine ? <MedicineImage medicine={medicine} style={styles.medicineImage} label="" /> : null}
      <View style={styles.offerInfo}>
        <Text style={styles.name} numberOfLines={2} ellipsizeMode="tail">{medicineName}</Text>
        {!!laboratory && <Text style={styles.laboratory} numberOfLines={1} ellipsizeMode="tail">{laboratory}</Text>}
        <Text style={[styles.pharmacy, color ? { color } : null]} numberOfLines={1}>{pharmacy}</Text>
      </View>
      <View style={styles.priceColumn}>
        <Text style={styles.price} numberOfLines={1}>R$ {priceDisplay}</Text>
      </View>
    </>
  );
}

export function OfferRow(props: OfferRowProps) {
  const styles = useStyles();
  if (props.onPress) {
    return (
      <Pressable style={styles.row} onPress={props.onPress}>
        <OfferRowContent {...props} />
      </Pressable>
    );
  }

  return (
    <View style={styles.row}>
      <OfferRowContent {...props} />
    </View>
  );
}

const useStyles = createThemedStyles((palette) => ({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    minWidth: 0,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: palette.border,
  },
  medicineImage: { width: 58, height: 58, flexShrink: 0, borderRadius: 8, borderWidth: 1, borderColor: palette.border, backgroundColor: palette.surface },
  offerInfo: {
    flex: 1,
    minWidth: 0,
  },
  name: {
    fontSize: 16,
    lineHeight: 21,
    color: palette.text,
    marginBottom: 2,
  },
  pharmacy: {
    fontSize: 12,
  },
  laboratory: { color: palette.textSoft, fontSize: 12, marginBottom: 2 },
  priceColumn: {
    minWidth: 94,
    flexShrink: 0,
    alignItems: 'flex-end',
  },
  price: {
    fontWeight: '700',
    fontSize: 16,
    color: palette.text,
  },
}));
