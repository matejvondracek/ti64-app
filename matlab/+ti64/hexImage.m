function hexImage(h5in, h5out)
% HEXIMAGE  Draw the measurements of a hexagonal-grid map as REGULAR HEXAGONS (their Voronoi cells: width dx, flat
%   sides left / right, neighbouring rows offset by half a cell) into a pixel grid. Used for exports with >= 4
%   pixels per measurement and for figures at that resolution, instead of the brick image of ti64.hexRaster
%   (decisions D20). Same rule as python/ti64app/raster.py (CellLayout.sample, hexagons=True): a pixel shows the
%   nearest measurement centre = the brick owner of its row or of the nearer adjacent row.
%   h5in:  /x, /y (n, um, file order), /rgba (n x 4 uint8, alpha 0 = background), /grid = [x0 y0 px py W H],
%          /bg (4, uint8)
%   h5out: /img (H x W x 4 uint8)

x = double(h5read(h5in, '/x')); x = x(:);
y = double(h5read(h5in, '/y')); y = y(:);
rgba = h5read(h5in, '/rgba');
if size(rgba, 2) ~= 4, rgba = rgba'; end               % h5py writes n x 4 row-major
g = double(h5read(h5in, '/grid'));
bg = uint8(h5read(h5in, '/bg')); bg = bg(:)';
img = ti64.hexRasterize(x, y, rgba, g, bg);
if exist(h5out, 'file'), delete(h5out); end
h5create(h5out, '/img', size(img), 'Datatype', 'uint8');
h5write(h5out, '/img', img);
end
