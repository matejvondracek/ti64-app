// Map viewer (phase 5): Leaflet (bundled with NiceGUI) on a flat CRS in micrometres.
//   lng = x (um, right), lat = y (um, down); zoom 0 = `base` um per screen pixel.
//   Base layer and overlays are server tiles (/api/ti64/...): pixels coloured by the active backend,
//   placed by ti64app.raster. Annotations / ROIs are vectors in map coordinates; drawing tools emit
//   'shape' events to Python, which records them as an annotations.set recipe step.
import { leaflet as L } from "nicegui-leaflet";

const TOOLS_2PT = ["arrow", "line", "roi_rect", "measure"];
const TOOLS_MULTI = ["polyline", "crack", "roi_poly"];
const TOOLS_1PT = ["text", "point", "probe"];
// legend snapping (fix 3.12): six positions on the map, default width of "auto size" items (fraction of the map width)
const ANCHORS = ["tl", "tc", "tr", "bl", "bc", "br"];
const AUTO_W = { scale_bar: 0.22, colorbar: 0.3, ipf_key: 0.18 };
const SNAP_PX = 28;

export default {
  template: `<div class="ti64-viewer" style="position:relative;width:100%;height:100%"></div>`,
  props: {
    base: Number,
    extent: Array,             // [x0, x1, y0, y1] um
    layer_url: String,
    layer_opacity: Number,
    overlays: Array,           // [{url, opacity}]
    shapes: Array,             // [{id, type, xy: [[x, y], ...], color, text, width_pt}]
    tool: String,
    scale_bar: Boolean,
    alt_layer_url: String,     // "Compare to original": the unedited loaded map, shown while the button is held
    initial_view: Object,      // {x0, x1, y0, y1} restored on reopen (gui_state.json)
    legends: Array,            // [{kind: scale_bar|colorbar|ipf_key, x, y, w, frame: "map"}] fractions of the MAP
                               // extent (anchored to the map: they move and scale with zoom / pan; null = old
                               // scale-bar control); dragged / resized here, exported exactly so (legends.py)
    legend_data: Object,       // {colorbar: {stops, lo, hi, label} | null, swatches: [[label, hex]], key_url}
                               // items with anchor tl|tc|tr|bl|bc|br sit in that corner of the map at `margin`
                               // (fraction of the map width); auto = width AUTO_W[kind] of the map (fix 3.12)
    legend_margin: Number,     // default margin of snapped legends (fraction of the map width)
    hex_grid: Boolean,         // hexagonal map: the zoom indicator says whether cells are drawn as brick or hexagons
    cell_um: Number,           // measurement step dx (um)
    resourcePath: String,
  },
  data() {
    return { drawPts: [] };
  },
  async mounted() {
    await this.$nextTick();
    const href = window.path_prefix + `${this.resourcePath}/leaflet/leaflet.css`;
    if (!document.querySelector(`link[href="${href}"]`)) {
      const l = document.createElement("link");
      l.rel = "stylesheet";
      l.href = href;
      document.head.appendChild(l);
    }
    const base = this.base;
    const crs = L.extend({}, L.CRS.Simple, { transformation: new L.Transformation(1 / base, 0, 1 / base, 0) });
    this.map = L.map(this.$el, {
      // whole zoom levels: every tile is drawn pixel-exact at its zoom (fractional zooms scale tiles in the
      // browser, which blurs them and leaves hairline seams)
      crs, minZoom: -9, maxZoom: 7, zoomSnap: 1, zoomDelta: 1, wheelPxPerZoomLevel: 120,
      attributionControl: false, doubleClickZoom: false, boxZoom: true,
    });
    this.$el.style.background = "#d8d8d8";
    this.shapeLayer = L.layerGroup().addTo(this.map);
    this.previewLayer = L.layerGroup().addTo(this.map);
    this.overlayLayers = [];
    this.setLayer();
    this.setOverlays();
    const v = this.initial_view;
    if (v && isFinite(v.x0) && v.x1 > v.x0 && v.y1 > v.y0) this.zoomTo(v.x0, v.y0, v.x1, v.y1, 0); else this.fit();
    this.drawShapes();
    this.makeScaleBar();
    this.makeOverlayUi();
    this.map.on("zoomend", () => { this.drawShapes(); this.updateScaleBar(); this.renderLegends(); this.updateZoomInfo();
                                   this.emitView(); });
    // overlay lines keep their width in SCREEN pixels: tiles are drawn per zoom level with the width in px; while
    // Leaflet animates a zoom it would stretch the old tiles (thicker / thinner lines), so the overlays are hidden
    // from the start of a zoom until the tiles of the new level have loaded
    this.map.on("zoomstart", () => { for (const l of this.overlayLayers) l.setOpacity(0);
                                     if (this.legendBox) this.legendBox.style.visibility = "hidden"; });
    this.map.on("zoomend", () => { for (const l of this.overlayLayers) this.showWhenLoaded(l);
                                   if (this.legendBox) this.legendBox.style.visibility = ""; });
    this.map.on("move", () => this.positionLegends());
    this.resizeObs = new ResizeObserver(() => this.renderLegends());
    this.resizeObs.observe(this.$el);
    this.map.on("moveend", () => this.emitView());
    this.map.on("click", (e) => this.onClick(e));
    this.map.on("dblclick", (e) => this.finishMulti(e));
    this.map.on("mousemove", (e) => this.onMove(e));
    this.keyHandler = (ev) => {
      if (ev.key === "Escape") { this.drawPts = []; this.previewLayer.clearLayers(); }
      if (ev.key === "Enter") this.finishMulti();
    };
    document.addEventListener("keydown", this.keyHandler);
    this.$emit("ready", {});
  },
  unmounted() {
    document.removeEventListener("keydown", this.keyHandler);
    if (this.resizeObs) this.resizeObs.disconnect();
    if (this.map) this.map.remove();
  },
  watch: {
    layer_url() { this.setLayer(); },
    layer_opacity() { if (this.baseLayer) this.baseLayer.setOpacity(this.layer_opacity ?? 1); },
    overlays: { handler() { this.setOverlays(); }, deep: true },
    shapes: { handler() { this.drawShapes(); }, deep: true },
    tool() {
      this.drawPts = [];
      this.previewLayer.clearLayers();
      this.$el.style.cursor = this.tool && this.tool !== "pan" ? "crosshair" : "";
    },
    scale_bar() { this.updateScaleBar(); },
    // re-fit only when the extent really changes (every set() sends a new array; refitting on each one threw the
    // user out of a zoomed-in view when an overlay, legend or tile selection changed)
    extent(n, o) { if (JSON.stringify(n) !== JSON.stringify(o)) this.fit(); },
    legends: { handler() { this.updateScaleBar(); this.renderLegends(); }, deep: true },
    legend_data: { handler() { this.renderLegends(); }, deep: true },
    hex_grid() { this.updateZoomInfo(); },
    cell_um() { this.updateZoomInfo(); },
  },
  methods: {
    ll(x, y) { return L.latLng(y, x); },
    bounds() {
      const [x0, x1, y0, y1] = this.extent;
      return L.latLngBounds(this.ll(x0, y0), this.ll(x1, y1));
    },
    fit() {
      if (!this.map || !this.extent) return;
      this.map.fitBounds(this.bounds(), { padding: [10, 10] });
    },
    zoomTo(x0, y0, x1, y1, pad = 20) {
      this.map.fitBounds(L.latLngBounds(this.ll(x0, y0), this.ll(x1, y1)), { padding: [pad, pad] });
    },
    // hold-to-compare: swap the base layer for the original (unedited) map in the same view and zoom
    showAlt(on) {
      if (!this.map || !this.alt_layer_url) return;
      if (on && !this.altLayer) {
        this.altLayer = L.tileLayer(this.alt_layer_url, this.tileOptions(1, 2)).addTo(this.map);
        if (this.baseLayer) this.baseLayer.setOpacity(0);
        for (const l of this.overlayLayers) l.setOpacity(0);
        this.shapeLayer.remove();
        this.$el.classList.add("ti64-showing-original");
        this.$emit("compare", { on: true });
      } else if (!on && this.altLayer) {
        this.map.removeLayer(this.altLayer);
        this.altLayer = null;
        if (this.baseLayer) this.baseLayer.setOpacity(this.layer_opacity ?? 1);
        for (const l of this.overlayLayers) l.setOpacity(l._ti64Opacity ?? 1);
        this.shapeLayer.addTo(this.map);
        this.$el.classList.remove("ti64-showing-original");
        this.$emit("compare", { on: false });
      }
    },
    getView() {
      const b = this.map.getBounds();
      return { x0: b.getWest(), x1: b.getEast(), y0: b.getSouth(), y1: b.getNorth(), zoom: this.map.getZoom() };
    },
    emitView() {
      const b = this.map.getBounds();
      // lat grows downwards: 'south' (smaller lat) is the top edge of the screen
      this.$emit("view", { x0: b.getWest(), x1: b.getEast(), y0: Math.min(b.getSouth(), b.getNorth()),
                           y1: Math.max(b.getSouth(), b.getNorth()), zoom: this.map.getZoom() });
    },
    tileOptions(opacity, z) {
      return { tileSize: 256, minZoom: -9, maxZoom: 7, noWrap: true, bounds: this.bounds(), keepBuffer: 3,
               opacity: opacity ?? 1, zIndex: z, className: "ti64-pixelated", updateWhenZooming: false };
    },
    setLayer() {
      if (!this.map) return;
      if (this.baseLayer) { this.map.removeLayer(this.baseLayer); this.baseLayer = null; }
      if (this.layer_url) this.baseLayer = L.tileLayer(this.layer_url, this.tileOptions(this.layer_opacity, 1)).addTo(this.map);
    },
    setOverlays() {
      if (!this.map) return;
      for (const l of this.overlayLayers) this.map.removeLayer(l);
      this.overlayLayers = (this.overlays || []).map((o, i) => {
        const l = L.tileLayer(o.url, this.tileOptions(o.opacity, 10 + i));
        l._ti64Opacity = o.opacity ?? 1;
        return l.addTo(this.map);
      });
    },
    showWhenLoaded(l) {
      if (!l.isLoading || !l.isLoading()) { if (!this.altLayer) l.setOpacity(l._ti64Opacity ?? 1); return; }
      l.once("load", () => { if (!this.altLayer) l.setOpacity(l._ti64Opacity ?? 1); });
    },
    // ---- shapes ---------------------------------------------------------------------------------
    pxToUm(px) { return px * this.base / Math.pow(2, this.map.getZoom()); },
    arrowHead(p0, p1, color) {
      const dx = p1[0] - p0[0], dy = p1[1] - p0[1];
      const n = Math.hypot(dx, dy) || 1;
      const len = Math.min(this.pxToUm(14), 0.45 * n), w = 0.6 * len;
      const ux = dx / n, uy = dy / n;
      const bx = p1[0] - ux * len, by = p1[1] - uy * len;
      return L.polygon([this.ll(p1[0], p1[1]), this.ll(bx - uy * w / 2, by + ux * w / 2), this.ll(bx + uy * w / 2, by - ux * w / 2)],
                       { color, fillColor: color, fillOpacity: 1, weight: 1, interactive: false });
    },
    label(at, text, color, dx = 6, dy = -8) {
      return L.marker(at, { interactive: false, icon: L.divIcon({ className: "ti64-label", iconSize: null,
        html: `<span style="color:${color};position:relative;left:${dx}px;top:${dy}px">${text}</span>` }) });
    },
    drawShapes() {
      if (!this.map) return;
      this.shapeLayer.clearLayers();
      for (const s of this.shapes || []) {
        const c = s.color || "#000000";
        const lls = s.xy.map((p) => this.ll(p[0], p[1]));
        const w = Math.max(1, (s.width_pt || 1.5) * 1.33);
        let lyr = null;
        if (s.type === "text") {
          lyr = L.marker(lls[0], { icon: L.divIcon({ className: "ti64-label", html: `<span style="color:${c}">${s.text}</span>`, iconSize: null }) });
        } else if (s.type === "point") {
          lyr = L.circleMarker(lls[0], { radius: 5, color: c, fillColor: c, fillOpacity: 1, weight: 1 });
          if (s.text) this.label(lls[0], s.text, c).addTo(this.shapeLayer);
        } else if (s.type === "arrow") {
          lyr = L.layerGroup([L.polyline(lls, { color: c, weight: w }), this.arrowHead(s.xy[0], s.xy[1], c)]);
          if (s.text) this.label(lls[0], s.text, c, -14, -10).addTo(this.shapeLayer);
        } else if (s.type === "roi_rect" || s.type === "roi_poly") {
          lyr = s.type === "roi_rect"
            ? L.rectangle(L.latLngBounds(lls[0], lls[1]), { color: c, weight: 2, dashArray: "6 4", fill: false })
            : L.polygon(lls, { color: c, weight: 2, dashArray: "6 4", fill: false });
          // label at the top-left corner of the ROI
          const tl = L.latLng(Math.min(...s.xy.map((q) => q[1])), Math.min(...s.xy.map((q) => q[0])));
          if (s.text) this.label(tl, s.text, c, 2, -10).addTo(this.shapeLayer);
        } else {
          lyr = L.polyline(lls, { color: c, weight: s.type === "crack" ? Math.max(w, 3) : w });
          if (s.type === "crack" && s.text) this.label(lls[lls.length - 1], s.text, c).addTo(this.shapeLayer);
        }
        lyr.on && lyr.on("click", (ev) => { L.DomEvent.stop(ev); this.$emit("shape_click", { id: s.id }); });
        lyr.addTo(this.shapeLayer);
      }
    },
    // ---- drawing --------------------------------------------------------------------------------
    pt(e) { return [e.latlng.lng, e.latlng.lat]; },
    onClick(e) {
      const t = this.tool;
      if (!t || t === "pan") return;
      const p = this.pt(e);
      if (TOOLS_1PT.includes(t)) { this.$emit(t === "probe" ? "probe" : "shape", { type: t, xy: [p] }); return; }
      this.drawPts.push(p);
      if (TOOLS_2PT.includes(t) && this.drawPts.length === 2) {
        const xy = this.drawPts;
        this.drawPts = [];
        this.previewLayer.clearLayers();
        this.$emit(t === "measure" ? "measure" : "shape", { type: t, xy });
      }
    },
    finishMulti() {
      if (!TOOLS_MULTI.includes(this.tool)) return;
      // a double click adds the same point twice: drop duplicates
      const xy = this.drawPts.filter((p, i, a) => i === 0 || Math.hypot(p[0] - a[i - 1][0], p[1] - a[i - 1][1]) > 1e-9);
      this.drawPts = [];
      this.previewLayer.clearLayers();
      if (xy.length >= (this.tool === "roi_poly" ? 3 : 2)) this.$emit("shape", { type: this.tool, xy });
    },
    onMove(e) {
      const p = this.pt(e);
      this.$emit("cursor", { x: p[0], y: p[1] });
      if (!this.drawPts.length) return;
      this.previewLayer.clearLayers();
      const pts = [...this.drawPts, p].map((q) => this.ll(q[0], q[1]));
      if (this.tool === "roi_rect") L.rectangle(L.latLngBounds(pts[0], pts[pts.length - 1]), { color: "#00a0ff", weight: 1, dashArray: "4 4", fill: false, interactive: false }).addTo(this.previewLayer);
      else L.polyline(pts, { color: "#ff00ff", weight: 1.5, dashArray: "4 4", interactive: false }).addTo(this.previewLayer);
    },
    // ---- scale bar ------------------------------------------------------------------------------
    makeScaleBar() {
      const Ctl = L.Control.extend({
        onAdd: () => {
          const d = L.DomUtil.create("div", "ti64-scalebar");
          d.innerHTML = `<div class="bar"></div><div class="lab"></div>`;
          return d;
        },
      });
      this.scaleCtl = new Ctl({ position: "bottomleft" });
      this.scaleCtl.addTo(this.map);
      this.updateScaleBar();
    },
    updateScaleBar() {
      if (!this.scaleCtl) return;
      const el = this.scaleCtl.getContainer();
      el.style.display = this.scale_bar && !this.legends ? "" : "none";
      const umPerPx = this.pxToUm(1);
      const target = 120 * umPerPx;
      const p = Math.pow(10, Math.floor(Math.log10(target)));
      let len = p;
      for (const m of [5, 2, 1]) { if (m * p <= target) { len = m * p; break; } }
      el.querySelector(".bar").style.width = `${len / umPerPx}px`;
      el.querySelector(".lab").textContent = len >= 1000 ? `${len / 1000} mm` : `${+len.toPrecision(3)} µm`;
    },
    niceLength(maxUm) {
      const p = Math.pow(10, Math.floor(Math.log10(maxUm)));
      for (const m of [5, 2, 1]) if (m * p <= maxUm) return m * p;
      return p / 2;
    },
    barLabel(L) {
      if (L >= 1000) return `${+(L / 1000).toPrecision(3)} mm`;
      if (L < 1) return `${+(L * 1000).toPrecision(3)} nm`;
      return `${+L.toPrecision(3)} µm`;
    },
    // ---- legends on the map (drag to move, drag the corner to resize; positions = fractions of the view) --------
    makeOverlayUi() {
      this.legendBox = document.createElement("div");
      this.legendBox.style.cssText = "position:absolute;inset:0;pointer-events:none;z-index:750;overflow:hidden";
      this.$el.appendChild(this.legendBox);
      this.zoomInfo = document.createElement("div");
      this.zoomInfo.className = "ti64-zoominfo";
      this.zoomInfo.setAttribute("tabindex", "0");
      this.zoomInfo.setAttribute("data-testid", "viewer_zoominfo");
      this.zoomInfo.style.cssText = "position:absolute;left:50%;transform:translateX(-50%);top:8px;z-index:760;" +
        "font:11px sans-serif;white-space:nowrap;" +
        "background:rgba(255,255,255,.9);padding:2px 8px;border-radius:10px;border:1px solid #cbd5e1;cursor:help";
      this.$el.appendChild(this.zoomInfo);
      // hover / keyboard-focus tooltip drawn in the page (the explanation of brick vs hexagons)
      this.zoomTip = document.createElement("div");
      this.zoomTip.setAttribute("data-testid", "viewer_zoomtip");
      this.zoomTip.style.cssText = "position:absolute;left:50%;transform:translateX(-50%);top:34px;z-index:770;" +
        "max-width:380px;font:13px 'Segoe UI',sans-serif;line-height:1.35;color:#fff;background:#4b5563;" +
        "padding:6px 10px;border-radius:6px;display:none;pointer-events:none";
      this.$el.appendChild(this.zoomTip);
      const show = () => { this.zoomTip.textContent = this.zoomInfo.dataset.tip || ""; this.zoomTip.style.display = ""; };
      const hide = () => { this.zoomTip.style.display = "none"; };
      this.zoomInfo.addEventListener("mouseenter", show);
      this.zoomInfo.addEventListener("focus", show);
      this.zoomInfo.addEventListener("mouseleave", hide);
      this.zoomInfo.addEventListener("blur", hide);
      this.renderLegends();
      this.updateZoomInfo();
    },
    updateZoomInfo() {
      if (!this.zoomInfo || !this.map) return;
      const umPerPx = this.pxToUm(1);
      const cellPx = (this.cell_um || 0) / umPerPx;
      let txt = `${Math.round(100 * Math.pow(2, this.map.getZoom()))} %`;
      let tip = "Zoom of the view (100 % = one screen pixel per image pixel).";
      if (this.cell_um) {
        txt += ` · cell ${cellPx >= 10 ? cellPx.toFixed(0) : cellPx.toFixed(1)} px`;
        if (this.hex_grid) {
          txt += cellPx >= 8 ? " · hexagons" : " · brick";
          tip = "Hexagonal grid. Brick: each measurement is drawn as a rectangle (one step wide, one row high) and " +
                "every other row is shifted by half a step, like hexRaster in EBSD_part2.m - exact, but the cell " +
                "corners look square. Hexagons: from 8 screen pixels per cell on, each measurement is drawn as its " +
                "true regular hexagon (neighbouring rows offset by half a cell). The analysis always uses the " +
                "hexagonal grid; this only changes the drawing. Exports: native and 2x are brick, 4x and larger " +
                "are hexagons.";
        } else {
          tip = "Square grid: one square cell per measurement.";
        }
      }
      this.zoomInfo.textContent = txt;
      this.zoomInfo.dataset.tip = tip;
      this.zoomInfo.setAttribute("aria-label", `${txt}. ${tip}`);
      this.zoomInfo.dataset.mode = this.hex_grid ? (cellPx >= 8 ? "hexagons" : "brick") : "square";
    },
    legendHtml(it, wpx) {
      // every size (font, padding, bar height) follows the box width, so the whole element scales with the map
      const d = this.legend_data || {};
      const box = (cap, pad) => `background:rgba(255,255,255,.9);border-radius:${cap / 2}px;padding:${pad};color:#000`;
      if (it.kind === "scale_bar") {
        const umPerPx = this.pxToUm(1);
        const L = this.niceLength(0.8 * wpx * umPerPx);
        const cap = Math.max(2, wpx / 14);
        return `<div style="${box(cap, `${cap * 0.35}px 0 ${cap * 0.6}px`)};text-align:center;font:600 ${cap}px sans-serif;line-height:1.2">` +
               `${this.barLabel(L)}<div style="height:${cap * 0.65}px;width:${L / umPerPx}px;background:#000;` +
               `margin:${cap * 0.25}px auto 0"></div></div>`;
      }
      if (it.kind === "colorbar") {
        const cap = Math.max(2, wpx / 22);
        const cb = d.colorbar;
        if (cb && cb.stops && cb.stops.length) {
          const f = (v) => (v === null || v === undefined ? "" : String(+(+v).toPrecision(3)));
          return `<div style="${box(cap, `${cap * 0.8}px ${cap}px ${cap * 0.4}px`)};font:${cap}px sans-serif;line-height:1.2">` +
                 `<div style="height:${cap * 1.4}px;border:1px solid #000;background:linear-gradient(to right,` +
                 `${cb.stops.join(",")})"></div><div style="display:flex;justify-content:space-between">` +
                 `<span>${f(cb.lo)}</span><span>${f(cb.hi)}</span></div>` +
                 `<div style="text-align:center;font-size:${cap * 0.9}px">${cb.label || ""}</div></div>`;
        }
        if (d.swatches && d.swatches.length) {
          return `<div style="${box(cap, `${cap * 0.5}px ${cap}px`)};font:${cap}px sans-serif;line-height:1.2">` +
                 d.swatches.map(([l, c]) => `<div style="display:flex;align-items:center;gap:${cap * 0.6}px;` +
                   `margin:${cap * 0.25}px 0"><span style="display:inline-block;width:${cap * 1.4}px;` +
                   `height:${cap * 1.2}px;background:${c};border:1px solid #666"></span>${l}</div>`).join("") + "</div>";
        }
        return "";              // nothing to show (orientation map): the box is hidden, never text over the map
      }
      if (it.kind === "ipf_key") {
        if (d.key_url) return `<img src="${d.key_url}" draggable="false" style="width:100%;display:block;` +
                              `background:#fff;border-radius:4px;pointer-events:none">`;
        return "";              // no IPF colouring on this map: hidden
      }
      return "";
    },
    // map fractions <-> container pixels (legends are anchored to the map extent)
    mapToPx(fx, fy) {
      const [x0, x1, y0, y1] = this.extent;
      return this.map.latLngToContainerPoint(this.ll(x0 + fx * (x1 - x0), y0 + fy * (y1 - y0)));
    },
    pxToMap(px, py) {
      const [x0, x1, y0, y1] = this.extent;
      const ll = this.map.containerPointToLatLng(L.point(px, py));
      return [(ll.lng - x0) / (x1 - x0), (ll.lat - y0) / (y1 - y0)];
    },
    mapWidthPx() {
      const [x0, x1] = this.extent;
      return (x1 - x0) / this.pxToUm(1);
    },
    positionLegends() {
      if (!this.legendBox || !this.map || !this.extent) return;
      for (const box of this.legendBox.children) {
        const it = (this.legends || [])[+box.dataset.index];
        if (!it || box.dataset.dragging) continue;
        const w = parseFloat(box.style.width);
        const p = it.anchor ? this.anchorPx(it.anchor, w, box.offsetHeight, this.marginOf(it))
                            : this.mapToPx(it.x, it.y);
        box.style.left = `${p.x}px`;
        box.style.top = `${p.y}px`;
      }
    },
    marginOf(it) {
      const m = it.margin ?? this.legend_margin;
      return (m === null || m === undefined ? 0.02 : m) * this.mapWidthPx();
    },
    // top-left corner (container px) of a w x h box snapped to an anchor of the map with margin m (px)
    anchorPx(a, w, h, m) {
      const p0 = this.mapToPx(0, 0), p1 = this.mapToPx(1, 1);
      const x = a[1] === "l" ? p0.x + m : a[1] === "r" ? p1.x - m - w : (p0.x + p1.x - w) / 2;
      const y = a[0] === "t" ? p0.y + m : p1.y - m - h;
      return { x, y };
    },
    nearestAnchor(left, top, w, h, m) {
      let best = null, bd = SNAP_PX;
      for (const a of ANCHORS) {
        const p = this.anchorPx(a, w, h, m);
        const d = Math.hypot(p.x - left, p.y - top);
        if (d < bd) { bd = d; best = a; }
      }
      return best;
    },
    renderLegends() {
      if (!this.legendBox || !this.map || !this.extent) return;
      this.legendBox.innerHTML = "";
      const items = this.legends || [];
      const MW = this.mapWidthPx();
      const fix = [];                        // anchored items whose stored x / y differ from where they sit now
      items.forEach((it, i) => {
        const box = document.createElement("div");
        const wpx = Math.max(4, (it.auto ? (AUTO_W[it.kind] || it.w) : it.w) * MW);
        const p0 = this.mapToPx(it.x, it.y);
        box.className = "ti64-legend";
        box.dataset.kind = it.kind;
        box.dataset.index = String(i);
        box.setAttribute("data-testid", `legend_${it.kind}`);
        box.style.cssText = `position:absolute;left:${p0.x}px;top:${p0.y}px;width:${wpx}px;` +
                            "pointer-events:auto;cursor:move;user-select:none;outline:1px dashed transparent";
        box.title = "Fixed to the map: it moves and scales with zoom and pan. Drag to move it (it snaps to the corners " +
                    "and edge centres of the map), drag the blue corner to resize it. Exports put it exactly here.";
        const handle = document.createElement("div");
        handle.style.cssText = "position:absolute;right:-6px;bottom:-6px;width:12px;height:12px;background:#2563eb;" +
                               "border:2px solid #fff;border-radius:3px;cursor:nwse-resize";
        const fill = (w) => {
          const html = this.legendHtml(it, w);
          box.innerHTML = html;
          box.appendChild(handle);
          box.style.display = html ? "" : "none";       // nothing to draw for this map: no box at all
        };
        fill(wpx);
        box.onmouseenter = () => { box.style.outlineColor = "#2563eb"; };
        box.onmouseleave = () => { box.style.outlineColor = "transparent"; };
        const start = (e, mode) => {
          e.preventDefault();
          e.stopPropagation();
          this.map.dragging.disable();
          box.dataset.dragging = "1";
          const r0 = { x: e.clientX, y: e.clientY, left: parseFloat(box.style.left), top: parseFloat(box.style.top),
                       w: parseFloat(box.style.width) };
          const move = (m) => {
            if (mode === "move") {
              let nl = r0.left + m.clientX - r0.x, nt = r0.top + m.clientY - r0.y;
              const a = this.nearestAnchor(nl, nt, r0.w, box.offsetHeight, this.marginOf(it));
              if (a) { const p = this.anchorPx(a, r0.w, box.offsetHeight, this.marginOf(it)); nl = p.x; nt = p.y; }
              box.dataset.snap = a || "";
              box.style.outline = a ? "2px solid #2563eb" : "1px dashed #2563eb";
              box.style.left = `${nl}px`;
              box.style.top = `${nt}px`;
            } else {
              const nw = Math.max(r0.w + m.clientX - r0.x, 20);
              box.style.width = `${nw}px`;
              fill(nw);
            }
          };
          const up = () => {
            document.removeEventListener("pointermove", move);
            document.removeEventListener("pointerup", up);
            this.map.dragging.enable();
            delete box.dataset.dragging;
            box.style.outline = "1px dashed transparent";
            const nw = parseFloat(box.style.width), nl = parseFloat(box.style.left), nt = parseFloat(box.style.top);
            const [fx, fy] = this.pxToMap(nl, nt);
            const upd = mode === "move" ? { anchor: box.dataset.snap || null }
                                        : { auto: false, anchor: it.anchor || null };     // a manual size
            delete box.dataset.snap;
            const next = (this.legends || []).map((o, j) => (j === i
              ? { ...o, ...upd, x: fx, y: fy, w: nw / this.mapWidthPx(), frame: "map" } : { ...o, frame: "map" }));
            this.$emit("legends", { items: next });
          };
          document.addEventListener("pointermove", move);
          document.addEventListener("pointerup", up);
        };
        box.addEventListener("pointerdown", (e) => start(e, e.target === handle ? "resize" : "move"));
        box.addEventListener("click", (e) => e.stopPropagation());
        box.addEventListener("dblclick", (e) => e.stopPropagation());
        this.legendBox.appendChild(box);
        if (it.anchor && box.style.display !== "none") {           // snapped: place it in its corner (needs height)
          const p = this.anchorPx(it.anchor, wpx, box.offsetHeight, this.marginOf(it));
          box.style.left = `${p.x}px`;
          box.style.top = `${p.y}px`;
          const [fx, fy] = this.pxToMap(p.x, p.y);
          const fw = wpx / MW;
          if (Math.abs(fx - it.x) > 1e-4 || Math.abs(fy - it.y) > 1e-4 || Math.abs(fw - it.w) > 1e-4)
            fix.push([i, fx, fy, fw]);
        } else if (it.auto && Math.abs(wpx / MW - it.w) > 1e-4) {
          fix.push([i, it.x, it.y, wpx / MW]);
        }
      });
      if (fix.length) {                        // keep the stored fractions = what is shown (exports use them)
        const next = items.map((o, j) => { const f = fix.find((q) => q[0] === j);
          return f ? { ...o, x: f[1], y: f[2], w: f[3], frame: "map" } : { ...o, frame: "map" }; });
        this.$emit("legends", { items: next });
      }
    },
  },
};
