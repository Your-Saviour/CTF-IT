export function clampOffset(minute, maxMinutes) {
  return Math.max(0, Math.min(maxMinutes, Math.round(minute)));
}

export function effectiveMinutes(injects, phases, operations, fallback = 60) {
  const candidates = [
    ...(injects || []).map((i) => (i.offset_minutes || 0) + 15),
    ...(phases || []).map((p) => p.end_offset_minutes || 0),
    ...(operations || []).map((o) => (o.offset_minutes || 0) + (o.duration || 0)),
    fallback,
  ];
  return Math.max(...candidates);
}
