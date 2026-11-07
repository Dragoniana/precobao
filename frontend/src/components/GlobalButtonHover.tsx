import { useEffect } from 'react';

import { Platform } from 'react-native';
import { useAppTheme } from '../context/ThemeContext';

const STYLE_ID = 'preco-bao-global-button-hover';

export function GlobalButtonHover() {
  const { palette } = useAppTheme();

  useEffect(() => {
    if (Platform.OS !== 'web') {
      return;
    }

    if (typeof document === 'undefined') {
      return;
    }

    const existingStyle = document.getElementById(STYLE_ID);

    existingStyle?.remove();

    const style = document.createElement('style');

    style.id = STYLE_ID;

    style.innerHTML = `
      button,
      a[href],
      [role="button"],
      div[tabindex="0"] {
        cursor: pointer;
        transition:
          filter 160ms ease-out,
          opacity 160ms ease-out,
          box-shadow 160ms ease-out,
          background-color 160ms ease-out,
          border-color 160ms ease-out;
      }

      button:hover:not(:disabled),
      a[href]:hover,
      [role="button"]:hover:not([aria-disabled="true"]),
      div[tabindex="0"]:hover:not([aria-disabled="true"]) {
        filter: brightness(0.96);
      }

      button:active:not(:disabled),
      a[href]:active,
      [role="button"]:active:not([aria-disabled="true"]),
      div[tabindex="0"]:active:not([aria-disabled="true"]) {
        filter: brightness(0.92);
      }

      button:focus-visible,
      a[href]:focus-visible,
      [role="button"]:focus-visible,
      div[tabindex="0"]:focus-visible {
        outline: 2px solid ${palette.primary};
        outline-offset: 3px;
      }
    `;

    document.head.appendChild(style);

    return () => {
      const currentStyle = document.getElementById(STYLE_ID);

      if (currentStyle) {
        currentStyle.remove();
      }
    };
  }, [palette.primary]);

  return null;
}
