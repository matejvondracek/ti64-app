function render(id, h5in, outPng, o)
% RENDER  Draw a RenderSpec (python/ti64app/backends/base.py) for a stored map with the MATLAB
%   backend: hex-raster image (ti64.hexRaster), boundary overlay segments, colour bar, legend,
%   scale bar, exported with exportgraphics. Map convention: east = +x, y down (setting 2).
%   h5in: /rgb (n x 3, file order, NaN = white) or /values (n, file order); optional /seg (m x 4:
%   x1 y1 x2 y2) and /seg_rgb (m x 3). o: struct cmap, clim, colorbar_label, seg_width, title,
%   width_cm, dpi, scale_bar, legend_labels (cell), legend_rgb (k x 3).

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
  [u, ~, grp] = unique(sc, 'rows');
  for k = 1:size(u, 1)
    s = sg(grp == k, :);
    X = [s(:, 1) s(:, 3) nan(size(s, 1), 1)]'; Y = [s(:, 2) s(:, 4) nan(size(s, 1), 1)]';
    plot(ax, X(:), Y(:), '-', 'Color', u(k, :), 'LineWidth', o.seg_width);
  end
end
if isfield(o, 'legend_labels') && ~isempty(o.legend_labels)
  lab = cellstr(o.legend_labels); lc = reshape(double(o.legend_rgb), 3, [])';
  hh = gobjects(numel(lab), 1);
  for k = 1:numel(lab), hh(k) = plot(ax, nan, nan, '-', 'Color', lc(k, :), 'LineWidth', 2); end
  legend(ax, hh, lab, 'Location', 'southoutside', 'Orientation', 'horizontal', 'Box', 'off', 'FontSize', 7, ...
         'AutoUpdate', 'off');
end
if ~ismember('rgb', names) && strlength(string(o.colorbar_label)) > 0
  colormap(ax, feval(cmapName, 256)); clim(ax, lim);
  cb = colorbar(ax); cb.Label.String = char(o.colorbar_label);
end
if o.scale_bar, scaleBar(ax, R); end
if strlength(string(o.title)) > 0, title(ax, char(o.title), 'FontSize', 9, 'Interpreter', 'none'); end
axis(ax, 'off');
w = o.width_cm;
f.Position = [1 1 w w * diff(R.ydata) / diff(R.xdata) + 1.5];
exportgraphics(f, outPng, 'Resolution', o.dpi, 'BackgroundColor', 'white');
close(f);
end

function scaleBar(ax, R)
% scale bar of a 'nice' length (~1/5 of the map width), bottom left, white box
W = diff(R.xdata);
L = 10^floor(log10(W / 5)); for m = [5 2 1], if m * L <= W / 4, L = m * L; break; end, end
x0 = R.xdata(1) + 0.04 * W; y0 = R.ydata(2) - 0.05 * diff(R.ydata);
rectangle(ax, 'Position', [x0 - 0.01 * W, y0 - 0.08 * diff(R.ydata), L + 0.02 * W, 0.11 * diff(R.ydata)], ...
  'FaceColor', [1 1 1 0.8], 'EdgeColor', 'none');
plot(ax, [x0 x0 + L], [y0 y0], 'k-', 'LineWidth', 3);
text(ax, x0 + L / 2, y0 - 0.02 * diff(R.ydata), sprintf('%g %sm', L, char(181)), ...
  'HorizontalAlignment', 'center', 'VerticalAlignment', 'bottom', 'FontSize', 8);
end
