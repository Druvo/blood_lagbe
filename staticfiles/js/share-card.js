/* Draws shareable 1080x1080 social cards (badge / urgent request) on a
   canvas, then offers download or the phone's native share sheet.
   Drawn in the browser so Bangla names use the visitor's own fonts. */
(function () {
  'use strict';

  var SIZE = 1080;
  var FONT = '"Montserrat", "Noto Sans Bengali", "Segoe UI", Arial, sans-serif';

  function font(weight, px) {
    return weight + ' ' + px + 'px ' + FONT;
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drop(ctx, cx, top, w, h, color) {
    ctx.beginPath();
    ctx.moveTo(cx, top);
    ctx.bezierCurveTo(cx + w * 0.55, top + h * 0.38, cx + w * 0.55, top + h, cx, top + h);
    ctx.bezierCurveTo(cx - w * 0.55, top + h, cx - w * 0.55, top + h * 0.38, cx, top);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
  }

  // Shrinks the font until the text fits maxWidth, then draws it centred.
  function fitText(ctx, text, x, y, maxWidth, weight, startPx, color) {
    var px = startPx;
    ctx.font = font(weight, px);
    while (ctx.measureText(text).width > maxWidth && px > 20) {
      px -= 2;
      ctx.font = font(weight, px);
    }
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.fillText(text, x, y);
  }

  function wrapLines(ctx, text, maxWidth) {
    var words = String(text).split(/\s+/);
    var lines = [];
    var line = '';
    words.forEach(function (word) {
      var test = line ? line + ' ' + word : word;
      if (ctx.measureText(test).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = test;
      }
    });
    if (line) lines.push(line);
    return lines;
  }

  function background(ctx) {
    var g = ctx.createLinearGradient(0, 0, SIZE, SIZE);
    g.addColorStop(0, '#e50202');
    g.addColorStop(1, '#6b0000');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, SIZE, SIZE);
    ctx.fillStyle = '#ffffff';
    roundRect(ctx, 60, 60, SIZE - 120, SIZE - 120, 48);
    ctx.fill();
  }

  function footer(ctx, line) {
    ctx.textAlign = 'center';
    ctx.fillStyle = '#999999';
    ctx.font = font(500, 30);
    ctx.fillText(line, SIZE / 2, 935);
    ctx.fillStyle = '#e50202';
    ctx.font = font(700, 34);
    ctx.fillText(window.location.host || 'bloodlagbe.com', SIZE / 2, 985);
  }

  function drawBadge(ctx, d) {
    background(ctx);
    ctx.textAlign = 'center';
    ctx.fillStyle = '#e50202';
    ctx.font = font(700, 34);
    ctx.fillText('B L O O D   L A G B E', SIZE / 2, 150);

    drop(ctx, SIZE / 2, 190, 230, 290, d.tierColor || '#cd7f32');
    ctx.fillStyle = '#ffffff';
    ctx.font = font(700, 100);
    ctx.textAlign = 'center';
    ctx.fillText(String(d.donations), SIZE / 2, 415);

    fitText(ctx, d.name, SIZE / 2, 565, 860, 700, 68, '#111111');
    fitText(ctx, d.tier || 'Aspiring Donor', SIZE / 2, 630, 860, 700, 42, d.tierColor || '#999999');
    if (d.hero) {
      fitText(ctx, d.hero, SIZE / 2, 685, 860, 600, 32, d.heroColor || '#e50202');
    }

    var stats = [
      ['UP TO ' + d.lives, 'EST. PEOPLE HELPED'],
      [String(d.streak), 'CYCLE STREAK'],
      [String(d.points), 'POINTS']
    ];
    stats.forEach(function (s, i) {
      var x = 210 + i * 330;
      ctx.textAlign = 'center';
      ctx.fillStyle = '#111111';
      ctx.font = font(700, s[0].length > 6 ? 52 : 76);
      ctx.fillText(s[0], x, 815);
      ctx.fillStyle = '#888888';
      ctx.font = font(600, 24);
      ctx.fillText(s[1], x, 860);
    });

    footer(ctx, 'Donate blood. Save lives.');
  }

  function drawRequest(ctx, d) {
    background(ctx);
    ctx.fillStyle = '#e50202';
    roundRect(ctx, 60, 60, SIZE - 120, 130, 48);
    ctx.fill();
    ctx.fillRect(60, 130, SIZE - 120, 60);
    ctx.textAlign = 'center';
    ctx.fillStyle = '#ffffff';
    ctx.font = font(700, 56);
    ctx.fillText('URGENT: BLOOD NEEDED', SIZE / 2, 150);

    drop(ctx, SIZE / 2, 240, 260, 330, '#e50202');
    ctx.fillStyle = '#ffffff';
    ctx.font = font(700, 120);
    ctx.textAlign = 'center';
    ctx.fillText(d.bloodGroup, SIZE / 2, 470);

    ctx.font = font(700, 54);
    var lines = wrapLines(ctx, d.hospital, 860).slice(0, 2);
    ctx.fillStyle = '#111111';
    var y = 660;
    lines.forEach(function (line) {
      ctx.fillText(line, SIZE / 2, y);
      y += 64;
    });
    fitText(ctx, d.district, SIZE / 2, y + 6, 860, 600, 40, '#555555');

    var bags = d.remaining + (d.remaining === 1 ? ' bag' : ' bags') + ' still needed';
    fitText(ctx, bags + ' by ' + d.neededBy, SIZE / 2, y + 78, 860, 700, 38, '#e50202');

    footer(ctx, 'Can you help? Open the link and tap "I can help".');
  }

  function render(data) {
    var canvas = document.createElement('canvas');
    canvas.width = SIZE;
    canvas.height = SIZE;
    var ctx = canvas.getContext('2d');
    if (data.type === 'request') {
      drawRequest(ctx, data);
    } else {
      drawBadge(ctx, data);
    }
    return canvas;
  }

  function loadFonts() {
    if (!document.fonts || !document.fonts.load) return Promise.resolve();
    return Promise.all([
      document.fonts.load('700 60px "Montserrat"'),
      document.fonts.load('600 30px "Montserrat"')
    ]).catch(function () {});
  }

  function toBlob(canvas) {
    return new Promise(function (resolve) {
      canvas.toBlob(resolve, 'image/png');
    });
  }

  function download(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function flash(button, text) {
    var original = button.getAttribute('data-label') || button.innerHTML;
    button.setAttribute('data-label', original);
    button.textContent = text;
    setTimeout(function () { button.innerHTML = original; }, 1800);
  }

  function init(root) {
    var data = JSON.parse(document.getElementById(root.dataset.shareData).textContent);
    var filename = root.dataset.shareFilename || 'blood-lagbe.png';
    var shareUrl = root.dataset.shareUrl;
    var shareText = root.dataset.shareText;

    function makeBlob() {
      return loadFonts().then(function () { return toBlob(render(data)); });
    }

    root.addEventListener('click', function (event) {
      var button = event.target.closest('[data-share-action]');
      if (!button) return;
      var action = button.dataset.shareAction;

      if (action === 'download') {
        makeBlob().then(function (blob) { download(blob, filename); });
      } else if (action === 'native') {
        makeBlob().then(function (blob) {
          var file = new File([blob], filename, { type: 'image/png' });
          if (navigator.canShare && navigator.canShare({ files: [file] })) {
            return navigator.share({ files: [file], text: shareText, url: shareUrl });
          }
          if (navigator.share) {
            return navigator.share({ text: shareText, url: shareUrl });
          }
          download(blob, filename);
          flash(button, 'Image saved');
        }).catch(function () {});
      } else if (action === 'copy') {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(shareUrl).then(function () {
            flash(button, 'Link copied');
          });
        } else {
          window.prompt('Copy this link:', shareUrl);
        }
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-share-root]').forEach(init);
  });
})();
