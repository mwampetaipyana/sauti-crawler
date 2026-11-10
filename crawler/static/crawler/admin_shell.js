// Sidebar and account menu for the admin shell.
(function () {
  const root = document.documentElement;
  const wide = window.matchMedia('(min-width: 960px)');
  const nav = document.getElementById('side-nav');
  const workspace = document.querySelector('.workspace');
  const opener = document.querySelector('.toolbar [data-nav-toggle]');

  function navShown() {
    return wide.matches ? !root.classList.contains('nav-closed') : root.classList.contains('nav-open');
  }

  // A sidebar that is off screen must also be out of the tab order and hidden from screen readers.
  // On small screens the open sidebar covers the page, so the page behind it is shut off instead.
  function sync() {
    if (!nav) return;
    const shown = navShown();
    nav.inert = !shown;
    if (workspace) workspace.inert = shown && !wide.matches;
    if (opener) opener.setAttribute('aria-expanded', String(shown));
  }

  function closeOverlay() {
    root.classList.remove('nav-open');
    sync();
    if (opener) opener.focus();
  }

  document.querySelectorAll('[data-nav-toggle]').forEach((control) => {
    control.addEventListener('click', () => {
      if (wide.matches) {
        // On wide screens the sidebar sits beside the page; remember the choice.
        const closed = root.classList.toggle('nav-closed');
        localStorage.setItem('sauti-nav', closed ? 'closed' : 'open');
        sync();
      } else if (root.classList.contains('nav-open')) {
        closeOverlay();
      } else {
        // On small screens it slides over the page; move focus into it.
        root.classList.add('nav-open');
        sync();
        const first = nav && nav.querySelector('.side-close, a');
        if (first) first.focus();
      }
    });
  });
  wide.addEventListener('change', () => { root.classList.remove('nav-open'); sync(); });
  sync();

  const menu = document.querySelector('.user-menu');
  document.addEventListener('click', (event) => {
    if (menu && menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (menu && menu.open) { menu.open = false; menu.querySelector('summary').focus(); }
    if (root.classList.contains('nav-open')) closeOverlay();
  });

  // Show or hide the password on sign-in forms.
  document.querySelectorAll('[data-reveal]').forEach((button) => {
    const field = document.getElementById(button.dataset.reveal);
    button.addEventListener('click', () => {
      const hidden = field.type === 'password';
      field.type = hidden ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(hidden));
      button.setAttribute('aria-label', hidden ? 'Ficha nenosiri' : 'Onyesha nenosiri');
      button.querySelector('use').setAttribute('href', hidden ? '#i-eye-slash' : '#i-eye');
    });
  });
})();
