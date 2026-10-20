import { fileURLToPath } from 'node:url';

export function skillConfig() {
  return {
    providerName: 'dsh-agentqr',
    includeDefaultRoots: false,
    bundledSkillDir: fileURLToPath(new URL('./skills/', import.meta.url)),
    watch: false,
  };
}
