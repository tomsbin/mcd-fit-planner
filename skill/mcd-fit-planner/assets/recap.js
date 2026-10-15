const message = document.getElementById('message');
function save(blob, name) {
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function artwork() { return new XMLSerializer().serializeToString(document.querySelector('#poster svg')); }
const period = document.body.dataset.period;
document.getElementById('svg-download').onclick = () => save(new Blob([artwork()], {type:'image/svg+xml;charset=utf-8'}), `mcd-recap-${period}.svg`);
document.getElementById('png-download').onclick = async () => {
  message.textContent = '正在生成分享长图…';
  await document.fonts.ready;
  const url = URL.createObjectURL(new Blob([artwork()], {type:'image/svg+xml;charset=utf-8'}));
  const img = new Image();
  img.onload = () => {
    try {
      const canvas = document.createElement('canvas');
      const svg = document.querySelector('#poster svg');
      canvas.width = Number(svg.getAttribute('width')); canvas.height = Number(svg.getAttribute('height'));
      canvas.getContext('2d').drawImage(img, 0, 0);
      canvas.toBlob(blob => {
        if (blob) { save(blob, `mcd-recap-${period}.png`); message.textContent = '长图已生成，可以保存了。'; }
        else message.textContent = '当前浏览器无法导出 PNG，可以先保存 SVG。';
      }, 'image/png');
    } catch (error) { message.textContent = '当前浏览器无法导出 PNG，可以先保存 SVG。'; }
    finally { URL.revokeObjectURL(url); }
  };
  img.onerror = () => { URL.revokeObjectURL(url); message.textContent = '图片导出失败，可以先保存 SVG。'; };
  img.src = url;
};
document.getElementById('filter').onchange = event => {
  for (const row of document.querySelectorAll('#meal-rows tr[data-status]')) {
    const state = row.dataset.status, value = event.target.value;
    row.hidden = !(value === 'all' || value === state || (value === 'outside' && ['above','below','mixed'].includes(state)) || (value === 'unknown' && ['uncertain','no_goal','no_history','conflict'].includes(state)));
  }
};
const scenes = [...document.querySelectorAll('.scene')];
const navLinks = [...document.querySelectorAll('.chapter-nav a')];
document.documentElement.classList.add('js');
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      for (const link of navLinks) link.setAttribute('aria-current', String(link.hash === '#' + entry.target.id));
      entry.target.querySelectorAll('.reveal').forEach(node => node.classList.add('visible'));
    }
  }, {rootMargin:'-15% 0px -15% 0px', threshold:0.15});
  scenes.forEach(scene => observer.observe(scene));
} else document.querySelectorAll('.reveal').forEach(node => node.classList.add('visible'));
// Navigation is optional: normal scrolling, links, keyboard and no-JS reading all work.
document.addEventListener('keydown', event => {
  if (!['ArrowDown','ArrowUp','PageDown','PageUp'].includes(event.key) || /INPUT|SELECT|TEXTAREA|BUTTON/.test(event.target.tagName) || document.getElementById('details').contains(event.target)) return;
  const nearest = scenes.reduce((best, scene, i) => Math.abs(scene.getBoundingClientRect().top) < best.distance ? {index:i,distance:Math.abs(scene.getBoundingClientRect().top)} : best, {index:0,distance:Infinity}).index;
  const next = Math.max(0, Math.min(scenes.length-1, nearest + (['ArrowDown','PageDown'].includes(event.key) ? 1 : -1)));
  if (next !== nearest && window.scrollY < document.getElementById('details').offsetTop - 100) {
    event.preventDefault(); scenes[next].scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  }
});
document.querySelectorAll('.show-details').forEach(link => link.onclick = () => { document.getElementById('details').open = true; });
