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
          #preco-initial-loader {
            position: fixed; inset: 0; z-index: 2147483647; display: flex; flex-direction: column;
            align-items: center; justify-content: center; gap: 16px;
            background: #101722; color: #FFFFFF; font: 700 20px system-ui, sans-serif;
            transition: opacity 140ms ease;
          }
          html[data-preco-theme='light'] #preco-initial-loader { background: #F6F8FC; color: #1B2234; }
          html[data-preco-theme='dark'] #preco-initial-loader { background: #101722; color: #FFFFFF; }
          html[data-preco-theme-ready='true'] #preco-initial-loader { opacity: 0; pointer-events: none; }
          #preco-initial-loader .brand { display: flex; align-items: center; gap: 12px; }
          #preco-initial-loader .mark {
            width: 44px; height: 44px; display: grid; place-items: center; border-radius: 12px;
            background: #3784F5; color: #FFFFFF; font-size: 24px; font-weight: 800;
          }
          #preco-initial-loader .spinner {
            width: 26px; height: 26px; border: 3px solid #AFC9F5; border-top-color: #3784F5;
            border-radius: 50%; animation: preco-spin 800ms linear infinite;
          }
          @keyframes preco-spin { to { transform: rotate(360deg); } }
        `}</style>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
        <ScrollViewStyleReset />
      </head>
      <body>
        <div id="preco-initial-loader" role="progressbar" aria-label="Carregando Preço Bão">
          <div className="brand"><span className="mark">P</span><span>Preço Bão</span></div>
          <span className="spinner" aria-hidden="true" />
        </div>
        {children}
      </body>
    </html>
  );
}
