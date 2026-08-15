export const PLANNER_THEME_SWATCHES = Object.freeze([
  '#06b6d4',
  '#2563eb',
  '#7c3aed',
  '#a855f7',
  '#db2777',
  '#dc2626',
  '#ea580c',
  '#16a34a',
]);

export function renderColourControl({explicitColor, effectiveColor, inherited, disabled}) {
  const current = explicitColor || effectiveColor || '#06b6d4';
  const status = explicitColor ? 'Custom colour' : inherited ? 'Inherited from zone' : 'Automatic colour';
  const disabledAttribute = disabled ? ' disabled' : '';
  const swatches = PLANNER_THEME_SWATCHES.map(color => `
    <button type="button" class="theme-swatch" data-theme-swatch="${color}" aria-label="Use ${color}" aria-pressed="${explicitColor === color}" style="--swatch:${color}"${disabledAttribute}></button>
  `).join('');
  return `<fieldset class="theme-control">
    <legend>Colour</legend>
    <div class="theme-swatches" aria-label="Colour palette">${swatches}</div>
    <label class="theme-custom">Custom colour<input type="color" name="theme_color" value="${current}"${disabledAttribute}></label>
    <div class="theme-control-footer"><span class="theme-status">${status}</span><button type="button" class="theme-reset" data-theme-reset${disabledAttribute}>Reset</button></div>
  </fieldset>`;
}
