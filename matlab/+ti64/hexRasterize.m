function img = hexRasterize(x, y, rgba, g, bg)
% HEXRASTERIZE  Regular hexagonal cells of a hex-grid map in a pixel grid (see hexImage.m, render.m):
%   x, y   measurement centres (um, any order, finite)
%   rgba   n x 4 uint8 colours (alpha 0 = background)
%   g      [x0 y0 px py W H]: top-left corner (um), pixel size (um), size (px); square pixels for regular hexagons
%   bg     1 x 4 uint8 background
%   A pixel shows the nearest measurement centre: the brick owner of its row or of the nearer adjacent row
%   (the Voronoi cell of a hexagonal grid is a regular hexagon of width dx).
n = numel(x);
ys = unique(round(y * 1e4) / 1e4);
dy = (max(y) - min(y)) / max(numel(ys) - 1, 1);
row = round((y - min(y)) / dy);
xr = sort(x(row == 0));
dx = (xr(end) - xr(1)) / max(numel(xr) - 1, 1);
h = round((x - min(x)) / (dx / 2));                     % half-column index (0-based)
R = max(row) + 1; C = max(h) + 2;
lut = zeros(R, C, 'int32');
lut(sub2ind([R C], row + 1, h + 1)) = int32(1:n)';
lut(sub2ind([R C], row + 1, h + 2)) = int32(1:n)';
x0 = min(x) - dx / 2; y0 = min(y) - dy / 2; cw = dx / 2;
gx0 = g(1); gy0 = g(2); px = g(3); py = g(4); W = g(5); H = g(6);
img = zeros(H, W, 4, 'uint8');
pxc = gx0 + ((0:W-1) + 0.5) * px;                       % pixel centres
c = floor((pxc - x0) / cw + 1e-7);
okc = c >= 0 & c < C;
chunk = max(1, floor(4e6 / W));
for r0 = 1:chunk:H
  rows = r0:min(H, r0 + chunk - 1);
  pyc = gy0 + ((rows - 1) + 0.5) * py;
  fr = (pyc - y0) / dy;
  r = floor(fr + 1e-7);
  rb = r + 1 - 2 * ((fr - r) < 0.5);                     % the nearer adjacent row
  [CC, RA] = meshgrid(c, r);
  [~, RB] = meshgrid(c, rb);
  [PX, PY] = meshgrid(pxc, pyc);
  OKC = repmat(okc, numel(rows), 1);
  ia = zeros(size(CC), 'int32'); ib = ia;
  m = OKC & RA >= 0 & RA < R;
  ia(m) = lut(sub2ind([R C], RA(m) + 1, CC(m) + 1));
  m = OKC & RB >= 0 & RB < R;
  ib(m) = lut(sub2ind([R C], RB(m) + 1, CC(m) + 1));
  both = ia > 0 & ib > 0;
  da = inf(size(ia)); db = da;
  da(both) = (x(ia(both)) - PX(both)).^2 + (y(ia(both)) - PY(both)).^2;
  db(both) = (x(ib(both)) - PX(both)).^2 + (y(ib(both)) - PY(both)).^2;
  pick = ia;
  sw = both & db < da - 1e-12;
  pick(sw) = ib(sw);
  onlyb = ia == 0 & ib > 0;                              % beyond the brick edge, inside an edge hexagon
  if any(onlyb(:))
    d = hypot(x(ib(onlyb)) - PX(onlyb), y(ib(onlyb)) - PY(onlyb));
    t = ib(onlyb); t(d > dx / sqrt(3) + 1e-9) = 0;
    pick(onlyb) = t;
  end
  blk = repmat(reshape(bg, 1, 1, 4), numel(rows), W);
  k = pick > 0;
  for ch = 1:4
    v = blk(:, :, ch);
    col = rgba(:, ch);
    v(k) = col(pick(k));
    blk(:, :, ch) = v;
  end
  transparentCol = false(size(pick));
  transparentCol(k) = rgba(pick(k), 4) == 0;              % alpha 0 = no value -> background
  for ch = 1:4
    v = blk(:, :, ch); v(transparentCol) = bg(ch); blk(:, :, ch) = v;
  end
  img(rows, :, :) = blk;
end
end
