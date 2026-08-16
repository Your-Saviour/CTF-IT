import test from 'node:test';
import assert from 'node:assert/strict';
import {createViewport, graphPoint, zoomAt, fitViewport, createHistory, nodeTargetLabel} from '../frontend/static/event-operation-workspace.js';

test('zoomAt keeps the graph point beneath the cursor stable', () => {
  const viewport = {x:100,y:50,zoom:1,width:800,height:600};
  const cursor = {x:300,y:250};
  const before = graphPoint(viewport, cursor);
  const zoomed = zoomAt(viewport, cursor, 1.5);
  assert.deepEqual(graphPoint(zoomed, cursor), before);
  assert.equal(zoomed.zoom, 1.5);
});

test('zoomAt clamps zoom to supported limits', () => {
  const viewport = createViewport(800,600);
  assert.equal(zoomAt(viewport,{x:0,y:0},0.1).zoom,0.35);
  assert.equal(zoomAt(viewport,{x:0,y:0},4).zoom,2);
});

test('fitViewport centres node bounds with padding', () => {
  const fitted = fitViewport([
    {x:100,y:100},{x:500,y:300},
  ], {width:1000,height:600}, 80);
  assert.ok(fitted.zoom > 1);
  const first = {x:100*fitted.zoom+fitted.x,y:100*fitted.zoom+fitted.y};
  const last = {x:(500+180)*fitted.zoom+fitted.x,y:(300+86)*fitted.zoom+fitted.y};
  assert.ok(first.x>=80&&first.y>=80);
  assert.ok(last.x<=920&&last.y<=520);
});

test('history deduplicates commits and supports undo and redo', () => {
  const history = createHistory({value:1});
  history.commit({value:1});
  history.commit({value:2});
  assert.deepEqual(history.undo(),{value:1});
  assert.deepEqual(history.redo(),{value:2});
});

test('new history commit clears redo and respects its limit', () => {
  const history = createHistory({value:0},3);
  history.commit({value:1});history.commit({value:2});history.commit({value:3});
  assert.deepEqual(history.undo(),{value:2});
  history.commit({value:4});
  assert.equal(history.redo(),null);
  assert.deepEqual(history.undo(),{value:2});
  assert.deepEqual(history.undo(),{value:1});
  assert.equal(history.undo(),null);
});

test('nodeTargetLabel resolves configured planned VM names', () => {
  const catalogue={targets:[{id:'vm:site/zone/web',name:'web-server',zone:'public'}]};
  assert.equal(nodeTargetLabel({type:'ability',config:{target_vm_id:'vm:site/zone/web'}},catalogue),'web-server');
  assert.equal(nodeTargetLabel({type:'objective',config:{target_vm_id:'missing'}},catalogue),'Unknown target');
  assert.equal(nodeTargetLabel({type:'delay',config:{}},catalogue),'');
});
