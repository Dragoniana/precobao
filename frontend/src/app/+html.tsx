import { ScrollViewStyleReset } from 'expo-router/html';
import type { PropsWithChildren } from 'react';

const themeBootstrap = `
  try {
    var preference = localStorage.getItem('preco-bao-appearance') || localStorage.getItem('preco-bao:appearance');
    var systemDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    var dark = preference === 'dark' || (preference !== 'light' && systemDark);
    document.documentElement.dataset.precoTheme = dark ? 'dark' : 'light';
  } catch (_) {
    var fallbackDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.precoTheme = fallbackDark ? 'dark' : 'light';
  }
`;

export default function RootHtml({ children }: PropsWithChildren) {
  return (
    <html lang="pt-BR">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
        <style>{`
          html, body { height: 100%; margin: 0; background-color: #101722; }
          html[data-preco-theme='light'], html[data-preco-theme='light'] body { background-color: #F6F8FC; }
          html[data-preco-theme='dark'], html[data-preco-theme='dark'] body { background-color: #101722; }
          html:not([data-preco-theme-ready='true']) #root { visibility: hidden; }
        `}</style>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
        <ScrollViewStyleReset />
      </head>
      <body>{children}</body>
    </html>
  );
}
