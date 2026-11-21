import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const validator = fileURLToPath(new URL('./validate-web-build.mjs', import.meta.url));
const validUrl = 'https://precobao.app/backend';

function validate(value) {
  return spawnSync(process.execPath, [validator, '--config'], {
    encoding: 'utf8',
    env: { ...process.env, EXPO_PUBLIC_API_URL: value ?? '' },
  });
}

assert.equal(validate(validUrl).status, 0, 'accepts the production HTTPS /backend URL');
for (const invalid of [
  undefined,
  '',
  'http://precobao.app/backend',
  'https://precobao.app/',
  'https://localhost/backend',
  'https://192.168.1.10/backend',
  'https://user:password@precobao.app/backend',
  'https://precobao.app/backend?token=x',
]) {
  assert.notEqual(validate(invalid).status, 0, `rejects invalid production URL: ${invalid ?? '(missing)'}`);
}

const expoCli = fileURLToPath(new URL('../node_modules/expo/bin/cli', import.meta.url));
const expoConfig = spawnSync(process.execPath, [expoCli, 'config', '--type', 'public', '--json'], {
  encoding: 'utf8',
  env: { ...process.env, EXPO_PUBLIC_API_URL: validUrl },
});
assert.equal(expoConfig.status, 0, expoConfig.stderr);
assert.equal(JSON.parse(expoConfig.stdout).extra.apiUrl, validUrl, 'Expo manifest carries the production API URL');

console.log('Production web build URL validation tests passed.');
