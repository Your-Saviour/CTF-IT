export const clone = value => structuredClone(value);
export const slugify = value => String(value || '').toLowerCase().trim().replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'') || 'item';

export function normalizeClientInfrastructure(value){
  const result=clone(value);
  for(const site of result.sites||[]) for(const zone of site.zones||[]){
    const used=new Set(), rows=[];
    for(const endpoint of zone.endpoints||[]){
      const count=endpoint.count==null?1:Number(endpoint.count);
      for(let index=1;index<=count;index++){
        const row=clone(endpoint); delete row.count;
        let key=endpoint.count==null?row.key:`${row.key}_${index}`; let candidate=key, suffix=2;
        while(used.has(candidate)) candidate=`${key}_${suffix++}`;
        row.key=candidate; row.name ||= candidate.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
        used.add(candidate); rows.push(row);
      }
    }
    zone.endpoints=rows;
  }
  return result;
}

export function nodeIndex(infrastructure){
  const map=new Map([['gateway',{type:'gateway',value:infrastructure.vpn_gateway,parent:null,path:'vpn_gateway'}]]);
  (infrastructure.sites||[]).forEach((site,si)=>{const sid=`site:${site.key}`;map.set(sid,{type:'site',value:site,parent:'gateway',path:`sites[${si}]`});map.set(`firewall:${site.key}`,{type:'firewall',value:site.firewall,parent:sid,path:`sites[${si}].firewall`});(site.zones||[]).forEach((zone,zi)=>{const zid=`zone:${site.key}/${zone.key}`;map.set(zid,{type:'zone',value:zone,parent:sid,path:`sites[${si}].zones[${zi}]`});(zone.endpoints||[]).forEach((vm,vi)=>map.set(`vm:${site.key}/${zone.key}/${vm.key}`,{type:'vm',value:vm,parent:zid,path:`sites[${si}].zones[${zi}].endpoints[${vi}]`}));});}); return map;
}

export function validateClientInfrastructure(value,catalogues={}){
  const errors=[], bases=new Set((catalogues.bases||[]).map(row=>row.id));
  const add=(path,nodeId,message)=>errors.push({path,nodeId,message});
  if(!value.vpn_gateway) add('vpn_gateway','gateway','VPN gateway is required');
  if(!(value.sites||[]).length) add('sites','gateway','Add at least one site');
  const siteKeys=new Set();
  for(const site of value.sites||[]){const sid=`site:${site.key}`;if(!site.name?.trim())add('site.name',sid,'Site name is required');if(siteKeys.has(site.key))add('site.key',sid,'Site key must be unique');siteKeys.add(site.key);if(!(site.zones||[]).length)add('site.zones',sid,'Add at least one zone');const zoneKeys=new Set();for(const zone of site.zones||[]){const zid=`zone:${site.key}/${zone.key}`;if(!zone.name?.trim())add('zone.name',zid,'Zone name is required');if(zoneKeys.has(zone.key))add('zone.key',zid,'Zone key must be unique');zoneKeys.add(zone.key);const vmKeys=new Set();for(const vm of zone.endpoints||[]){const vid=`vm:${site.key}/${zone.key}/${vm.key}`;if(!vm.name?.trim())add('vm.name',vid,'VM name is required');if(vmKeys.has(vm.key))add('vm.key',vid,'VM key must be unique');vmKeys.add(vm.key);if(bases.size&&!bases.has(vm.base_type))add('vm.base_type',vid,'Choose an available base type');}}}
  return errors;
}

export function createPlannerStore(initial){let state=clone(initial), listeners=[];return{get:()=>state,set(next){state=clone(next);listeners.forEach(fn=>fn(state));},update(fn){this.set(fn(clone(state)));},subscribe(fn){listeners.push(fn);return()=>listeners=listeners.filter(row=>row!==fn);}};}
