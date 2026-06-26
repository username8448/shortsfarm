import {readFile} from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const raw = Buffer.concat(chunks).toString('utf8');
const payload = JSON.parse(raw);

if (!payload?.recipe || !payload?.outputPath) {
  throw new Error('Render payload должен содержать recipe и outputPath.');
}

await readFile(path.resolve('src/remotion/index.ts'));
const serveUrl = await bundle({
  entryPoint: path.resolve('src/remotion/index.ts'),
});
const compositionId = payload.recipe?.template?.composition_id || 'ReactionLayoutTemplate';
const rendererOptions = {
  serveUrl,
  id: compositionId,
  inputProps: payload.recipe,
  browserExecutable: payload.browserExecutable || undefined,
  chromiumOptions: {enableMultiProcessOnLinux: true},
};
const composition = await selectComposition(rendererOptions);
await renderMedia({
  codec: 'h264',
  composition,
  serveUrl,
  outputLocation: payload.outputPath,
  inputProps: payload.recipe,
  browserExecutable: payload.browserExecutable || undefined,
  chromiumOptions: {enableMultiProcessOnLinux: true},
  overwrite: true,
});

process.stdout.write(JSON.stringify({status: 'done', outputPath: payload.outputPath}));
