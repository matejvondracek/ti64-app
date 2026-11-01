function render(id, h5in, outPng, o)
% RENDER  Draw a RenderSpec (python/ti64app/backends/base.py) for a stored map with the MATLAB
%   backend: hex-raster image (ti64.hexRaster), boundary overlay segments, colour bar, legend,
%   scale bar, exported with exportgraphics. Map convention: east = +x, y down (setting 2).
%   h5in: /rgb (n x 3, file order, NaN = white) or /values (n, file order); optional /seg (m x 4:
%   x1 y1 x2 y2), /seg_rgb (m x 3) and optional /seg_w (m, line widths). o: struct cmap, clim, colorbar_label, seg_width, title,
%   width_cm, dpi, scale_bar, legend_labels (cell), legend_rgb (k x 3).
%   Phase 5 (export): o.exact (figure exactly width_cm wide, printed at dpi; the map fills it when there is
%   no title / colour bar / legend), o.xlim / o.ylim (ROI, um; NaN = whole map), annotation primitives in
%   h5in: /pl_xy (polylines separated by NaN), /pl_style (k x 4: r g b width_pt), /pg_xy, /pg_rgb
%   (filled polygons), /tx_xy (k x 2), /tx_style (k x 4: r g b size_pt) with o.text_str (cell) and
%   o.text_ha (cell). Output format from the extension of outPng: .png .tif .svg .pdf.

S = ti64.store('get', id);
if isstruct(S), ebsd = S.ebsd; else, ebsd = S; end
R = ti64.hexRaster(ebsd);
info = h5info(h5in);
names = {info.Datasets.Name};
img = 255 * ones(R.H * R.W, 3, 'uint8');
cmapName = char(o.cmap);
if ismember('rgb', names)
  c = h5read(h5in, '/rgb')';                          % n x 3
else
  v = h5read(h5in, '/values'); v = v(:);
  if isempty(o.clim) || any(isnan(o.clim))
    lim = [min(v, [], 'omitnan') max(v, [], 'omitnan')];
  else
    lim = double(o.clim(:)');
  end
  if lim(2) <= lim(1), lim(2) = lim(1) + 1; end
  cm = feval(cmapName, 256);
  k = 1 + round(255 * min(max((v - lim(1)) / (lim(2) - lim(1)), 0), 1));
  c = nan(numel(v), 3);
  ok = ~isnan(v);
  c(ok, :) = cm(k(ok), :);
end
n = size(c, 1);
ok = all(isfinite(c), 2);
cc = uint8(round(255 * min(max(c(ok, :), 0), 1)));
rows = find(ok);
img(R.ind1(rows), :) = cc;
img(R.ind2(rows), :) = cc;
img = reshape(img, R.H, R.W, 3);

f = figure('Visible', 'off', 'Color', 'w', 'Units', 'centimeters');
ax = axes(f);
image(ax, 'XData', R.xdata, 'YData', R.ydata, 'CData', img);
axis(ax, 'image'); set(ax, 'YDir', 'reverse');              % y down, as the map convention
hold(ax, 'on');
if ismember('seg', names)
  sg = h5read(h5in, '/seg')'; sc = h5read(h5in, '/seg_rgb')';
  if ismember('seg_w', names), sw = h5read(h5in, '/seg_w'); sw = sw(:); else, sw = o.seg_width * ones(size(sg, 1), 1); end
  [u, ~, grp] = unique([sc sw], 'rows', 'stable');     % keep the drawing order (first = bottom)
  for k = 1:size(u, 1)
    s = sg(grp == k, :);
    X = [s(:, 1) s(:, 3) nan(size(s, 1), 1)]'; Y = [s(:, 2) s(:, 4) nan(size(s, 1), 1)]';
    plot(ax, X(:), Y(:), '-', 'Color', u(k, 1:3), 'LineWidth', u(k, 4));
  end
end
if isfield(o, 'legend_labels') && ~isempty(o.legend_labels)
  lab = cellstr(o.legend_labels); lc = reshape(double(o.legend_rgb), 3, [])';
  hh = gobjects(numel(lab), 1);
  for k = 1:numel(lab), hh(k) = plot(ax, nan, nan, '-', 'Color', lc(k, :), 'LineWidth', 2); end
  nc = min(numel(lab), 6);                                  % wrap long legends (as pymtex, ncol <= 6)
  if sum(strlength(lab)) > 60, nc = min(nc, 3); end
  legend(ax, hh, lab, 'Location', 'southoutside', 'NumColumns', nc, 'Box', 'off', 'FontSize', 7, ...
         'AutoUpdate', 'off');
end
if ~ismember('rgb', names) && strlength(string(o.colorbar_label)) > 0
  colormap(ax, feval(cmapName, 256)); clim(ax, lim);
  cb = colorbar(ax); cb.Label.String = char(o.colorbar_label);
end
drawPrims(ax, h5in, names, o);
lims = [];
if isfield(o, 'xlim') && ~any(isnan(o.xlim))
  xlim(ax, sort(double(o.xlim))); ylim(ax, sort(double(o.ylim)));
  lims = [sort(double(o.xlim)) sort(double(o.ylim))];
end
if o.scale_bar, scaleBar(ax, R, lims); end
if strlength(string(o.title)) > 0, title(ax, char(o.title), 'FontSize', 9, 'Interpreter', 'none'); end
axis(ax, 'off');
w = o.width_cm;
if isfield(o, 'exact') && o.exact
  printExact(f, ax, o, outPng);
else
  f.Position = [1 1 w w * diff(R.ydata) / diff(R.xdata) + 1.5];
  exportgraphics(f, outPng, 'Resolution', o.dpi, 'BackgroundColor', 'white');
end
close(f);
end

function xy = readXY(h5in, name)
% polyline / polygon coordinates as 2 x N (rows x, y), NaN columns separate the items
% (h5py writes N x 2 row-major; h5read returns it transposed)
xy = h5read(h5in, ['/' name]);
end

function st = readRows(h5in, name, ~)
% k x m table written by h5py (row-major) -> transpose back
st = h5read(h5in, ['/' name])';
end

function drawPrims(ax, h5in, names, o)
% annotation primitives (python/ti64app/annotations.py to_primitives), map coordinates
if ismember('pl_xy', names)
  xy = readXY(h5in, 'pl_xy'); st = readRows(h5in, 'pl_style', 4);
  brk = [0 find(isnan(xy(1, :))) size(xy, 2) + 1];
  k = 0;
  for b = 1:numel(brk) - 1
    r = brk(b) + 1:brk(b + 1) - 1;
    if isempty(r), continue; end
    k = k + 1;
    plot(ax, xy(1, r), xy(2, r), '-', 'Color', st(k, 1:3), 'LineWidth', st(k, 4));
  end
end
if ismember('pg_xy', names)
  xy = readXY(h5in, 'pg_xy'); c = readRows(h5in, 'pg_rgb', 3);
  brk = [0 find(isnan(xy(1, :))) size(xy, 2) + 1];
  k = 0;
  for b = 1:numel(brk) - 1
    r = brk(b) + 1:brk(b + 1) - 1;
    if isempty(r), continue; end
    k = k + 1;
    patch(ax, xy(1, r), xy(2, r), c(k, :), 'EdgeColor', 'none');
  end
end
if ismember('tx_xy', names)
  xy = readRows(h5in, 'tx_xy', 2); st = readRows(h5in, 'tx_style', 4);
  str = cellstr(o.text_str); ha = cellstr(o.text_ha);
  for k = 1:size(xy, 1)
    text(ax, xy(k, 1), xy(k, 2), str{k}, 'Color', st(k, 1:3), 'FontSize', st(k, 4), ...
         'HorizontalAlignment', ha{k}, 'VerticalAlignment', 'middle', 'Interpreter', 'none', 'Clipping', 'on');
  end
end
end

function printExact(f, ax, o, out)
% figure exactly o.width_cm wide (raster: round(width / 2.54 * dpi) pixels), printed with a fixed paper size.
% Bare map (no title / colour bar / legend): the map fills the figure. Otherwise the axes, colour bar and
% legend are placed explicitly (cm) inside the fixed width, as backends/pymtex_plot._save_exact does.
w = o.width_cm;
px = 2.54 / o.dpi;                                   % one output pixel in cm
xl = xlim(ax); yl = ylim(ax);
aspect = diff(yl) / diff(xl);
cbh = findobj(f, 'Type', 'ColorBar');
lgh = findobj(f, 'Type', 'Legend');
tt = strlength(string(o.title)) > 0;
f.Units = 'centimeters';
if isempty(cbh) && isempty(lgh) && ~tt
  h = max(1, round(w * aspect / px)) * px;
  f.Position = [1 1 w h];
  ax.Units = 'normalized'; ax.Position = [0 0 1 1];
else
  mw = w * (0.74 * ~isempty(cbh) + 0.96 * isempty(cbh));
  mh = mw * aspect;
  nl = 0;
  if tt
    lines = wrapWords(char(o.title), max(20, floor(w * 7)));
    title(ax, lines, 'FontSize', 7, 'Interpreter', 'none', 'FontWeight', 'normal');
    nl = numel(lines);
  end
  lgH = 0;
  if ~isempty(lgh)
    lgh.FontSize = 7; lgh.Units = 'centimeters';
    lp = lgh.Position;
    while lp(3) > 0.98 * w && lgh.NumColumns > 1
      lgh.NumColumns = lgh.NumColumns - 1; lp = lgh.Position;
    end
    lgH = lp(4) + 0.15;
  end
  h = round((mh + 0.32 * nl + 0.15 * tt + lgH + 0.25) / px) * px;
  f.Position = [1 1 w h];
  ax.Units = 'centimeters';
  ax.Position = [0.02 * w, lgH + 0.1, mw, mh];
  if ~isempty(lgh), lgh.Position = [max(0, (w - lp(3)) / 2), 0.05, lp(3), lp(4)]; end
  if ~isempty(cbh)
    cbh.FontSize = 7; cbh.Label.FontSize = 7;
    cbh.Units = 'centimeters';
    cbh.Position = [0.02 * w + mw + 0.015 * w, lgH + 0.1, 0.025 * w, mh];
  end
end
f.PaperUnits = 'centimeters'; f.PaperSize = f.Position(3:4); f.PaperPosition = [0 0 f.Position(3:4)];
f.InvertHardcopy = 'off';
[~, ~, ext] = fileparts(out);
r = sprintf('-r%d', round(o.dpi));
switch lower(ext)
  case '.png', print(f, out, '-dpng', r);
  case {'.tif', '.tiff'}, print(f, out, '-dtiff', r);
  case '.svg', print(f, out, '-dsvg', '-vector', r);
  case '.pdf', print(f, out, '-dpdf', '-vector', r);
  otherwise, error('ti64:render', 'unknown format %s', ext);
end
end

function lines = wrapWords(str, n)
% split a title into lines of at most n characters at spaces
words = strsplit(str, ' ');
lines = {};
cur = '';
for k = 1:numel(words)
  if isempty(cur), cand = words{k}; else, cand = [cur ' ' words{k}]; end
  if numel(cand) > n && ~isempty(cur)
    lines{end + 1} = cur; cur = words{k}; %#ok<AGROW>
  else
    cur = cand;
  end
end
if ~isempty(cur), lines{end + 1} = cur; end
end

function scaleBar(ax, R, lims)
% scale bar of a 'nice' length (~1/5 of the shown width), bottom left, white box; lims = ROI or []
if isempty(lims), xd = R.xdata; yd = R.ydata; else, xd = lims(1:2); yd = lims(3:4); end
W = diff(xd);
L = 10^floor(log10(W / 5)); for m = [5 2 1], if m * L <= W / 4, L = m * L; break; end, end
x0 = xd(1) + 0.04 * W; y0 = yd(2) - 0.05 * diff(yd);
rectangle(ax, 'Position', [x0 - 0.01 * W, y0 - 0.08 * diff(yd), L + 0.02 * W, 0.11 * diff(yd)], ...
  'FaceColor', [1 1 1 0.8], 'EdgeColor', 'none');
plot(ax, [x0 x0 + L], [y0 y0], 'k-', 'LineWidth', 3);
text(ax, x0 + L / 2, y0 - 0.02 * diff(yd), sprintf('%g %sm', L, char(181)), ...
  'HorizontalAlignment', 'center', 'VerticalAlignment', 'bottom', 'FontSize', 8);
end
