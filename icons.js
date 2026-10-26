/* أيقونات أبيات: SVG مصمتة بلون واحد (currentColor) فتتلوّن كاملةً بلون الحالة من سطر واحد. */
(function () {
  const HOUSE =
    '<path d="M6 34 L32 11 L58 34" fill="none" stroke="currentColor" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<rect x="43" y="10" width="7" height="15" rx="1"/>' +
    '<path fill-rule="evenodd" d="M14 35 L32 20 L50 35 V54 A3 3 0 0 1 47 57 H17 A3 3 0 0 1 14 54 Z M27 57 V46 A5 5 0 0 1 37 46 V57 Z"/>';
  const BUILDING =
    '<path fill-rule="evenodd" d="M14 6 H50 V58 H14 Z M21 13 h8 v7 h-8 Z M35 13 h8 v7 h-8 Z M21 24 h8 v7 h-8 Z M35 24 h8 v7 h-8 Z M21 35 h8 v7 h-8 Z M35 35 h8 v7 h-8 Z M28 58 V47 H36 V58 Z"/>' +
    '<rect x="9" y="58" width="46" height="4" rx="1"/>';
  const svg = (inner, cls, size) =>
    `<svg class="${cls}" viewBox="0 0 64 64" width="${size}" height="${size}" fill="currentColor" aria-hidden="true" focusable="false">${inner}</svg>`;
  window.ICONS = {
    house: (size = 40) => svg(HOUSE, 'ico ico-house', size),
    building: (size = 20) => svg(BUILDING, 'ico ico-building', size),
  };
})();
