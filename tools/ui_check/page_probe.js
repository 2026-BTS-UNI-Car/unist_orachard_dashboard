// 화면 안에서 도는 측정 코드. run_checks.py가 이 파일을 읽어 페이지에 넣는다.
// 페이지의 DOM과 저장소를 바꾸지 않는다(창 객체에 STOP 요소 참조 하나만 둔다).
// 좌표는 모두 CSS px이고, 따로 적지 않으면 문서 기준(스크롤 0일 때의 위치)이다.
(config) => {
  const root = document.documentElement;
  const body = document.body;
  const vh = config.viewportHeight;
  const vw = root.clientWidth || config.viewportWidth;
  const maxList = config.maxList || 40;
  const problems = [];
  const round = (value) => Math.round(value * 10) / 10;
  const guard = (name, fallback, fn) => {
    try { return fn(); } catch (error) { problems.push(`${name}: ${String(error).slice(0, 200)}`); return fallback; }
  };

  // ---- 요소 설명 -----------------------------------------------------
  const ownSelector = (el) => {
    let value = el.tagName.toLowerCase();
    if (el.id) value += `#${el.id}`;
    const classes = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3);
    if (classes.length) value += `.${classes.join('.')}`;
    return value;
  };
  const selectorOf = (el) => {
    let value = ownSelector(el);
    if (el.id) return value;
    for (const attribute of el.attributes || []) {
      if (attribute.name.startsWith('data-') && attribute.value.length <= 40) { value += `[${attribute.name}="${attribute.value}"]`; break; }
    }
    if (el.tagName === 'INPUT' && el.type) value += `[type=${el.type}]`;
    let parent = el.parentElement;
    while (parent && parent !== body && !parent.id) parent = parent.parentElement;
    return parent && parent !== body && parent.id ? `#${parent.id} ${value}` : value;
  };
  const snippet = (el, limit = 60) => {
    const raw = el.textContent || el.value || el.getAttribute('aria-label') || el.getAttribute('alt') || '';
    const text = String(raw).trim().replace(/\s+/g, ' ');
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  };
  const docRect = (r) => ({x: round(r.left + window.scrollX), y: round(r.top + window.scrollY), w: round(r.right - r.left), h: round(r.bottom - r.top)});

  // ---- 보이는지 판단 -------------------------------------------------
  const styleCache = new WeakMap();
  const styleOf = (el) => { let s = styleCache.get(el); if (!s) { s = getComputedStyle(el); styleCache.set(el, s); } return s; };
  const cssVisible = (el) => {
    if (typeof el.checkVisibility === 'function') {
      return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true, opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true});
    }
    for (let node = el; node && node !== root; node = node.parentElement) {
      const s = styleOf(node);
      if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
    }
    return true;
  };
  // html의 overflow가 visible이면 body의 overflow는 화면 전체로 옮겨 가므로 body 상자는 내용을 자르지 않는다.
  const rootStyle = getComputedStyle(root);
  const bodyOverflowGoesToViewport = rootStyle.overflowX === 'visible' && rootStyle.overflowY === 'visible';
  // el의 조상들이 잘라 내고 남기는 범위(화면 기준 좌표). 자르는 조상이 없으면 끝없는 범위다.
  // stopAt을 주면 그 조상에 닿기 전까지만 본다(stopAt 자신은 자르지 않은 것으로 본다).
  const clipRegion = (el, stopAt = null) => {
    const region = {left: -Infinity, top: -Infinity, right: Infinity, bottom: Infinity};
    let node = el;
    let position = styleOf(el).position;
    while (position !== 'fixed') {
      const parent = node.parentElement;
      if (!parent || parent === root || parent === stopAt) break;
      const ps = styleOf(parent);
      node = parent;
      if (position === 'absolute' && ps.position === 'static') continue; // 기준 상자가 아닌 조상은 자르지 않는다.
      if (parent === body && bodyOverflowGoesToViewport) { position = ps.position; continue; }
      const clipX = ps.overflowX !== 'visible';
      const clipY = ps.overflowY !== 'visible';
      if (clipX || clipY) {
        const pr = parent.getBoundingClientRect();
        if (clipX) { region.left = Math.max(region.left, pr.left); region.right = Math.min(region.right, pr.right); }
        if (clipY) { region.top = Math.max(region.top, pr.top); region.bottom = Math.min(region.bottom, pr.bottom); }
      }
      position = ps.position;
    }
    return region;
  };
  // 잘라 내는 조상을 반영한, 요소 상자에서 실제로 보이는 부분.
  const visibleBox = (el, stopAt = null) => {
    const r = el.getBoundingClientRect();
    const region = clipRegion(el, stopAt);
    return {left: Math.max(r.left, region.left), top: Math.max(r.top, region.top), right: Math.min(r.right, region.right), bottom: Math.min(r.bottom, region.bottom)};
  };
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'NOSCRIPT', 'TEMPLATE', 'BR', 'WBR', 'OPTION', 'OPTGROUP', 'SOURCE', 'TRACK']);
  const info = new Map(); // 보이는 요소 -> {rect, box}
  const hiddenByClip = new Set();
  for (const el of body.querySelectorAll('*')) {
    if (SKIP_TAGS.has(el.tagName)) continue;
    if (el.namespaceURI !== 'http://www.w3.org/1999/xhtml' && el.tagName.toLowerCase() !== 'svg') continue; // svg 안쪽 도형은 건너뛴다.
    if (!cssVisible(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const box = visibleBox(el);
    if (box.right - box.left <= 0.5 || box.bottom - box.top <= 0.5) { hiddenByClip.add(el); continue; }
    info.set(el, {rect, box});
  }
  const visible = (el) => info.has(el);
  // 왼쪽이나 위쪽 바깥으로 완전히 나가 스크롤로도 볼 수 없는 요소(닫힌 서랍 메뉴 등).
  const offCanvas = (el) => { const box = info.get(el).box; return box.right <= 0 || box.bottom + window.scrollY <= 0; };

  // ---- 문서 크기 -----------------------------------------------------
  const documentInfo = {
    path: location.pathname,
    title: document.title,
    scroll_width: root.scrollWidth,
    client_width: root.clientWidth,
    scroll_height: root.scrollHeight,
    client_height: root.clientHeight,
    body_scroll_width: body.scrollWidth,
    inner_width: window.innerWidth,
    inner_height: window.innerHeight,
    visual_scale: window.visualViewport ? round(window.visualViewport.scale * 1000) / 1000 : 1,
    device_pixel_ratio: window.devicePixelRatio,
    visible_elements: info.size,
    elements_hidden_by_clipping: hiddenByClip.size,
  };

  // ---- 내용 잎(글, 그림, 조작 요소) ----------------------------------
  // 상자 자체가 내용인 요소. 버튼은 상자와 안쪽 글을 둘 다 본다.
  const BOX_TAGS = new Set(['IMG', 'CANVAS', 'VIDEO', 'INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'svg', 'SVG']);
  const REPLACED_TAGS = new Set(['IMG', 'CANVAS', 'VIDEO', 'INPUT', 'SELECT', 'TEXTAREA', 'svg', 'SVG']);
  // 글의 세로 넘침은 글자 크기에 견주어 본다. 줄 높이가 글자 크기보다 작은 제목은
  // 글자 상자가 줄 상자보다 몇 px 크게 나오지만 실제로 넘친 것이 아니다.
  const textSlack = (el, axis, slack) => (axis === 'y' ? Math.max(slack, 0.3 * (parseFloat(styleOf(el).fontSize) || 16)) : slack);
  const ownTextBox = (el) => {
    let right = -Infinity, bottom = -Infinity, left = Infinity, top = Infinity, found = false;
    for (const node of el.childNodes) {
      if (node.nodeType !== 3 || !node.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (r.width <= 0 || r.height <= 0) continue;
        found = true; right = Math.max(right, r.right); bottom = Math.max(bottom, r.bottom); left = Math.min(left, r.left); top = Math.min(top, r.top);
      }
    }
    return found ? {left, top, right, bottom} : null;
  };
  // el의 상자(테두리 안쪽, 안쪽 여백 포함) 밖으로 나간 내용 잎을 찾는다. axis는 'x' 또는 'y'.
  // scrollWidth가 clientWidth보다 커지는 기준과 같은 경계다.
  const spillCulprits = (el, axis, slack) => {
    const r = el.getBoundingClientRect();
    const s = styleOf(el);
    const limit = axis === 'x' ? r.right - (parseFloat(s.borderRightWidth) || 0) : r.bottom - (parseFloat(s.borderBottomWidth) || 0);
    const edge = (b) => (axis === 'x' ? b.right : b.bottom);
    const rows = [];
    const own = ownTextBox(el);
    if (own && edge(own) > limit + textSlack(el, axis, slack)) rows.push({sel: selectorOf(el), kind: 'own-text', text: snippet(el), over_px: round(edge(own) - limit)});
    for (const child of el.querySelectorAll('*')) {
      if (!visible(child) && !hiddenByClip.has(child)) continue;
      const raw = child.getBoundingClientRect();
      if (raw.width <= 0 || raw.height <= 0) continue;
      // el과 잎 사이의 다른 상자(안쪽 스크롤 영역 등)가 이미 잘라 낸 부분은 el 탓이 아니다.
      const region = clipRegion(child, el);
      const cr = {left: Math.max(raw.left, region.left), top: Math.max(raw.top, region.top), right: Math.min(raw.right, region.right), bottom: Math.min(raw.bottom, region.bottom)};
      let over = 0, kind = '';
      // 상자 잎은 2px까지 봐준다(슬라이더의 기본 바깥 여백 같은 것).
      if (BOX_TAGS.has(child.tagName) && cr.right - cr.left > 0.5 && cr.bottom - cr.top > 0.5 && edge(cr) > limit + Math.max(slack, 2)) {
        over = edge(cr) - limit; kind = child.tagName.toLowerCase();
      }
      if (!REPLACED_TAGS.has(child.tagName)) {
        const text = ownTextBox(child);
        if (text) {
          // 글은 잎 상자 밖으로 나갈 수 있다. 잎이 스스로 자르면 잎 상자까지, 아니면 사이 조상들이 자른 범위까지만 닿는다.
          const cs = styleOf(child);
          const selfClips = (axis === 'x' ? cs.overflowX : cs.overflowY) !== 'visible';
          const reach = Math.min(edge(text), edge(region), selfClips ? edge(raw) : Infinity);
          const shown = Math.min(text.right, region.right) > Math.max(text.left, region.left) && Math.min(text.bottom, region.bottom) > Math.max(text.top, region.top);
          if (shown && reach > limit + textSlack(child, axis, slack) && reach - limit > over) { over = reach - limit; kind = 'text'; }
        }
      }
      if (kind) rows.push({sel: selectorOf(child), kind, text: snippet(child), over_px: round(over)});
      if (rows.length >= 200) break;
    }
    rows.sort((a, b) => b.over_px - a.over_px);
    return rows;
  };

  // ---- 화면 밖으로 나간 요소 -----------------------------------------
  const outside = guard('outside_viewport', {count_all: 0, count: 0, items: []}, () => {
    const flagged = new Set();
    for (const [el, {box}] of info) {
      if (offCanvas(el)) continue;
      if (box.right > vw + 1 || (box.left < -1 && box.right > 0)) flagged.add(el);
    }
    const leaves = [...flagged].filter((el) => ![...el.children].some((child) => flagged.has(child)));
    const items = leaves.map((el) => {
      const {rect, box} = info.get(el);
      return {sel: selectorOf(el), text: snippet(el), ...docRect(rect), over_right_px: round(Math.max(0, box.right - vw)), over_left_px: round(Math.max(0, -box.left))};
    }).sort((a, b) => (b.over_right_px + b.over_left_px) - (a.over_right_px + a.over_left_px));
    return {count_all: flagged.size, count: leaves.length, items: items.slice(0, maxList)};
  });

  // ---- 자기 상자 밖으로 넘친 내용 / 잘린 내용 ------------------------
  const overflowX = [], overflowY = [], clipped = [], ellipsis = [], scrollers = [];
  guard('overflow_and_clip', null, () => {
    const visibleX = new Set(), visibleY = new Set();
    for (const [el] of info) {
      if (el === body || offCanvas(el)) continue;
      const s = styleOf(el);
      if (s.display === 'inline' || el.clientWidth < 4 || el.clientHeight < 4) continue;
      if (['INPUT', 'SELECT', 'TEXTAREA', 'CANVAS', 'IMG', 'VIDEO'].includes(el.tagName)) continue;
      const dx = el.scrollWidth - el.clientWidth, dy = el.scrollHeight - el.clientHeight;
      if (dx > 2) {
        if (s.overflowX === 'visible') visibleX.add(el);
        else if (s.overflowX === 'hidden' || s.overflowX === 'clip') {
          const culprits = spillCulprits(el, 'x', 1);
          if (culprits.length) {
            const row = {sel: selectorOf(el), axis: 'x', text: snippet(el), ...docRect(info.get(el).rect), scroll_size: el.scrollWidth, client_size: el.clientWidth, over_px: dx, culprits: culprits.slice(0, 6)};
            (s.textOverflow === 'ellipsis' ? ellipsis : clipped).push(row);
          }
        } else scrollers.push({sel: selectorOf(el), axis: 'x', over_px: dx});
      }
      if (dy > 2) {
        if (s.overflowY === 'visible') visibleY.add(el);
        else if (s.overflowY === 'hidden' || s.overflowY === 'clip') {
          const culprits = spillCulprits(el, 'y', 2);
          if (culprits.length) {
            const row = {sel: selectorOf(el), axis: 'y', text: snippet(el), ...docRect(info.get(el).rect), scroll_size: el.scrollHeight, client_size: el.clientHeight, over_px: dy, culprits: culprits.slice(0, 6)};
            // 줄 수 제한(line-clamp)은 말줄임과 같은 의도된 줄임으로 본다.
            const clamp = s.webkitLineClamp || s.getPropertyValue('-webkit-line-clamp');
            (clamp && clamp !== 'none' ? ellipsis : clipped).push(row);
          }
        }
      }
    }
    const innermost = (set) => {
      const hasInner = new Set();
      for (const el of set) for (let p = el.parentElement; p && p !== body; p = p.parentElement) if (set.has(p)) hasInner.add(p);
      return [...set].filter((el) => !hasInner.has(el));
    };
    for (const el of innermost(visibleX)) {
      const culprits = spillCulprits(el, 'x', 1);
      if (culprits.length) overflowX.push({sel: selectorOf(el), axis: 'x', text: snippet(el), ...docRect(info.get(el).rect), over_px: el.scrollWidth - el.clientWidth, culprits: culprits.slice(0, 4)});
    }
    for (const el of innermost(visibleY)) {
      const culprits = spillCulprits(el, 'y', 4);
      if (culprits.length) overflowY.push({sel: selectorOf(el), axis: 'y', text: snippet(el), ...docRect(info.get(el).rect), over_px: el.scrollHeight - el.clientHeight, culprits: culprits.slice(0, 4)});
    }
    for (const list of [overflowX, overflowY, clipped, ellipsis]) list.sort((a, b) => b.over_px - a.over_px);
  });

  // ---- 조작 요소 -----------------------------------------------------
  const INTERACTIVE = 'button, a[href], input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="link"], [role="tab"], [role="switch"], [role="checkbox"], [role="menuitem"]';
  const interactive = guard('interactive', {items: [], skipped: {}}, () => {
    const skipped = {not_visible: 0, pointer_events_none: 0, off_canvas: 0};
    const elements = [];
    for (const el of body.querySelectorAll(INTERACTIVE)) {
      if (!visible(el)) { skipped.not_visible += 1; continue; }
      if (styleOf(el).pointerEvents === 'none') { skipped.pointer_events_none += 1; continue; }
      if (offCanvas(el)) { skipped.off_canvas += 1; continue; }
      elements.push(el);
    }
    const indexOf = new Map(elements.map((el, index) => [el, index]));
    const labelIds = new Map();
    const layerOf = (el) => {
      for (let node = el; node && node !== root; node = node.parentElement) {
        const position = styleOf(node).position;
        if (position === 'fixed' || position === 'sticky') return position;
      }
      return 'flow';
    };
    const items = elements.map((el) => {
      const {rect} = info.get(el);
      const s = styleOf(el);
      const ancestors = [];
      for (let p = el.parentElement; p && p !== body; p = p.parentElement) if (indexOf.has(p)) ancestors.push(indexOf.get(p));
      const label = el.closest('label');
      let labelId = null;
      if (label) { if (!labelIds.has(label)) labelIds.set(label, labelIds.size); labelId = labelIds.get(label); }
      // 체크 상자와 라디오는 감싼 label 전체가 누름 영역이다.
      let hit = rect, hitSource = 'self';
      if (label && el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio') && visible(label)) {
        const lr = label.getBoundingClientRect();
        hit = {left: Math.min(lr.left, rect.left), top: Math.min(lr.top, rect.top), right: Math.max(lr.right, rect.right), bottom: Math.max(lr.bottom, rect.bottom)};
        hitSource = 'label';
      }
      const inlineLink = el.tagName === 'A' && s.display === 'inline'
        && [...el.parentElement.childNodes].some((node) => node.nodeType === 3 && node.textContent.trim());
      return {
        sel: selectorOf(el), text: snippet(el, 40), tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
        ...docRect(rect), hit: docRect(hit), hit_source: hitSource,
        disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
        layer: layerOf(el), ancestors, label: labelId, inline_text_link: inlineLink,
        in_first_viewport: rect.top >= 0 && rect.bottom <= vh + 0.5 && rect.left >= 0 && rect.right <= vw + 0.5,
      };
    });
    return {items, skipped};
  });

  // ---- 캔버스 --------------------------------------------------------
  const canvases = guard('canvases', [], () => [...body.querySelectorAll('canvas')].filter(visible).map((canvas) => {
    const {rect} = info.get(canvas);
    const parent = canvas.parentElement.getBoundingClientRect();
    return {sel: selectorOf(canvas), ...docRect(rect), css_w: round(rect.width), css_h: round(rect.height), buffer_w: canvas.width, buffer_h: canvas.height,
      dpr: window.devicePixelRatio, parent_w: round(parent.width), parent_h: round(parent.height)};
  }));

  // ---- 그림 ----------------------------------------------------------
  const images = guard('images', {total: 0, broken: []}, () => {
    const all = [...body.querySelectorAll('img')].filter(visible);
    const broken = all.filter((img) => img.complete && img.naturalWidth === 0).map((img) => {
      let source = img.getAttribute('src') || '';
      if (source.startsWith('data:')) source = 'data:…'; else if (source.startsWith('blob:')) source = 'blob:…';
      return {sel: selectorOf(img), alt: (img.getAttribute('alt') || '').slice(0, 60), src: source.slice(0, 120), has_src: Boolean(source), ...docRect(info.get(img).rect)};
    });
    return {total: all.length, broken};
  });

  // ---- STOP 조작 요소 ------------------------------------------------
  const stop = guard('stop', {candidates: []}, () => {
    const seen = new Set();
    const candidates = [];
    const add = (el, source, selector) => {
      if (seen.has(el)) return;
      seen.add(el);
      const rect = el.getBoundingClientRect();
      const shown = visible(el) && !offCanvas(el);
      let position = 'flow';
      for (let node = el; node && node !== root; node = node.parentElement) {
        const p = styleOf(node).position;
        if (p === 'fixed' || p === 'sticky') { position = p; break; }
      }
      let hit = null;
      if (shown && rect.top >= 0 && rect.bottom <= window.innerHeight && rect.left >= 0 && rect.right <= window.innerWidth) {
        const top = document.elementFromPoint((rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2);
        hit = Boolean(top && (top === el || el.contains(top) || top.contains(el)));
      }
      candidates.push({source, selector, sel: selectorOf(el), text: snippet(el, 30), visible: shown, layer: position, hit_test: hit, disabled: Boolean(el.disabled), ...docRect(rect)});
    };
    for (const selector of config.stopSelectors || []) {
      let found = [];
      try { found = [...document.querySelectorAll(selector)]; } catch (error) { problems.push(`stop selector ${selector}: ${error}`); }
      for (const el of found) add(el, 'selector', selector);
    }
    const texts = new Set((config.stopTexts || []).map((value) => value.toLowerCase()));
    for (const el of body.querySelectorAll('button, [role="button"], a[href]')) {
      if (texts.has((el.textContent || '').trim().toLowerCase())) add(el, 'text', '');
    }
    const primary = candidates.findIndex((item) => item.visible);
    window.__uiCheckStopElement = primary >= 0 ? [...seen][primary] : null;
    return {candidates, primary_index: primary};
  });

  // ---- 꾸밈 통계(참고용) ---------------------------------------------
  const style = guard('style', {}, () => {
    const fontSizes = {}, fontWeights = {}, colors = new Set(), backgrounds = new Set(), radii = new Set(), shadows = new Set();
    let gradients = 0, blur = 0, shadowElements = 0, textElements = 0, heavyText = 0;
    const bump = (bag, key) => { bag[key] = (bag[key] || 0) + 1; };
    for (const [el] of info) {
      if (offCanvas(el)) continue;
      const s = styleOf(el);
      const hasText = [...el.childNodes].some((node) => node.nodeType === 3 && node.textContent.trim());
      if (hasText) {
        textElements += 1; bump(fontSizes, s.fontSize); bump(fontWeights, s.fontWeight); colors.add(s.color);
        if (Number(s.fontWeight) >= 800) heavyText += 1;
      }
      if (s.backgroundColor !== 'rgba(0, 0, 0, 0)') backgrounds.add(s.backgroundColor);
      if (s.backgroundImage.includes('gradient')) gradients += 1;
      if (s.borderTopLeftRadius !== '0px') radii.add(s.borderTopLeftRadius);
      if (s.boxShadow !== 'none') { shadows.add(s.boxShadow); shadowElements += 1; }
      if (s.backdropFilter && s.backdropFilter !== 'none') blur += 1;
    }
    return {font_sizes: fontSizes, font_weights: fontWeights, text_elements: textElements, heavy_text_elements: heavyText, text_colors: colors.size,
      background_colors: backgrounds.size, radius_kinds: radii.size, shadow_kinds: shadows.size, shadow_elements: shadowElements, gradient_elements: gradients, blur_elements: blur};
  });

  return {
    document: documentInfo,
    effective_viewport: {width: vw, height: vh},
    outside_viewport: outside,
    content_overflow_x: {count: overflowX.length, items: overflowX.slice(0, maxList)},
    content_overflow_y: {count: overflowY.length, items: overflowY.slice(0, maxList)},
    clipped: {count: clipped.length, items: clipped.slice(0, maxList)},
    ellipsis: {count: ellipsis.length, items: ellipsis.slice(0, maxList)},
    scrollable_x: {count: scrollers.length, items: scrollers.slice(0, maxList)},
    interactive,
    canvases,
    images,
    stop,
    style,
    probe_problems: problems,
  };
}
