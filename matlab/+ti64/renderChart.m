function renderChart(centers, heights, o, outPng)
% RENDERCHART  Bar chart of a ChartSpec (python/ti64app/backends/base.py) with MATLAB graphics.
%   o: struct bar_width, xlabel, ylabel, title, log_y, vline_x (vector), vline_labels (cell),
%   color (1x3), width_cm, dpi.
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
xlabel(ax, char(o.xlabel), 'Interpreter', 'none'); ylabel(ax, char(o.ylabel), 'Interpreter', 'none');
if strlength(string(o.title)) > 0, title(ax, char(o.title), 'FontSize', 9, 'Interpreter', 'none'); end
exportgraphics(f, outPng, 'Resolution', o.dpi, 'BackgroundColor', 'white');
close(f);
end
