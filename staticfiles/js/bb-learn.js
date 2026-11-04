/* Learn page: eligibility pre-check and blood group picker.
 * Progressive enhancement: without JS the criteria list and the full table
 * stay visible. The result is a soft recommendation, never clearance. */
(function () {
  'use strict';

  // ---- Eligibility checker ----
  var box = document.querySelector('[data-checker]');
  if (box) {
    box.hidden = false;
    var form = box.querySelector('[data-checker-form]');
    var out = box.querySelector('[data-checker-result]');
    var intervalDays = parseInt(box.getAttribute('data-interval-days'), 10) || 120;
    var intervalLabel = box.getAttribute('data-interval-label') || (intervalDays + ' days');

    var neverBox = form.elements.never;
    var lastInput = form.elements.last;
    var today = new Date();
    lastInput.max = today.getFullYear() + '-' + ('0' + (today.getMonth() + 1)).slice(-2) + '-' + ('0' + today.getDate()).slice(-2);
    neverBox.addEventListener('change', function () { lastInput.disabled = neverBox.checked; });
    lastInput.addEventListener('input', function () { if (lastInput.value) neverBox.checked = false; });
    lastInput.disabled = neverBox.checked;

    var esc = function (t) { var d = document.createElement('div'); d.textContent = t; return d.innerHTML; };

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var stop = [], ask = [], missing = [];
      var age = parseFloat(form.elements.age.value);
      var weight = parseFloat(form.elements.weight.value);
      var val = function (n) { return form.elements[n].value; };

      if (isNaN(age)) missing.push('your age');
      else if (age < 18 || age > 60) stop.push('Most blood banks accept donors aged 18 to 60. Ask yours about its limits.');
      if (isNaN(weight)) missing.push('your weight');
      else if (weight < 45) stop.push('Most blood banks ask for a weight of at least 45 kg.');
      if (val('pregnant') === 'yes') stop.push('During pregnancy and the months after birth, donors are usually asked to wait. Ask the blood bank.');

      if (!neverBox.checked) {
        if (!lastInput.value) missing.push('the date of your last donation (or tick "never donated")');
        else {
          var parts = lastInput.value.split('-');
          var last = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
          var days = Math.floor((new Date(today.getFullYear(), today.getMonth(), today.getDate()) - last) / 86400000);
          if (days < 0) missing.push('a last donation date that is not in the future');
          else if (days < intervalDays) {
            stop.push('Your last donation was ' + days + ' day' + (days === 1 ? '' : 's') + ' ago. We use a gap of ' + intervalLabel + ' as a reminder, so about ' + (intervalDays - days) + ' more day' + ((intervalDays - days) === 1 ? '' : 's') + ' to wait.');
          }
        }
      }

      if (val('ill') === 'yes') ask.push('Being unwell or recently ill often means waiting until you are fully better.');
      if (val('travel') === 'yes') ask.push('Recent travel to some areas can mean a waiting period. Tell the staff where you went.');
      if (val('tattoo') === 'yes') ask.push('Many blood banks ask you to wait some months after a tattoo or piercing.');
      if (val('meds') === 'yes') ask.push('Some medicines mean waiting or not donating. Tell the staff what you take.');

      var tone, title, intro, items;
      if (missing.length) {
        tone = 'ask'; title = 'A little more information, please';
        intro = 'Please fill in ' + missing.join(', ') + '.'; items = [];
      } else if (stop.length) {
        tone = 'no'; title = 'Probably not yet'; intro = 'Based on your answers:'; items = stop.concat(ask);
      } else if (ask.length) {
        tone = 'ask'; title = 'Check with the blood bank first'; intro = 'You may be able to donate, but please mention:'; items = ask;
      } else {
        tone = 'good'; title = 'You may be able to donate'; intro = 'Nothing in your answers rules you out.'; items = [];
      }

      var html = '<h3 class="bb-h4">' + esc(title) + '</h3><p>' + esc(intro) + '</p>';
      if (items.length) html += '<ul>' + items.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>';
      html += '<p class="pg-result__bank"><strong>The blood bank decides.</strong> This is a soft recommendation, not medical clearance. Staff will check you on the day.</p>';
      out.innerHTML = html;
      out.setAttribute('data-tone', tone);
      out.hidden = false;
      out.focus({ preventScroll: true });
      if (out.scrollIntoView) out.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }

  // ---- Blood group picker ----
  var pick = document.querySelector('[data-pick]');
  var table = document.querySelector('[data-compat]');
  if (pick && table) {
    pick.classList.add('is-ready');
    var pickOut = pick.querySelector('[data-pick-out]');
    var chips = pick.querySelectorAll('[data-pick-group]');
    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        var group = chip.getAttribute('data-pick-group');
        chips.forEach(function (c) {
          var on = c === chip;
          c.classList.toggle('bb-chip--active', on);
          c.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        table.querySelectorAll('tbody tr').forEach(function (row) {
          row.classList.toggle('is-picked', row.getAttribute('data-row') === group);
          if (row.getAttribute('data-row') === group) {
            pickOut.innerHTML = '';
            var a = document.createElement('p');
            var b = document.createElement('p');
            a.innerHTML = '<strong>' + group + '</strong> can usually receive red cells from: ';
            a.appendChild(document.createTextNode(row.getAttribute('data-get')));
            b.innerHTML = '<strong>' + group + '</strong> can usually give red cells to: ';
            b.appendChild(document.createTextNode(row.getAttribute('data-give')));
            pickOut.appendChild(a); pickOut.appendChild(b);
            pickOut.hidden = false;
          }
        });
      });
    });
  }
})();
