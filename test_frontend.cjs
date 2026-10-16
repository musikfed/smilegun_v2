const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'templates', 'index.html'), 'utf8');
const game = fs.readFileSync(path.join(__dirname, 'static', 'game.js'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, 'static', 'i18n.js'), 'utf8');

new Function(game); // JavaScript syntax check
new Function(i18n); // translation bundle syntax check

const checks = [
  ['responsive mobile CSS', /@media\(max-width:800px\),\(orientation:portrait\)/.test(html)],
  ['template has no accidental Jinja comment opener', !html.includes('{#')],
  ['no old CSS zoom workaround', !/--workspace-scale|zoom:\s*\.45/.test(html)],
  ['front camera request', /facingMode:\s*\{ideal:\s*"user"\}/.test(game)],
  ['playsinline preview', /playsinline/.test(html)],
  ['five language selector options', ['ru','en','zh','kk','ko'].every(code => html.includes(`value="${code}"`))],
  ['all five translation packs', ['ru','en','zh','kk','ko'].every(code => new RegExp(`\\b${code}:\\s*\\{`).test(i18n))],
  ['localized four game modes', /mode_targets/.test(game) && /mode_galaxy/.test(game) && /mode_dodge/.test(game) && /mode_side/.test(game)],
  ['mouth spread event', /mouth_shot_id/.test(game)],
  ['smile time bonus cap', /Math\.min\(cap, remain \+ bonus\)/.test(game)],
  ['smile armor', /smile_armor_seconds/.test(game)],
  ['peace ghost', /state\.peace/.test(game)],
  ['two palms pause', /two_palms_id/.test(game)],
  ['bottom exit button', /id="exit-game"/.test(html)],
  ['browser frame upload', /\/camera\/browser\/frame/.test(game)],
  ['OpenCV fallback', /\/camera\/start/.test(game)],
  ['super smile trigger consumption', /super_id/.test(game)],
  ['spread scales with charge', /5 \+ Math\.round\(charge \* 6\)/.test(game)],
  ['language persistence', /localStorage\.setItem\("smilegun_lang"/.test(game)],
  ['repository screenshot demo mode', /applyDemoMode/.test(game)],
];

let passed = 0;
for (const [name, condition] of checks) {
  assert.ok(condition, name);
  passed++;
}
console.log(`${passed} frontend regressions passed.`);
