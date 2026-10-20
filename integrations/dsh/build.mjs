import { copyFile, mkdir, rm } from 'node:fs/promises';

const files = [
  'SKILL.md',
  'references/setup.md',
  'references/invoices.md',
  'scripts/agentqr.py',
  'scripts/runtime.py',
  'scripts/invoices.py',
  'scripts/invoice_common.py',
  'scripts/invoice_mail.py',
  'scripts/invoice_http.py',
  'scripts/invoice_files.py',
  'scripts/invoice_flow.py',
  'scripts/requirements-invoices.txt',
  'assets/self-test.png.base64',
];
const target = new URL('./skills/agentqr/', import.meta.url);
await rm(target, { recursive: true, force: true });
for (const file of files) {
  const destination = new URL(file, target);
  await mkdir(new URL('.', destination), { recursive: true });
  await copyFile(new URL(`../../skills/agentqr/${file}`, import.meta.url), destination);
}
await copyFile(new URL('../../LICENSE', import.meta.url), new URL('./LICENSE', import.meta.url));
