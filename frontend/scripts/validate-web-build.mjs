import { readdir, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const expectedApiUrl = 'https://precobao.app/backend';

async function configuredApiUrl() {
  if (process.env.EXPO_PUBLIC_API_URL?.trim()) return process.env.EXPO_PUBLIC_API_URL.trim();
  try {
    const dotenv = await readFile(resolve('.env'), 'utf8');
    const line = dotenv.split(/\r?\n/).find((entry) => /^\s*EXPO_PUBLIC_API_URL\s*=/.test(entry));
    const value = line?.replace(/^\s*EXPO_PUBLIC_API_URL\s*=\s*/, '').trim();
    return value?.replace(/^(["'])(.*)\1$/, '$2');
  } catch {
    return undefined;
  }
}

export function validateProductionApiUrl(value) {
  let url;
  try {
    url = new URL(value ?? '');
  } catch {
    throw new Error('Set EXPO_PUBLIC_API_URL to https://precobao.app/backend.');
  }

  if (
    url.href !== `${expectedApiUrl}` ||
    url.protocol !== 'https:' ||
    url.hostname !== 'precobao.app' ||
    url.pathname !== '/backend' ||
    url.username || url.password || url.search || url.hash
  ) {
    throw new Error('Production Web builds must use exactly https://precobao.app/backend.');
  }
}

async function walk(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walk(path));
    else files.push(path);
  }
  return files;
}

const mode = process.argv[2];
if (mode === '--config') {
  validateProductionApiUrl(await configuredApiUrl());
  console.log(`Validated production API URL: ${expectedApiUrl}`);
} else if (mode === '--bundle') {
  validateProductionApiUrl(await configuredApiUrl());
  const dist = resolve('dist');
  const files = await walk(dist);
  const requiredRoutes = ['index.html', 'login.html', 'signup.html', 'forgot-password.html', 'search.html'];
  const requiredIcons = ['favicon.ico', 'favicon-16x16.png', 'favicon-32x32.png', 'apple-touch-icon.png'];
  const names = new Set(files.map((file) => file.slice(dist.length + 1).replaceAll('\\', '/')));
  for (const route of requiredRoutes) {
    if (!names.has(route)) throw new Error(`Web export is missing required route ${route}.`);
  }
  for (const icon of requiredIcons) {
    if (!names.has(icon)) throw new Error(`Web export is missing required favicon asset ${icon}.`);
  }
  const routeHtmlFiles = files.filter((file) => /\.html$/.test(file)
    && !['_sitemap.html', '+not-found.html'].includes(file.slice(dist.length + 1).replaceAll('\\', '/')));
  for (const file of routeHtmlFiles) {
    const contents = await readFile(file, 'utf8');
    if (!/<title[^>]*>precobao\.app<\/title>/.test(contents)) {
      throw new Error(`Exported route ${file} must have the fixed title precobao.app.`);
    }
    for (const icon of requiredIcons) {
      if (!contents.includes(`/${icon}?v=20261124`)) {
        throw new Error(`Exported route ${file} is missing the cache-busted ${icon} link.`);
      }
    }
  }
  const textFiles = files.filter((file) => /\.(?:html|js|json)$/.test(file));
  let apiUrlEmbedded = false;
  for (const file of textFiles) {
    const contents = await readFile(file, 'utf8');
    if (contents.includes(expectedApiUrl)) apiUrlEmbedded = true;
  }
  if (!apiUrlEmbedded) {
    throw new Error(`The exported Web runtime does not expose ${expectedApiUrl}; refusing the release.`);
  }
  console.log(`Validated ${files.length} exported assets and production API configuration.`);
} else {
  throw new Error('Usage: validate-web-build.mjs --config|--bundle');
}
