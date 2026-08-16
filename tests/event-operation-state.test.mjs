import test from 'node:test';
import assert from 'node:assert/strict';
import {addNode, addEdge, deleteSelection, autoArrange, moveNode, nextId} from '../frontend/static/event-operation-state.js';

const base = () => ({version:1, policy:{launch_mode:'manual'}, nodes:[
  {id:'start',type:'start',label:'Start',x:0,y:0,config:{}},
  {id:'finish',type:'finish',label:'Finish',x:600,y:0,config:{}}
], edges:[]});

test('nextId produces the first unused stable identifier', () => {
  assert.equal(nextId('ability', [{id:'ability-1'}, {id:'ability-2'}]), 'ability-3');
});

test('addNode does not mutate the current draft', () => {
  const state = base();
  const next = addNode(state, {type:'delay', label:'Wait', config:{seconds:10}}, {x:200,y:80});
  assert.equal(state.nodes.length, 2);
  assert.equal(next.nodes[2].id, 'delay-1');
  assert.deepEqual([next.nodes[2].x,next.nodes[2].y], [200,80]);
});

test('addEdge rejects self, duplicate typed, and invalid conditions', () => {
  const state = addNode(base(), {type:'delay',label:'Wait',config:{}}, {x:100,y:0});
  assert.throws(() => addEdge(state,'start','start','always'), /different/);
  assert.throws(() => addEdge(state,'start','delay-1','maybe'), /condition/);
  const connected = addEdge(state,'start','delay-1','always');
  assert.throws(() => addEdge(connected,'start','delay-1','always'), /already exists/);
});

test('deleteSelection removes incident edges', () => {
  let state = addNode(base(), {type:'delay',label:'Wait',config:{}}, {x:100,y:0});
  state = addEdge(state,'start','delay-1','always');
  const next = deleteSelection(state, {kind:'node',id:'delay-1'});
  assert.equal(next.nodes.length, 2);
  assert.equal(next.edges.length, 0);
});

test('autoArrange is deterministic and orders connected nodes by depth', () => {
  let state = addNode(base(), {type:'delay',label:'Wait',config:{}}, {x:999,y:999});
  state = addEdge(state,'start','delay-1','always');
  state = addEdge(state,'delay-1','finish','always');
  const arranged = autoArrange(state);
  const xs = Object.fromEntries(arranged.nodes.map(node => [node.id,node.x]));
  assert.ok(xs.start < xs['delay-1'] && xs['delay-1'] < xs.finish);
  assert.deepEqual(autoArrange(state), arranged);
});

test('moveNode updates one node without mutating state and clamps to the canvas origin', () => {
  const state = base();
  const moved = moveNode(state, 'start', {x:-30,y:245});
  assert.deepEqual([state.nodes[0].x,state.nodes[0].y], [0,0]);
  assert.deepEqual([moved.nodes[0].x,moved.nodes[0].y], [0,245]);
  assert.deepEqual(moved.nodes[1], state.nodes[1]);
});
