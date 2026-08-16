import test from 'node:test';
import assert from 'node:assert/strict';
import {
  addNode, addEdge, connectionError, deleteSelection, autoArrange, moveNode,
  moveNodes, duplicateNodes, insertConnectedNode, nextId,
  isTriggerType, replaceTrigger,
} from '../frontend/static/event-operation-state.js';

const base = () => ({version:1, policy:{time_limit_minutes:60}, nodes:[
  {id:'trigger',type:'manual_trigger',label:'Manual Trigger',x:0,y:0,disabled:false,config:{}},
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
  assert.throws(() => addEdge(state,'trigger','trigger','always'), /different/);
  assert.throws(() => addEdge(state,'trigger','delay-1','maybe'), /condition/);
  const connected = addEdge(state,'trigger','delay-1','always');
  assert.throws(() => addEdge(connected,'trigger','delay-1','always'), /already exists/);
});

test('deleteSelection removes incident edges', () => {
  let state = addNode(base(), {type:'delay',label:'Wait',config:{}}, {x:100,y:0});
  state = addEdge(state,'trigger','delay-1','always');
  const next = deleteSelection(state, {kind:'node',id:'delay-1'});
  assert.equal(next.nodes.length, 2);
  assert.equal(next.edges.length, 0);
});

test('autoArrange is deterministic and orders connected nodes by depth', () => {
  let state = addNode(base(), {type:'delay',label:'Wait',config:{}}, {x:999,y:999});
  state = addEdge(state,'trigger','delay-1','always');
  state = addEdge(state,'delay-1','finish','always');
  const arranged = autoArrange(state);
  const xs = Object.fromEntries(arranged.nodes.map(node => [node.id,node.x]));
  assert.ok(xs.trigger < xs['delay-1'] && xs['delay-1'] < xs.finish);
  assert.deepEqual(autoArrange(state), arranged);
});

test('moveNode updates one node without mutating state across the unbounded canvas', () => {
  const state = base();
  const moved = moveNode(state, 'trigger', {x:-30,y:245});
  assert.deepEqual([state.nodes[0].x,state.nodes[0].y], [0,0]);
  assert.deepEqual([moved.nodes[0].x,moved.nodes[0].y], [-30,245]);
  assert.deepEqual(moved.nodes[1], state.nodes[1]);
});

test('connectionError rejects a connection that would create a cycle', () => {
  let state = addNode(base(), {type:'delay',label:'Wait',config:{}}, {x:100,y:0});
  state = addEdge(state, 'trigger', 'delay-1', 'always');
  state = addEdge(state, 'delay-1', 'finish', 'always');
  assert.match(connectionError(state, 'finish', 'trigger', 'always'), /trigger/i);
  assert.throws(() => addEdge(state, 'finish', 'trigger', 'always'), /trigger/i);
});

test('insertConnectedNode atomically adds a node and typed edge without mutating state', () => {
  const state = base();
  const result = insertConnectedNode(
    state, 'trigger', 'failure',
    {type:'gate',label:'Handle failure',config:{mode:'any'}},
    {x:220,y:140},
  );
  assert.equal(state.nodes.length, 2);
  assert.equal(state.edges.length, 0);
  assert.equal(result.nodeId, 'gate-1');
  assert.equal(result.edgeId, 'edge-1');
  assert.deepEqual(result.state.edges[0], {
    id:'edge-1',source:'trigger',target:'gate-1',condition:'failure',label:'',
  });
});

test('moveNodes moves only selected nodes across the unbounded canvas', () => {
  let state = addNode(base(), {type:'delay',label:'Wait',config:{}}, {x:50,y:75});
  const moved = moveNodes(state, ['trigger','delay-1'], {x:-80,y:25});
  assert.deepEqual([moved.nodes.find(node=>node.id==='trigger').x,moved.nodes.find(node=>node.id==='trigger').y], [-80,25]);
  assert.deepEqual([moved.nodes.find(node=>node.id==='delay-1').x,moved.nodes.find(node=>node.id==='delay-1').y], [-30,100]);
  assert.deepEqual(moved.nodes.find(node=>node.id==='finish'), state.nodes.find(node=>node.id==='finish'));
});

test('duplicateNodes creates new IDs and copies internal edges only', () => {
  let state = addNode(base(), {type:'delay',label:'Wait',config:{}}, {x:100,y:80});
  state = addNode(state, {type:'gate',label:'Branch',config:{mode:'any'}}, {x:300,y:80});
  state = addEdge(state, 'delay-1', 'gate-1', 'success');
  state = addEdge(state, 'trigger', 'delay-1', 'always');
  const result = duplicateNodes(state, ['delay-1','gate-1'], {x:40,y:30});
  assert.deepEqual(result.nodeIds, ['delay-2','gate-2']);
  assert.deepEqual(
    result.state.nodes.filter(node=>result.nodeIds.includes(node.id)).map(node=>[node.id,node.x,node.y]),
    [['delay-2',140,110],['gate-2',340,110]],
  );
  assert.ok(result.state.edges.some(edge=>edge.source==='delay-2'&&edge.target==='gate-2'&&edge.condition==='success'));
  assert.ok(!result.state.edges.some(edge=>edge.source==='trigger'&&edge.target==='delay-2'));
});

test('replaceTrigger changes trigger type atomically while preserving graph identity', () => {
  let state = addNode(base(), {type:'delay',label:'Wait',config:{seconds:5}}, {x:240,y:20});
  state = addEdge(state, 'trigger', 'delay-1', 'always');
  const next = replaceTrigger(state, {
    type:'scheduled_trigger', label:'Scheduled Trigger', config:{offset_minutes:15},
  });
  assert.deepEqual(next.nodes[0], {
    id:'trigger',type:'scheduled_trigger',label:'Scheduled Trigger',x:0,y:0,
    disabled:false,config:{offset_minutes:15},
  });
  assert.deepEqual(next.edges, state.edges);
  assert.equal(state.nodes[0].type, 'manual_trigger');
});

test('trigger nodes cannot receive edges, be deleted, or be duplicated', () => {
  assert.equal(isTriggerType('event_start_trigger'), true);
  assert.equal(isTriggerType('delay'), false);
  assert.match(connectionError(base(), 'finish', 'trigger', 'always'), /trigger/i);
  assert.deepEqual(deleteSelection(base(), {kind:'node',id:'trigger'}), base());
  const duplicated = duplicateNodes(base(), ['trigger'], {x:20,y:20});
  assert.deepEqual(duplicated.state, base());
  assert.deepEqual(duplicated.nodeIds, []);
});
