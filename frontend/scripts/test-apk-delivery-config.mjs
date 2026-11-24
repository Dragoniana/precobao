import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const nginx = readFileSync(new URL('../nginx.production.conf', import.meta.url), 'utf8');
const compose = readFileSync(new URL('../../deploy/azure/docker-compose.yml', import.meta.url), 'utf8');
const releases = readFileSync(new URL('../src/config/appReleases.ts', import.meta.url), 'utf8');

assert.ok(nginx.includes('location = /downloads/preco-bao.apk'), 'stable download URL must be configured');
assert.ok(nginx.includes('preco-bao-1.0.1-internal.apk'), 'stable URL must map to the versioned artifact');
assert.ok(nginx.includes('application/vnd.android.package-archive'), 'APK MIME type must be configured');
assert.ok(nginx.includes('Content-Disposition'), 'APK must be delivered as an attachment');
assert.ok(compose.includes('/usr/share/nginx/html/downloads'), 'web container must receive its read-only download directory');
assert.ok(compose.includes('read_only: true'), 'download directory must be mounted read-only');
assert.ok(releases.includes('https://precobao.app/downloads/preco-bao.apk'), 'mobile UI must use the stable HTTPS URL');

console.log('APK delivery configuration guard passed.');
