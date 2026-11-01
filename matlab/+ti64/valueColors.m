function valueColors(h5in, cmapName, clim, h5out)
% VALUECOLORS  Per-measurement RGBA colours of scalar values with a MATLAB colormap (map viewer and
%   pixel-exact export, phase 5). Same rule as ti64.render: 256 levels, k = 1 + round(255 t),
%   limits = clim, or [min max] of the finite values when clim is empty / NaN.
%   h5in: /values (n). h5out: /rgba (n x 4 uint8, alpha 0 = no value), /lut (256 x 4 uint8), /clim (2).
v = h5read(h5in, '/values'); v = double(v(:));
if isempty(clim) || any(isnan(clim))
  lim = [min(v, [], 'omitnan') max(v, [], 'omitnan')];
  if any(isnan(lim)), lim = [0 1]; end
else
  lim = double(clim(:)');
end
if lim(2) <= lim(1), lim(2) = lim(1) + 1; end
cm = feval(char(cmapName), 256);
lut = uint8(round(255 * [cm ones(256, 1)]));
ok = isfinite(v);
k = 1 + round(255 * min(max((v(ok) - lim(1)) / (lim(2) - lim(1)), 0), 1));
rgba = zeros(numel(v), 4, 'uint8');
rgba(ok, :) = lut(k, :);
if exist(h5out, 'file'), delete(h5out); end
h5create(h5out, '/rgba', fliplr(size(rgba)), 'Datatype', 'uint8');
h5write(h5out, '/rgba', rgba');                     % Python reads (n, 4)
h5create(h5out, '/lut', [4 256], 'Datatype', 'uint8');
h5write(h5out, '/lut', lut');
h5create(h5out, '/clim', 2);
h5write(h5out, '/clim', lim(:));
end
