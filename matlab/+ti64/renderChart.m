function renderChart(centers, heights, o, outPng)
% RENDERCHART  Bar chart of a ChartSpec (python/ti64app/backends/base.py) with MATLAB graphics.
%   o: struct bar_width, xlabel, ylabel, title, log_y, vline_x (vector), vline_labels (cell),
%   color (1x3), width_cm, dpi; optional curve_x, curve_y (fit curves, one vector each, curves separated
%   by NaN) and curve_labels (cell, one per curve), drawn as lines with a legend.
f = figure('Visible', 'off', 'Color', 'w', 'Units', 'centimeters');
f.Position = [1 1 o.width_cm 0.62 * o.width_cm];
ax = axes(f);
bar(ax, double(centers(:)), double(heights(:)), 1, 'FaceColor', double(o.color(:)'), 'EdgeColor', 'none');
ax.Children(1).BarWidth = 1;
if numel(centers) > 1, ax.Children(1).BarWidth = o.bar_width / min(diff(sort(double(centers(:))))); end
if o.log_y, set(ax, 'YScale', 'log'); end
lab = cellstr(o.vline_labels);
for k = 1:numel(o.vline_x)
  xline(ax, o.vline_x(k), 'r--', lab{k}, 'LineWidth', 1.5);
end
if isfield(o, 'curve_x') && ~isempty(o.curve_x)
  hold(ax, 'on');
  cx = double(o.curve_x(:)); cy = double(o.curve_y(:));
  brk = [0; find(isnan(cx)); numel(cx) + 1];
  labs = cellstr(o.curve_labels);
  cols = [0.85 0.33 0.10; 0.10 0.10 0.10; 0.47 0.67 0.19];
  hl = gobjects(0);
  for k = 1:numel(brk) - 1
    r = brk(k) + 1:brk(k + 1) - 1;
    if isempty(r), continue; end
    hl(end + 1) = plot(ax, cx(r), cy(r), '-', 'Color', cols(1 + mod(numel(hl), 3), :), 'LineWidth', 1.5); %#ok<AGROW>
  end
  if ~isempty(hl), legend(ax, hl, labs(1:numel(hl)), 'Interpreter', 'none', 'FontSize', 8, 'Box', 'off'); end
  hold(ax, 'off');
end
xlabel(ax, char(o.xlabel), 'Interpreter', 'none'); ylabel(ax, char(o.ylabel), 'Interpreter', 'none');
if strlength(string(o.title)) > 0, title(ax, char(o.title), 'FontSize', 9, 'Interpreter', 'none'); end
exportgraphics(f, outPng, 'Resolution', o.dpi, 'BackgroundColor', 'white');
close(f);
end
