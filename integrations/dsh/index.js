import * as filesystemSkills from '@deepseek-ai/dsh-skill-filesystem';
import { skillConfig } from './config.js';

export const name = 'agentqr';
export const inject = ['skills'];

export function apply(ctx) {
  filesystemSkills.apply(ctx, skillConfig());
}
