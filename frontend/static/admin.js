(function () {
  const sidebar = document.querySelector('.admin-sidebar');
  const menu = document.querySelector('[data-menu-toggle]');
  const focusableSelector = [
    'a[href]', 'button:not([disabled])', 'input:not([disabled])',
    'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])'
  ].join(',');

  function closeMenu(restoreFocus) {
    const wasOpen = sidebar?.classList.contains('open');
    sidebar?.classList.remove('open');
    menu?.setAttribute('aria-expanded', 'false');
    if (restoreFocus && wasOpen) menu?.focus();
  }

  function drawerFocusables(drawer) {
    return [...drawer.querySelectorAll(focusableSelector)].filter(element =>
      !element.hidden && element.getAttribute('aria-hidden') !== 'true'
    );
  }

  function openDrawer(drawer, trigger, preferredFocus) {
    if (!drawer) return;
    if (trigger) drawer._returnFocus = trigger;
    drawer.classList.add('open');
    requestAnimationFrame(() => {
      const firstFormControl = drawer.querySelector('form input:not([disabled]), form select:not([disabled]), form textarea:not([disabled]), form button:not([disabled])');
      (preferredFocus || firstFormControl || drawerFocusables(drawer)[0])?.focus();
    });
  }

  function closeDrawer(drawer, restoreFocus = true) {
    if (!drawer?.classList.contains('open')) return;
    drawer.classList.remove('open');
    if (restoreFocus && drawer._returnFocus?.isConnected) drawer._returnFocus.focus();
  }

  window.openAdminDrawer = openDrawer;
  window.closeAdminDrawer = closeDrawer;

  menu?.addEventListener('click', () => {
    const open = sidebar.classList.toggle('open');
    menu.setAttribute('aria-expanded', String(open));
    if (open) sidebar.querySelector('a')?.focus();
  });
  document.querySelector('[data-menu-close]')?.addEventListener('click', () => closeMenu(true));
  sidebar?.querySelectorAll('a').forEach(link => link.addEventListener('click', () => closeMenu(false)));

  document.querySelectorAll('[data-drawer-open]').forEach(button => button.addEventListener('click', () => {
    const drawer = document.getElementById(button.dataset.drawerOpen);
    openDrawer(drawer, button);
  }));
  document.querySelectorAll('[data-drawer-close]').forEach(button => button.addEventListener('click', () => {
    closeDrawer(button.closest('.drawer-backdrop'));
  }));
  document.querySelectorAll('.drawer-backdrop').forEach(backdrop => backdrop.addEventListener('click', event => {
    if (event.target === backdrop) closeDrawer(backdrop);
  }));

  document.addEventListener('keydown', event => {
    const drawer = document.querySelector('.drawer-backdrop.open');
    if (event.key === 'Escape') {
      if (drawer) {
        event.preventDefault();
        closeDrawer(drawer);
      } else if (sidebar?.classList.contains('open')) {
        event.preventDefault();
        closeMenu(true);
      }
      return;
    }
    if (event.key === 'Tab' && drawer) {
      const controls = drawerFocusables(drawer);
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
})();
