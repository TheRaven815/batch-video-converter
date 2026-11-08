/* global process, console */
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const frontendDir = resolve(scriptDir, '..');
const repoRoot = resolve(frontendDir, '..');

function pythonExecutable() {
  if (process.env.PYTHON) return process.env.PYTHON;

  const candidates =
    process.platform === 'win32'
      ? [join(repoRoot, 'venv', 'Scripts', 'python.exe')]
      : [join(repoRoot, 'venv', 'bin', 'python')];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

function run(command, args, env) {
  const result = spawnSync(command, args, { stdio: 'inherit', env });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

const tempDir = mkdtempSync(join(tmpdir(), 'bvc-openapi-'));
const schemaPath = join(tempDir, 'openapi.json');
const env = { ...process.env, PYTHONPATH: join(repoRoot, 'src') };

try {
  run(pythonExecutable(), [join(repoRoot, 'scripts', 'export_openapi.py'), schemaPath], env);
  run(
    process.execPath,
    [
      join(frontendDir, 'node_modules', 'openapi-typescript', 'bin', 'cli.js'),
      schemaPath,
      '-o',
      join(frontendDir, 'src', 'generated', 'api-schema.ts'),
    ],
    process.env,
  );
} finally {
  rmSync(tempDir, { recursive: true, force: true });
}
