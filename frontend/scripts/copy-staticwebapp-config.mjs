import { copyFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

const source = resolve('staticwebapp.config.json');
const destination = resolve('dist/staticwebapp.config.json');

await mkdir(dirname(destination), { recursive: true });
await copyFile(source, destination);
console.log('Copied staticwebapp.config.json to the Expo export.');
