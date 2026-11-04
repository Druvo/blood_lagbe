/* Draws shareable 1080x1080 social cards (donor badge / urgent request) on a
   canvas, then offers download, copy-link or the phone's native share sheet.
   Drawn in the browser so Bangla names use the visitor's own fonts, and the
   droplet-with-heart mark is drawn with canvas paths (no image to load).
   The card data never contains a phone number. */
(function () {
  'use strict';

  var SIZE = 1080;
  var BANGLA = '"Hind Siliguri", "Noto Sans Bengali", "Nirmala UI", "Vrinda"';
  var FONT_DISPLAY = '"Plus Jakarta Sans", ' + BANGLA + ', "Inter", "Segoe UI", system-ui, Arial, sans-serif';
  var FONT_BODY = '"Inter", ' + BANGLA + ', "Segoe UI", system-ui, Arial, sans-serif';

  var CRIMSON = ['#ff4d6d', '#d8213a', '#6e0a1c'];
  var INK = '#101828';
  var MUTED = '#667085';

  // The brand mark, in a 64x64 box (same paths as bloodbond-mark.svg).
  var DROP_PATH = 'M32 3C32 3 11 26 11 40a21 21 0 0 0 42 0C53 26 32 3 32 3Z';
  var HEART_PATH = 'M32 51c-8-5.5-12.5-9.6-12.5-14.2a6.4 6.4 0 0 1 12.5-2 6.4 6.4 0 0 1 12.5 2C44.5 41.4 40 45.5 32 51Z';
  var SHINE_PATH = 'M20 22c3-5 7-10 12-16';

  function display(weight, px) { return weight + ' ' + px + 'px ' + FONT_DISPLAY; }
  function body(weight, px) { return weight + ' ' + px + 'px ' + FONT_BODY; }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  /* The BloodBond mark. `size` is the drawn height of the 64-unit box.
     `fill` is a CSS colour; when omitted the brand gradient is used. */
  function mark(ctx, x, y, size, fill) {
    var k = size / 64;
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(k, k);
    var drop = new Path2D(DROP_PATH);
    if (fill) {
      ctx.fillStyle = fill;
    } else {
      var g = ctx.createLinearGradient(12, 4, 52, 60);
      g.addColorStop(0, '#ff5d7a');
      g.addColorStop(0.55, '#d8213a');
      g.addColorStop(1, '#8d1128');
      ctx.fillStyle = g;
    }
    ctx.fill(drop);
    ctx.strokeStyle = 'rgba(255,255,255,0.35)';
    ctx.lineWidth = 3;
    ctx.lineCap = 'round';
    ctx.stroke(new Path2D(SHINE_PATH));
    ctx.fillStyle = '#ffffff';
    ctx.fill(new Path2D(HEART_PATH));
    ctx.restore();
  }

  /* A large droplet filled with one colour, no heart (room for a number). */
  function bigDrop(ctx, cx, top, height, color) {
    var k = height / 58;           // the drop spans y 3..61 in the 64 box
    ctx.save();
    ctx.translate(cx - 32 * k, top - 3 * k);
    ctx.scale(k, k);
    var g = ctx.createLinearGradient(14, 4, 50, 60);
    g.addColorStop(0, shade(color, 0.22));
    g.addColorStop(1, shade(color, -0.22));
    ctx.fillStyle = g;
    ctx.shadowColor = 'rgba(16,24,40,0.25)';
    ctx.shadowBlur = 30;
    ctx.shadowOffsetY = 12;
    ctx.fill(new Path2D(DROP_PATH));
    ctx.shadowColor = 'transparent';
    ctx.strokeStyle = 'rgba(255,255,255,0.4)';
    ctx.lineWidth = 2.6;
    ctx.lineCap = 'round';
    ctx.stroke(new Path2D(SHINE_PATH));
    ctx.restore();
  }

  // Lightens (amount > 0) or darkens (< 0) a #rrggbb colour; others pass through.
  function shade(hex, amount) {
    var m = /^#([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return hex || '#d8213a';
    var n = parseInt(m[1], 16);
    var out = [n >> 16, (n >> 8) & 255, n & 255].map(function (c) {
      var v = amount > 0 ? c + (255 - c) * amount : c * (1 + amount);
      return Math.max(0, Math.min(255, Math.round(v)));
    });
    return 'rgb(' + out.join(',') + ')';
  }

  // Shrinks the font until the text fits maxWidth, then draws it centred.
  function fitText(ctx, text, x, y, maxWidth, fontFn, weight, startPx, color) {
    var px = startPx;
    ctx.font = fontFn(weight, px);
    while (ctx.measureText(text).width > maxWidth && px > 20) {
      px -= 2;
      ctx.font = fontFn(weight, px);
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

  function siteName(d) { return (d && d.siteName) || 'BloodBond'; }

  function background(ctx) {
    var g = ctx.createLinearGradient(0, 0, SIZE, SIZE);
    g.addColorStop(0, CRIMSON[0]);
    g.addColorStop(0.5, CRIMSON[1]);
    g.addColorStop(1, CRIMSON[2]);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, SIZE, SIZE);
    // Soft glows for depth.
    [[900, 140, 380], [120, 960, 340]].forEach(function (c) {
      var r = ctx.createRadialGradient(c[0], c[1], 0, c[0], c[1], c[2]);
      r.addColorStop(0, 'rgba(255,255,255,0.22)');
      r.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = r;
      ctx.fillRect(0, 0, SIZE, SIZE);
    });
    ctx.save();
    ctx.shadowColor = 'rgba(40,0,10,0.35)';
    ctx.shadowBlur = 50;
    ctx.shadowOffsetY = 20;
    ctx.fillStyle = '#ffffff';
    roundRect(ctx, 60, 60, SIZE - 120, SIZE - 120, 56);
    ctx.fill();
    ctx.restore();
  }

  // Brand lockup: mark + site name, centred on cx.
  function lockup(ctx, d, cx, y) {
    var name = siteName(d);
    ctx.font = display(800, 44);
    var w = ctx.measureText(name).width;
    var markSize = 64;
    var gap = 14;
    var total = markSize + gap + w;
    var left = cx - total / 2;
    mark(ctx, left, y - 46, markSize);
    ctx.textAlign = 'left';
    ctx.fillStyle = INK;
    ctx.fillText(name, left + markSize + gap, y);
    ctx.textAlign = 'center';
  }

  function footer(ctx, d, line) {
    ctx.textAlign = 'center';
    ctx.fillStyle = MUTED;
    ctx.font = body(500, 30);
    fitText(ctx, line, SIZE / 2, 922, 840, body, 500, 30, MUTED);
    ctx.fillStyle = CRIMSON[1];
    ctx.font = display(800, 36);
    var host = window.location.host;
    var tail = host ? siteName(d) + '  ·  ' + host : siteName(d);
    fitText(ctx, tail, SIZE / 2, 976, 840, display, 800, 36, CRIMSON[1]);
  }

  function drawBadge(ctx, d) {
    background(ctx);
    lockup(ctx, d, SIZE / 2, 172);

    var tierColor = d.tierColor || '#cd7f32';
    bigDrop(ctx, SIZE / 2, 218, 290, tierColor);
    ctx.textAlign = 'center';
    ctx.fillStyle = '#ffffff';
    ctx.font = display(800, String(d.donations).length > 2 ? 84 : 116);
    ctx.fillText(String(d.donations), SIZE / 2, 450);
    ctx.font = body(700, 22);
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.fillText('VERIFIED', SIZE / 2, 486);

    fitText(ctx, d.name, SIZE / 2, 598, 840, display, 800, 70, INK);
    fitText(ctx, d.tier || 'Aspiring Donor', SIZE / 2, 656, 840, display, 800, 44, tierColor === '#999999' ? MUTED : tierColor);
    if (d.hero) {
      fitText(ctx, d.hero, SIZE / 2, 704, 840, display, 700, 32, d.heroColor || CRIMSON[1]);
    }

    // Stats panel.
    ctx.fillStyle = '#fdf1ef';
    roundRect(ctx, 120, 740, SIZE - 240, 130, 30);
    ctx.fill();
    var stats = [
      ['UP TO ' + d.lives, 'EST. PEOPLE HELPED'],
      [String(d.streak), 'CYCLE STREAK'],
      [String(d.points), 'POINTS']
    ];
    stats.forEach(function (s, i) {
      var x = 120 + (SIZE - 240) * (i + 0.5) / 3;
      ctx.textAlign = 'center';
      ctx.fillStyle = INK;
      ctx.font = display(800, s[0].length > 8 ? 40 : 54);
      ctx.fillText(s[0], x, 806);
      ctx.fillStyle = MUTED;
      ctx.font = body(700, 19);
      ctx.fillText(s[1], x, 842);
      if (i) {
        ctx.fillStyle = 'rgba(16,24,40,0.1)';
        ctx.fillRect(120 + (SIZE - 240) * i / 3, 764, 2, 82);
      }
    });

    footer(ctx, d, 'Donate blood. Save lives.');
  }

  function drawRequest(ctx, d) {
    background(ctx);
    // Crimson header band inside the white card.
    var g = ctx.createLinearGradient(60, 60, SIZE - 60, 260);
    g.addColorStop(0, CRIMSON[0]);
    g.addColorStop(0.55, CRIMSON[1]);
    g.addColorStop(1, CRIMSON[2]);
    ctx.fillStyle = g;
    roundRect(ctx, 60, 60, SIZE - 120, 150, 56);
    ctx.fill();
    ctx.fillRect(60, 130, SIZE - 120, 80);
    ctx.textAlign = 'center';
    fitText(ctx, 'URGENT: BLOOD NEEDED', SIZE / 2, 152, 800, display, 800, 58, '#ffffff');
    ctx.font = body(600, 26);
    ctx.fillStyle = 'rgba(255,255,255,0.85)';
    ctx.fillText(siteName(d).toUpperCase(), SIZE / 2, 192);

    bigDrop(ctx, SIZE / 2, 250, 320, CRIMSON[1]);
    ctx.textAlign = 'center';
    ctx.fillStyle = '#ffffff';
    ctx.font = display(800, 124);
    ctx.fillText(d.bloodGroup, SIZE / 2, 500);

    ctx.font = display(800, 54);
    var lines = wrapLines(ctx, d.hospital, 840).slice(0, 2);
    ctx.fillStyle = INK;
    ctx.textAlign = 'center';
    var y = 650;
    lines.forEach(function (line) {
      ctx.fillText(line, SIZE / 2, y);
      y += 64;
    });
    fitText(ctx, d.district, SIZE / 2, y + 4, 840, display, 700, 40, MUTED);

    var bags = d.remaining + (d.remaining === 1 ? ' bag' : ' bags') + ' still needed';
    var needed = bags + (d.neededBy ? ' by ' + d.neededBy : '');
    ctx.fillStyle = '#fdf1ef';
    roundRect(ctx, 150, y + 40, SIZE - 300, 84, 42);
    ctx.fill();
    fitText(ctx, needed, SIZE / 2, y + 96, SIZE - 360, display, 800, 38, CRIMSON[1]);

    footer(ctx, d, 'Can you help? Open the link and tap "I can help".');
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

  // Makes sure the web fonts (and the Bangla subset for this text) are ready
  // before drawing, otherwise canvas silently uses a fallback font.
  function loadFonts(data) {
    if (!document.fonts || !document.fonts.load) return Promise.resolve();
    var sample = [data.name, data.hospital, data.district, data.tier].filter(Boolean).join(' ');
    return Promise.all([
      document.fonts.load('800 60px "Plus Jakarta Sans"', sample),
      document.fonts.load('700 30px "Plus Jakarta Sans"', sample),
      document.fonts.load('500 30px "Inter"'),
      document.fonts.load('700 30px "Inter"'),
      document.fonts.load('700 60px "Hind Siliguri"', sample)
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
    var filename = root.dataset.shareFilename || 'share-card.png';
    var shareUrl = root.dataset.shareUrl;
    var shareText = root.dataset.shareText;

    function makeBlob() {
      return loadFonts(data).then(function () { return toBlob(render(data)); });
    }

    // Optional on-page preview of the exact image people will share.
    var preview = root.querySelector('[data-share-preview]');
    if (preview) {
      loadFonts(data).then(function () {
        var canvas = render(data);
        canvas.className = 'cm-preview-img';
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', 'Preview of the image you can share');
        preview.appendChild(canvas);
      });
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
