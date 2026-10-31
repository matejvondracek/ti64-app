function R = hexRaster(ebsd)
% HEXRASTER  Map from FILE rows of a stored map to a raster image (ported from EBSD_part2.m).
%   Hex grid: every hex pixel fills two half-width image columns, odd rows shifted by half a
%   column -> exact brick-layout copy of the hex grid (2.6 M pixels draw as ONE image).
%   Square grid: one image column per pixel. R.ind1 / R.ind2 are indexed by file row (oldId).
%   MTEX 7: EBSD.load returns grid-ordered data, so rows are mapped through prop.oldId.

oldId = ebsd.prop.oldId(:);
real = ~isnan(oldId);
x = ebsd.x(:); y = ebsd.y(:);
x = x(real); y = y(real); oid = oldId(real);
ys = unique(round(y * 1e4) / 1e4);
dy = median(diff(ys));
row = round((y - ys(1)) / dy) + 1;
xr = sort(x(row == 1));
dx = median(diff(xr));
xmin = min(x);
isHex = isa(ebsd, 'EBSDhex');
if isHex
  h = round((x - xmin) / (dx / 2));               % half-column index (0-based)
  R.W = max(h) + 2;
  c1 = h + 1; c2 = h + 2;
  R.xdata = xmin - dx / 4 + [0, (R.W - 1) * dx / 2];
else
  h = round((x - xmin) / dx);
  R.W = max(h) + 1;
  c1 = h + 1; c2 = c1;
  R.xdata = xmin + [0, (R.W - 1) * dx];
end
R.H = max(row);
n = max(oid);
R.ind1 = zeros(n, 1); R.ind2 = zeros(n, 1);
R.ind1(oid) = sub2ind([R.H R.W], row, c1);
R.ind2(oid) = sub2ind([R.H R.W], row, c2);
R.ydata = ys(1) + [0, (R.H - 1) * dy];
R.n = n;
end
