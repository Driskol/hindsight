#!/usr/bin/env node
/**
 * Render a figure as one animated SVG — for a README, a PR, an issue, a blog post.
 *
 *   node scripts/figure-svg.mjs what-hindsight-does            # a figure in figures/
 *   node scripts/figure-svg.mjs spec.json out.svg              # a spec written as JSON
 *
 * A spec is `{ "props": { layout, edges, steps } }` (or bare props) — the same shape the React
 * figures use, so anything here can move to figures/ and become interactive without a rewrite.
 *
 * No install, no browser: plain node (22+ strips the types on the way in).
 */
import { writeFileSync } from 'node:fs';
import { register } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { toSvg } from '../src/svg.ts';

register('./figure-loader.mjs', import.meta.url); // lets a figure file load without its JSX entry
const root = dirname(dirname(fileURLToPath(import.meta.url)));
const [input, out] = process.argv.slice(2);
if (!input) {
  console.error('usage: node scripts/figure-svg.mjs <spec.json|figure-slug> [out.svg]');
  process.exit(2);
}

const isJson = input.endsWith('.json');
const loaded = isJson
  ? (await import(resolve(input), { with: { type: 'json' } })).default
  : (await import(join(root, 'figures', `${input}.ts`))).default;
const props = loaded.props ?? loaded;
if (!props?.layout) throw new Error(`${input}: no figure props (expected { props: { layout, edges, steps } })`);

const dest = out ?? `${isJson ? input.replace(/\.json$/, '') : input}.svg`;
const svg = toSvg(props);
writeFileSync(dest, svg);
console.log(`${dest} — ${(svg.length / 1024).toFixed(1)} kB`);
