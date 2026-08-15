import assert from 'node:assert/strict';
import test from 'node:test';

test('picker markup shows selected and Automatic SVG previews with accessible listbox semantics', async () => {
  const {renderIconPicker} = await import('../frontend/static/event-planner-icon-picker.js');
  const icon = {path: 'M1 1h2v2z', viewBox: '0 0 4 4'};
  const html = renderIconPicker({
    name: 'primary_icon', label: 'Primary icon', value: 'database', selectedLabel: 'Database',
    selectedIcon: icon, automaticLabel: 'Automatic (Server)', automaticIcon: icon,
    groups: [{label: 'Services', options: [{value: 'database', label: 'Database'}]}], disabled: false,
  });

  assert.match(html, /data-icon-picker="primary_icon"/);
  assert.match(html, /aria-haspopup="listbox"/);
  assert.match(html, /role="listbox"/);
  assert.match(html, /aria-label="Search Primary icon icons"/);
  assert.match(html, /data-icon-value=""/);
  assert.match(html, /Automatic \(Server\)/);
  assert.match(html, /data-icon-value="database"[^>]*aria-selected="true"/);
  assert.equal((html.match(/M1 1h2v2z/g) || []).length, 2);
});

test('picker markup escapes labels and SVG attributes', async () => {
  const {renderIconPicker} = await import('../frontend/static/event-planner-icon-picker.js');
  const html = renderIconPicker({
    name: 'icon', label: '<Secondary>', value: '', selectedLabel: 'Automatic',
    selectedIcon: {path: 'M0 0" onload="bad', viewBox: '0 0 24 24" onload="bad'},
    automaticLabel: 'Automatic', automaticIcon: {path: 'M0 0', viewBox: '0 0 24 24'},
    groups: [{label: '<Platforms>', options: []}], disabled: true,
  });

  assert.equal(html.includes('<Secondary>'), false);
  assert.equal(html.includes('<Platforms>'), false);
  assert.equal(html.includes('onload="bad'), false);
  assert.match(html, /disabled/);
});
