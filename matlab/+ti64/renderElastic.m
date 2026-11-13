function renderElastic(h5in, spec, out)
% RENDERELASTIC  E(direction) as a stereographic plot in the style of stereograph_settings.m (turbo, dotted centre
%   cross, bold direction labels, colour bar 'E (GPa)' with decimal commas) and a 3D surface (phase 6.7; same content
%   as PymtexTexture.render_elastic). Saved as `out` and, for a PNG, also as .fig.
%   h5in: /E (n_rho x n_phi), /rho, /phi (same size, rad), /E3, /theta3, /phi3 (full sphere)
%   spec: axisLabels {x y z}, markerLabels (cell), markerVectors (m x 3), markerE (m), title, widthCm, dpi
E = h5read(h5in, '/E')'; rho = h5read(h5in, '/rho')'; phi = h5read(h5in, '/phi')';
E3 = h5read(h5in, '/E3')'; th3 = h5read(h5in, '/theta3')'; ph3 = h5read(h5in, '/phi3')';
r = tan(rho / 2);                                          % stereographic projection
X = r .* cos(phi); Y = r .* sin(phi);
cmin = min(E(:)); cmax = max(E(:));
f = figure('Visible', 'off', 'Color', 'w', 'Units', 'centimeters', 'Position', [0 0 spec.widthCm spec.widthCm * 0.5]);
ax = subplot(1, 2, 1);
surf(ax, X, Y, zeros(size(X)), E, 'EdgeColor', 'none', 'FaceColor', 'interp');
view(ax, 2); axis(ax, 'equal', 'tight', 'off'); hold(ax, 'on');
t = linspace(-1, 1, 41);
plot(ax, t, zeros(size(t)), 'k.', 'MarkerSize', 5);
plot(ax, zeros(size(t)), t, 'k.', 'MarkerSize', 5);
lab = cellstr(spec.axisLabels);
text(ax, 1.0, -0.1, lab{1}, 'HorizontalAlignment', 'right', 'VerticalAlignment', 'top', 'FontWeight', 'bold', 'FontSize', 7);
text(ax, -0.04, 1.08, lab{2}, 'HorizontalAlignment', 'right', 'VerticalAlignment', 'bottom', 'FontWeight', 'bold', 'FontSize', 7);
text(ax, -0.08, 0.08, lab{3}, 'HorizontalAlignment', 'right', 'Color', 'w', 'FontWeight', 'bold', 'FontSize', 7);
M = spec.markerVectors;
for k = 1:numel(spec.markerLabels)
  v = M(k, :) / norm(M(k, :)); if v(3) < 0, v = -v; end
  rr = tan(acos(min(max(v(3), -1), 1)) / 2); a = atan2(v(2), v(1));
  plot(ax, rr * cos(a), rr * sin(a), 'wo', 'MarkerFaceColor', 'w', 'MarkerEdgeColor', 'k', 'MarkerSize', 4);
  text(ax, rr * cos(a) + 0.03, rr * sin(a) + 0.03, sprintf('%s\n%s', spec.markerLabels{k}, ...
    strrep(sprintf('%.1f', spec.markerE(k)), '.', ',')), 'FontSize', 6);
end
clim(ax, [cmin cmax]); colormap(ax, turbo(1024));
cb = colorbar(ax); cb.Label.String = 'E (GPa)'; cb.FontSize = 7; cb.Label.FontSize = 8;
cb.Ticks = linspace(cmin + 0.001, cmax - 0.001, 6);
cb.TickLabels = strrep(compose('%.1f', cb.Ticks), '.', ',');
ax3 = subplot(1, 2, 2);
Xs = E3 .* sin(th3) .* cos(ph3); Ys = E3 .* sin(th3) .* sin(ph3); Zs = E3 .* cos(th3);
surf(ax3, Xs, Ys, Zs, E3, 'EdgeColor', 'none', 'FaceColor', 'interp');
axis(ax3, 'equal', 'off'); clim(ax3, [cmin cmax]); colormap(ax3, turbo(1024)); view(ax3, 135, 25);
m = max(E3(:)); hold(ax3, 'on');
ax_ = eye(3);
for k = 1:3
  plot3(ax3, [0 1.25 * m * ax_(k, 1)], [0 1.25 * m * ax_(k, 2)], [0 1.25 * m * ax_(k, 3)], 'k-');
  text(ax3, 1.3 * m * ax_(k, 1), 1.3 * m * ax_(k, 2), 1.3 * m * ax_(k, 3), lab{k}, 'FontSize', 6, 'Interpreter', 'none');
end
if ~isempty(spec.title), sgtitle(f, spec.title, 'FontSize', 9, 'Interpreter', 'none'); end
ti64.plot.saveFigure(f, out, spec.dpi);
[p, nm, ext] = fileparts(char(out));
if strcmpi(ext, '.png'), ti64.plot.saveFigure(f, fullfile(p, [nm '.fig']), spec.dpi); end
close(f);
end
