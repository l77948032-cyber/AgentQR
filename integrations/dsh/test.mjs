import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import { skillConfig } from './config.js';

test('registers the isolated bundled skill through the native DSH provider', () => {
  const config = skillConfig();
  assert.equal(config.providerName, 'dsh-agentqr');
  assert.equal(config.includeDefaultRoots, false);
  assert.equal(config.watch, false);
  assert.ok(isAbsolute(config.bundledSkillDir));
});

test('bundles exact current instructions, runtime and synthetic test without caches', async () => {
  for (const file of ['SKILL.md', 'references/setup.md', 'references/invoices.md',
    'scripts/agentqr.py', 'scripts/runtime.py', 'scripts/invoices.py', 'scripts/invoice_common.py',
    'scripts/invoice_mail.py', 'scripts/invoice_http.py', 'scripts/invoice_files.py',
    'scripts/invoice_flow.py', 'scripts/requirements-invoices.txt', 'assets/self-test.png.base64']) {
    assert.deepEqual(
      await readFile(new URL(`./skills/agentqr/${file}`, import.meta.url)),
      await readFile(new URL(`../../skills/agentqr/${file}`, import.meta.url)),
    );
  }
  const files = await readdir(new URL('./skills/', import.meta.url), { recursive: true });
  assert.ok(!files.some(file => /__pycache__|\.pyc$|\.env|credential|cookie/.test(file)));
});
