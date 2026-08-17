import test from 'node:test';
import assert from 'node:assert/strict';
import { clampOffset, effectiveMinutes } from '../frontend/static/event-timeline-state.js';

test('clampOffset rounds and clamps to [0, max]', () => {
  assert.equal(clampOffset(12.6, 60), 13);
  assert.equal(clampOffset(-3, 60), 0);
  assert.equal(clampOffset(99, 60), 60);
});

test('effectiveMinutes spans phases, injects, and operations', () => {
  const injects = [{ offset_minutes: 45 }];
  const phases = [{ end_offset_minutes: 90 }];
  const operations = [{ offset_minutes: 0, duration: 30 }];
  assert.equal(effectiveMinutes(injects, phases, operations, 60), 90);
});

test('effectiveMinutes falls back to the provided minimum', () => {
  assert.equal(effectiveMinutes([], [], [], 60), 60);
});
