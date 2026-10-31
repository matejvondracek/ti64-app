function exportRecon(rid, h5file)
% EXPORTRECON  Parent grains, their boundaries and the pre-merge beta/beta boundaries of a stored
%   reconstruction to HDF5 (fields of ReconData in python/ti64app/backends/base.py):
%   grain_id, grain_n_px, grain_area_um2, grain_ecd_um, grain_is_edge, grain_mean_quat (n x 4);
%   gb_x1 y1 x2 y2 length_um grain1 grain2 mis_deg (between the grains' MEAN parent orientations,
%   NaN unless both sides are parent grains; 0 = edge / not a parent grain);
%   pre_x1 y1 x2 y2 length_um mis_deg (neighbour beta misorientation before merging).

S = ti64.store('get', rid);
pg = S.parentGrains;
A = area(pg);
q = pg.meanOrientation;
data = {'grain_id', double(pg.id), 'grain_n_px', double(pg.numPixel), 'grain_area_um2', A, ...
  'grain_ecd_um', 2 * sqrt(A / pi), 'grain_is_edge', double(pg.isBoundary), ...
  'grain_mean_quat', [q.a(:) q.b(:) q.c(:) q.d(:)]};

gb = pg.boundary;
gid = gb.grainId;
ids = pg.id;
inPg = ismember(gid, ids) & gid > 0;
both = all(inPg, 2);
mis = nan(size(gid, 1), 1);
if any(both)
  ind = pg.id2ind(gid(both, :));
  mis(both) = angle(q(ind(:, 1)), q(ind(:, 2))) / degree;
end
g1 = gid(:, 1); g1(~inPg(:, 1)) = 0;
g2 = gid(:, 2); g2(~inPg(:, 2)) = 0;
[x1, y1, x2, y2] = segs(gb);
data = [data, {'gb_x1', x1, 'gb_y1', y1, 'gb_x2', x2, 'gb_y2', y2, 'gb_length_um', gb.segLength, ...
  'gb_grain1', g1, 'gb_grain2', g2, 'gb_mis_deg', mis}];
[x1, y1, x2, y2] = segs(S.gBpre);
data = [data, {'pre_x1', x1, 'pre_y1', y1, 'pre_x2', x2, 'pre_y2', y2, ...
  'pre_length_um', S.gBpre.segLength, 'pre_mis_deg', S.misPre(:)}];

if isfile(h5file), delete(h5file); end
for k = 1:2:numel(data)
  v = double(data{k+1});
  if isempty(v), v = zeros(0, 1); end
  h5create(h5file, ['/' data{k}], max(size(v'), 1), 'Datatype', 'double');
  if ~isempty(v), h5write(h5file, ['/' data{k}], v'); end
end
end

function [x1, y1, x2, y2] = segs(gb)
V = gb.allV; F = gb.F;
x1 = V.x(F(:, 1)); y1 = V.y(F(:, 1)); x2 = V.x(F(:, 2)); y2 = V.y(F(:, 2));
x1 = x1(:); y1 = y1(:); x2 = x2(:); y2 = y2(:);
end
