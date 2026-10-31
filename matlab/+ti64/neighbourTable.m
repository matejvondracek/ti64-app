function nbr = neighbourTable(x, y, valid, isHex)
% NEIGHBOURTABLE  Neighbour indices of every pixel (same algorithm as
%   python/ti64app/backends/hexgrid.py neighbour_table; keep both identical).
%   nbr = ti64.neighbourTable(x, y, valid, isHex)   x, y, valid: vectors (grid order)
%   nbr: n x k (k = 6 hex, 4 square), 0 where there is no neighbour.
%   Fixed neighbour ORDER (tie-breaking in ti64.dilationFill): W, E, NW, NE, SW, SE.

x = double(x(:)); y = double(y(:)); valid = logical(valid(:));
ys = unique(round(y(valid) * 1e4) / 1e4);
if numel(ys) > 1, dy = median(diff(ys)); else, dy = 1; end
row = round((y - ys(1)) / dy);
xr = sort(x(valid & row == 0));
if numel(xr) > 1, dx = median(diff(xr)); else, dx = 1; end
xmin = min(x(valid));
if isHex
  col = round((x - xmin) / (dx / 2));
  offs = [0 -2; 0 2; -1 -1; -1 1; 1 -1; 1 1];
else
  col = round((x - xmin) / dx);
  offs = [0 -1; 0 1; -1 0; 1 0];
end
ncol = max(col(valid)) + 3;
key = row * ncol + col;
vi = find(valid);
[sk, order] = sort(key(valid));
si = vi(order);
nbr = zeros(numel(x), size(offs, 1));
for j = 1:size(offs, 1)
  k = (row + offs(j, 1)) * ncol + (col + offs(j, 2));
  [hit, loc] = ismember(k, sk);
  hit = hit & valid & (col + offs(j, 2) >= 0);
  nbr(hit, j) = si(loc(hit));
end
end
