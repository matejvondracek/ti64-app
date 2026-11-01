// Map viewer (phase 5): Leaflet (bundled with NiceGUI) on a flat CRS in micrometres.
//   lng = x (um, right), lat = y (um, down); zoom 0 = `base` um per screen pixel.
//   Base layer and overlays are server tiles (/api/ti64/...): pixels coloured by the active backend,
//   placed by ti64app.raster. Annotations / ROIs are vectors in map coordinates; drawing tools emit
//   'shape' events to Python, which records them as an annotations.set recipe step.
import { leaflet as L } from "nicegui-leaflet";

const TOOLS_2PT = ["arrow", "line", "roi_rect", "measure"];
const TOOLS_MULTI = ["polyline", "crack", "roi_poly"];
const TOOLS_1PT = ["text", "point", "probe"];

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
    this.fit();
    this.drawShapes();
    this.makeScaleBar();
    this.map.on("zoomend", () => { this.drawShapes(); this.updateScaleBar(); this.emitView(); });
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
    extent() { this.fit(); },
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
    zoomTo(x0, y0, x1, y1) {
      this.map.fitBounds(L.latLngBounds(this.ll(x0, y0), this.ll(x1, y1)), { padding: [20, 20] });
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
      this.overlayLayers = (this.overlays || []).map((o, i) =>
        L.tileLayer(o.url, this.tileOptions(o.opacity, 10 + i)).addTo(this.map));
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
      el.style.display = this.scale_bar ? "" : "none";
      const umPerPx = this.pxToUm(1);
      const target = 120 * umPerPx;
      const p = Math.pow(10, Math.floor(Math.log10(target)));
      let len = p;
      for (const m of [5, 2, 1]) { if (m * p <= target) { len = m * p; break; } }
      el.querySelector(".bar").style.width = `${len / umPerPx}px`;
      el.querySelector(".lab").textContent = len >= 1000 ? `${len / 1000} mm` : `${+len.toPrecision(3)} µm`;
    },
  },
};
