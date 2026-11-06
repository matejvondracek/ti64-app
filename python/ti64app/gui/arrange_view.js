// Arrange view (Stitch tab): tile thumbnails at their (stage or manual) positions in image pixels.
//   wheel = zoom at the cursor, drag the background = pan, drag a tile = move it (not when locked),
//   click = select only this tile, Shift / Ctrl + click = add / remove, Shift + drag on the background = box select,
//   double click on the background = fit, arrow keys = move the selected tiles by 1 px (Shift: 10 px).
// Emits: moved {moves: [{key, x, y}]} after a drag / key move, select {keys}.
export default {
  template: `
    <div class="ti64-arrange" tabindex="0" style="position:relative;width:100%;height:100%;overflow:hidden;
         background:#e5e7eb;cursor:grab;outline:none" @wheel.prevent="onWheel" @pointerdown="onDown"
         @dblclick="onDbl" @keydown="onKey">
      <div :style="innerStyle">
        <div v-for="t in tiles" :key="t.key" :data-key="t.key" :title="t.name"
             :style="tileStyle(t)" @pointerdown.stop="onTileDown($event, t)">
          <img :src="t.url" draggable="false" style="width:100%;height:100%;display:block;pointer-events:none;
               user-select:none" />
          <div :style="labelStyle(t)">{{ t.label }}{{ t.locked ? ' 🔒' : '' }}</div>
        </div>
      </div>
      <div v-if="box" :style="boxStyle"></div>
      <div style="position:absolute;right:8px;bottom:6px;font:11px sans-serif;background:rgba(255,255,255,.85);
           padding:2px 6px;border-radius:4px">{{ Math.round(scale * 1000) / 10 }} % · {{ selCount }} selected</div>
    </div>`,
  props: {
    tiles: Array,          // [{key, name, label, url, x, y, w, h, locked, selected}] (x, y, w, h in image px)
    fit_token: Number,     // change to fit the view to all tiles
  },
  data() {
    return { scale: 0.1, tx: 0, ty: 0, drag: null, box: null };
  },
  computed: {
    innerStyle() {
      return `position:absolute;left:0;top:0;transform-origin:0 0;transform:translate(${this.tx}px,${this.ty}px) scale(${this.scale})`;
    },
    boxStyle() {
      const b = this.box;
      const x = Math.min(b.x0, b.x1), y = Math.min(b.y0, b.y1);
      return `position:absolute;left:${x}px;top:${y}px;width:${Math.abs(b.x1 - b.x0)}px;height:${Math.abs(b.y1 - b.y0)}px;
              border:1px dashed #2563eb;background:rgba(37,99,235,.12);pointer-events:none`;
    },
    selCount() { return (this.tiles || []).filter((t) => t.selected).length; },
  },
  watch: {
    fit_token() { this.$nextTick(() => this.fit()); },
  },
  mounted() {
    this.$nextTick(() => this.fit());
    this.ro = new ResizeObserver(() => { if (!this.fitted) this.fit(); });
    this.ro.observe(this.$el);
  },
  unmounted() { if (this.ro) this.ro.disconnect(); },
  methods: {
    tileStyle(t) {
      const border = t.selected ? "#2563eb" : "#6b7280";
      const w = Math.max(1, 2 / this.scale);
      return `position:absolute;left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;
              outline:${w}px ${t.locked ? "dashed" : "solid"} ${border};opacity:${t.selected ? 1 : 0.45};
              cursor:${t.locked ? "not-allowed" : "move"}`;
    },
    labelStyle(t) {
      const f = 12 / this.scale;
      return `position:absolute;left:${4 / this.scale}px;top:${4 / this.scale}px;font:600 ${f}px sans-serif;
              color:${t.selected ? "#1d4ed8" : "#374151"};background:rgba(255,255,255,.8);padding:0 ${3 / this.scale}px;
              border-radius:${3 / this.scale}px;pointer-events:none;white-space:nowrap`;
    },
    fit() {
      const ts = this.tiles || [];
      const r = this.$el.getBoundingClientRect();
      if (!ts.length || r.width < 10 || r.height < 10) return;
      const x0 = Math.min(...ts.map((t) => t.x)), y0 = Math.min(...ts.map((t) => t.y));
      const x1 = Math.max(...ts.map((t) => t.x + t.w)), y1 = Math.max(...ts.map((t) => t.y + t.h));
      const s = 0.92 * Math.min(r.width / (x1 - x0), r.height / (y1 - y0));
      this.scale = s;
      this.tx = (r.width - s * (x1 - x0)) / 2 - s * x0;
      this.ty = (r.height - s * (y1 - y0)) / 2 - s * y0;
      this.fitted = true;
    },
    local(e) {
      const r = this.$el.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    },
    toImage(p) { return [(p[0] - this.tx) / this.scale, (p[1] - this.ty) / this.scale]; },
    onWheel(e) {
      const p = this.local(e);
      const k = Math.exp(-e.deltaY * 0.0015);
      const s = Math.min(8, Math.max(0.002, this.scale * k));
      this.tx = p[0] - (p[0] - this.tx) * s / this.scale;
      this.ty = p[1] - (p[1] - this.ty) * s / this.scale;
      this.scale = s;
    },
    onDbl() { this.fit(); },
    onDown(e) {
      this.$el.focus();
      const p = this.local(e);
      if (e.shiftKey) {
        this.box = { x0: p[0], y0: p[1], x1: p[0], y1: p[1] };
      } else {
        this.drag = { kind: "pan", p, tx: this.tx, ty: this.ty, moved: false };
      }
      this.listen();
    },
    onTileDown(e, t) {
      this.$el.focus();
      const p = this.local(e);
      this.drag = { kind: "tile", t, p, x: t.x, y: t.y, moved: false, add: e.shiftKey || e.ctrlKey || e.metaKey };
      this.listen();
    },
    listen() {
      const move = (e) => this.onMove(e);
      const up = (e) => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up);
                          this.onUp(e); };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    },
    onMove(e) {
      const p = this.local(e);
      if (this.box) { this.box.x1 = p[0]; this.box.y1 = p[1]; return; }
      const d = this.drag;
      if (!d) return;
      const dx = p[0] - d.p[0], dy = p[1] - d.p[1];
      if (Math.hypot(dx, dy) > 3) d.moved = true;
      if (d.kind === "pan") { this.tx = d.tx + dx; this.ty = d.ty + dy; }
      else if (!d.t.locked && d.moved) { d.t.x = d.x + dx / this.scale; d.t.y = d.y + dy / this.scale; }
    },
    onUp(e) {
      if (this.box) {
        const b = this.box;
        const a = this.toImage([Math.min(b.x0, b.x1), Math.min(b.y0, b.y1)]);
        const c = this.toImage([Math.max(b.x0, b.x1), Math.max(b.y0, b.y1)]);
        const hit = (this.tiles || []).filter((t) => t.x < c[0] && t.x + t.w > a[0] && t.y < c[1] && t.y + t.h > a[1])
          .map((t) => t.key);
        const keep = e.ctrlKey || e.metaKey ? (this.tiles || []).filter((t) => t.selected).map((t) => t.key) : [];
        this.box = null;
        this.$emit("select", { keys: [...new Set([...keep, ...hit])] });
        return;
      }
      const d = this.drag;
      this.drag = null;
      if (!d) return;
      if (d.kind === "tile") {
        if (d.moved && !d.t.locked) {
          this.$emit("moved", { moves: [{ key: d.t.key, x: d.t.x, y: d.t.y }] });
        } else if (!d.moved) {
          const sel = (this.tiles || []).filter((t) => t.selected).map((t) => t.key);
          let keys;
          if (d.add) keys = sel.includes(d.t.key) ? sel.filter((k) => k !== d.t.key) : [...sel, d.t.key];
          else keys = [d.t.key];
          this.$emit("select", { keys });
        }
      }
    },
    onKey(e) {
      const step = e.shiftKey ? 10 : 1;
      const dd = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
      if (e.key === "a" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        this.$emit("select", { keys: (this.tiles || []).map((t) => t.key) });
        return;
      }
      if (!dd) return;
      e.preventDefault();
      const moves = [];
      for (const t of this.tiles || []) {
        if (t.selected && !t.locked) { t.x += dd[0]; t.y += dd[1]; moves.push({ key: t.key, x: t.x, y: t.y }); }
      }
      if (moves.length) this.$emit("moved", { moves });
    },
  },
};
