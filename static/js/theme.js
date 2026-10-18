// Runs before first paint so a saved theme choice does not flash the other theme.
(function () {
  try {
    var t = localStorage.getItem('zp_theme');
    if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  } catch (e) { /* storage blocked: follow the system theme */ }
})();
