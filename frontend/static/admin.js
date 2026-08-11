(function () {
  const sidebar = document.querySelector('.admin-sidebar');
  const menu = document.querySelector('[data-menu-toggle]');
  const close = () => { sidebar?.classList.remove('open'); menu?.setAttribute('aria-expanded', 'false'); };
  menu?.addEventListener('click', () => { const open = sidebar.classList.toggle('open'); menu.setAttribute('aria-expanded', String(open)); if(open) sidebar.querySelector('a')?.focus(); });
  document.querySelector('[data-menu-close]')?.addEventListener('click', () => { close(); menu?.focus(); });
  sidebar?.querySelectorAll('a').forEach(link => link.addEventListener('click', close));
  document.addEventListener('keydown', e => { if(e.key === 'Escape') { close(); document.querySelectorAll('.drawer-backdrop.open').forEach(x => x.classList.remove('open')); } });
  document.querySelectorAll('[data-drawer-open]').forEach(button => button.addEventListener('click', () => {
    const drawer = document.getElementById(button.dataset.drawerOpen); drawer?.classList.add('open'); drawer?.querySelector('input,button,select,textarea')?.focus();
  }));
  document.querySelectorAll('[data-drawer-close]').forEach(button => button.addEventListener('click', () => button.closest('.drawer-backdrop')?.classList.remove('open')));
  document.querySelectorAll('.drawer-backdrop').forEach(backdrop => backdrop.addEventListener('click', e => { if(e.target === backdrop) backdrop.classList.remove('open'); }));
})();
