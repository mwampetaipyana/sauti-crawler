// The story list: each title opens its story in place. Without this script every story stays open.
(function () {
  document.querySelectorAll('.story-toggle').forEach((button) => {
    const story = button.closest('.story');
    button.setAttribute('aria-expanded', String(story.classList.contains('is-open')));
    button.addEventListener('click', () => {
      button.setAttribute('aria-expanded', String(story.classList.toggle('is-open')));
    });
  });

  // Choosing a sort order applies it straight away.
  document.querySelectorAll('[data-autosubmit]').forEach((select) => {
    select.addEventListener('change', () => select.form.submit());
  });
})();
