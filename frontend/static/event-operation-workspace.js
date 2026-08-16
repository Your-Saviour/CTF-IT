const clone = value => JSON.parse(JSON.stringify(value));
const clamp = (value,min,max) => Math.min(max,Math.max(min,value));

export function createViewport(width, height) {
  return {x:0,y:0,zoom:1,width:Number(width)||0,height:Number(height)||0};
}

export function graphPoint(viewport, point) {
  return {x:(point.x-viewport.x)/viewport.zoom,y:(point.y-viewport.y)/viewport.zoom};
}

export function zoomAt(viewport, screenPoint, nextZoom) {
  const zoom=clamp(Number(nextZoom)||1,.35,2), anchor=graphPoint(viewport,screenPoint);
  return {...viewport,zoom,x:screenPoint.x-anchor.x*zoom,y:screenPoint.y-anchor.y*zoom};
}

export function fitViewport(nodes, bounds, padding=64) {
  const width=Number(bounds.width)||0,height=Number(bounds.height)||0;
  if(!nodes.length)return createViewport(width,height);
  const minX=Math.min(...nodes.map(node=>node.x)),minY=Math.min(...nodes.map(node=>node.y));
  const maxX=Math.max(...nodes.map(node=>node.x+180)),maxY=Math.max(...nodes.map(node=>node.y+86));
  const contentWidth=Math.max(1,maxX-minX),contentHeight=Math.max(1,maxY-minY);
  const zoom=clamp(Math.min((width-padding*2)/contentWidth,(height-padding*2)/contentHeight),.35,2);
  return {width,height,zoom,x:(width-contentWidth*zoom)/2-minX*zoom,y:(height-contentHeight*zoom)/2-minY*zoom};
}

export function nodeTargetLabel(node, catalogue) {
  if(!['ability','objective'].includes(node?.type))return '';
  const targetId=node.config?.target_vm_id;
  return catalogue?.targets?.find(target=>target.id===targetId)?.name||'Unknown target';
}

export function createHistory(initial, limit=50) {
  let past=[clone(initial)],future=[];
  const current=()=>clone(past.at(-1));
  return {
    current,
    commit(value){const next=clone(value);if(JSON.stringify(past.at(-1))===JSON.stringify(next))return current();past.push(next);if(past.length>limit)past.shift();future=[];return current()},
    undo(){if(past.length<2)return null;future.push(past.pop());return current()},
    redo(){if(!future.length)return null;past.push(future.pop());return current()},
  };
}
