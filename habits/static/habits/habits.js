/* Native share for the year card. Everything else works without JS. */
(function () {
  var button = document.querySelector('[data-hb-share]');
  if (!button) return;
  if (!navigator.share) { button.hidden = true; return; }
  button.hidden = false;
  button.addEventListener('click', function () {
    navigator.share({
      title: button.getAttribute('data-title') || document.title,
      text: button.getAttribute('data-text') || '',
      url: button.getAttribute('data-url') || window.location.href
    }).catch(function () {});
  });
})();
