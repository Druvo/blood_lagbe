// Narrows the place <select> to the chosen district and only shows the
// "other place" text box when no listed place is picked. Progressive
// enhancement: without JS all places and the text box stay visible and the
// server validates the pair.
(function () {
  var district = document.querySelector('select[name="district"]');
  var place = document.querySelector('select[name="place"]');
  var textGroup = document.querySelector('.js-place-text');
  if (!district || !place) return;

  var options = Array.prototype.slice.call(place.options);
  var blank = options.filter(function (o) { return !o.value; })[0];
  var listed = options.filter(function (o) { return o.value; });

  function renderPlaces() {
    var chosen = district.value;
    var keep = place.value;
    while (place.firstChild) place.removeChild(place.firstChild);
    if (blank) place.appendChild(blank);
    listed.forEach(function (o) {
      if (!chosen || o.getAttribute('data-district') === chosen) {
        place.appendChild(o);
      }
    });
    place.value = Array.prototype.some.call(place.options, function (o) {
      return o.value === keep;
    }) ? keep : '';
    toggleText();
  }

  function toggleText() {
    if (textGroup) textGroup.style.display = place.value ? 'none' : '';
  }

  district.addEventListener('change', renderPlaces);
  place.addEventListener('change', toggleText);
  renderPlaces();
})();
