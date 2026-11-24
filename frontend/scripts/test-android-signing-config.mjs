import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const gradle = readFileSync(new URL('../android/app/build.gradle', import.meta.url), 'utf8');
const releaseBlock = gradle.match(/release\s*\{([\s\S]*?)\n\s*\}/)?.[1] ?? '';
const standaloneBlock = gradle.match(/standalone\s*\{([\s\S]*?)\n\s*\}/)?.[1] ?? '';

assert.ok(releaseBlock, 'release build type must exist');
assert.ok(standaloneBlock, 'standalone build type must exist');
assert.ok(!/signingConfig\s+signingConfigs\.debug/.test(releaseBlock), 'release must not use shared debug signing');
assert.ok(!/signingConfig\s+signingConfigs\.debug/.test(standaloneBlock), 'standalone must not use shared debug signing');
for (const setting of [
  'ANDROID_RELEASE_STORE_FILE',
  'ANDROID_RELEASE_STORE_PASSWORD',
  'ANDROID_RELEASE_KEY_ALIAS',
  'ANDROID_RELEASE_KEY_PASSWORD',
]) {
  assert.ok(gradle.includes(setting), `release signing must require ${setting}`);
}
assert.ok(gradle.includes('validateReleaseSigning'), 'release signing must fail closed without a valid external keystore');
assert.ok(!existsSync(new URL('../android/app/debug.keystore', import.meta.url)), 'shared debug keystore must not be shipped in source');

console.log('Android release signing guard passed.');
