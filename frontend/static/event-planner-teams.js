export const TEAM_ROLE_OPTIONS = Object.freeze([
  Object.freeze({value: 'blue', label: 'Blue team'}),
  Object.freeze({value: 'red', label: 'Red team'}),
]);

export function teamFieldForNode(node) {
  if (node?.type === 'zone') {
    return {name: 'team', label: 'Team role', value: node.value.team, options: TEAM_ROLE_OPTIONS};
  }
  if (node?.type === 'firewall-zone') {
    return {
      name: 'firewall_team', label: 'Team role', value: node.value.firewall_team ?? 'blue', options: TEAM_ROLE_OPTIONS,
    };
  }
  return null;
}

export function teamForNode(node) {
  return teamFieldForNode(node)?.value ?? null;
}
