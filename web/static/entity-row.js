// Delegation also covers rows replaced by HTMX state updates.
(() => {
  document.body.addEventListener('click', event => {
    const card = event.target.closest('.entity-card');
    if (!card || event.defaultPrevented || event.button !== 0 ||
        event.ctrlKey || event.metaKey || event.shiftKey || event.altKey ||
        event.target.closest('button, a, input, select, textarea, label, form')) return;
    if (window.getSelection()?.toString()) return;
    card.querySelector('.entity-name')?.click();
  });

  function start(title) {
    const text = title.querySelector('.entity-name-text');
    const distance = text.scrollWidth - title.clientWidth;
    if (distance <= 0) {
      title.classList.remove('is-scrolling');
      return;
    }
    title.style.setProperty('--entity-title-offset', '-' + distance + 'px');
    title.style.setProperty('--entity-title-duration', Math.max(3, distance / 35) + 's');
    title.classList.add('is-scrolling');
  }

  document.body.addEventListener('pointerover', event => {
    const title = event.target.closest('.entity-name');
    if (title && !title.contains(event.relatedTarget)) start(title);
  });
  document.body.addEventListener('pointerout', event => {
    const title = event.target.closest('.entity-name');
    if (title && !title.contains(event.relatedTarget) && !title.matches(':focus-visible')) {
      title.classList.remove('is-scrolling');
    }
  });
  document.body.addEventListener('focusin', event => {
    const title = event.target.closest('.entity-name');
    if (title && title.matches(':focus-visible')) start(title);
  });
  document.body.addEventListener('focusout', event => {
    const title = event.target.closest('.entity-name');
    if (title && !title.matches(':hover')) title.classList.remove('is-scrolling');
  });
})();
