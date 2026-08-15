export function addressFieldForNode(node) {
  if (node?.type === 'zone') {
    return {name: 'address_range', label: 'Address range', value: node.value.address_range ?? ''};
  }
  if (node?.type === 'vm') {
    return {name: 'address', label: 'Address', value: node.value.address ?? ''};
  }
  return null;
}

export function addressAnnotationForNode(node) {
  const field = addressFieldForNode(node);
  return field && field.value !== '' ? field.value : null;
}
