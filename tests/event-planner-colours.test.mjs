import assert from 'node:assert/strict';
import test from 'node:test';

import {PLANNER_THEME_SWATCHES, renderColourControl} from '../frontend/static/event-planner-colours.js';

test('colour control exposes swatches, a custom picker, reset, and inherited state', () => {
  const html = renderColourControl({
    explicitColor: null,
    effectiveColor: '#2563eb',
    inherited: true,
    disabled: false,
  });

  assert.equal(PLANNER_THEME_SWATCHES.length, 8);
  assert.match(html, /data-theme-swatch="#2563eb"/);
  assert.match(html, /type="color"/);
  assert.match(html, /name="theme_color"/);
  assert.match(html, /data-theme-reset/);
  assert.match(html, />Inherited from zone</);
  assert.match(html, /aria-pressed="false"/);
});

test('colour control identifies an explicit swatch and disables read-only actions', () => {
  const html = renderColourControl({
    explicitColor: '#a855f7',
    effectiveColor: '#a855f7',
    inherited: false,
    disabled: true,
  });

  assert.match(html, /data-theme-swatch="#a855f7"[^>]*aria-pressed="true"/);
  assert.match(html, /name="theme_color"[^>]*disabled/);
  assert.match(html, /data-theme-reset[^>]*disabled/);
  assert.match(html, />Custom colour</);
});
