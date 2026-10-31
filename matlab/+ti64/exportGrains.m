function exportGrains(gid, h5file)
% EXPORTGRAINS  Grains + boundaries of a stored grains object to HDF5 (same fields as
%   GrainsData in python/ti64app/backends/base.py; pixel arrays in FILE order via oldId).
%   Burgers types: the 5 alpha/alpha misorientations between variants of one beta orientation
%   (orientation.Burgers(csBeta, csAlpha), parent-to-child), nearest type + distance per segment.

S = ti64.store('get', gid);
g = S.grains; ebsd = S.ebsd;
oldId = ebsd.prop.oldId(:);
real = ~isnan(oldId);
[~, order] = sort(oldId(real));
idx = find(real); idx = idx(order);
pg = ebsd.grainId(:); pg(isnan(pg)) = 0;

gphase = ebsd.phaseMap(g.phaseId);
mq = nan(numel(g), 4);                       % mean orientation one phase at a time (multi-phase grains)
for k = 1:numel(ebsd.CSList)
  cs = ebsd.CSList(k);
  if ~isa(cs, 'crystalSymmetry') || ~any(g.phaseId == k), continue; end
  gk = g(g.phaseId == k);
  q = gk.meanOrientation;
  mq(g.phaseId == k, :) = [q.a(:) q.b(:) q.c(:) q.d(:)];
end
A = area(g);
data = {'pixel_grain_id', double(pg(idx)), ...
  'grain_id', double(g.id), 'grain_phase', double(gphase(:)), 'grain_n_px', double(g.numPixel), ...
  'grain_area_um2', A, 'grain_ecd_um', 2 * sqrt(A / pi), 'grain_is_edge', double(g.isBoundary), ...
  'grain_mean_quat', mq};

% Burgers alpha/alpha types
csA = []; csB = [];
for k = 1:numel(ebsd.CSList)
  cs = ebsd.CSList(k);
  if isa(cs, 'crystalSymmetry') && contains(cs.mineral, 'alpha'), csA = cs; end
  if isa(cs, 'crystalSymmetry') && contains(cs.mineral, 'beta'),  csB = cs; end
end
types = [];
if ~isempty(csA) && ~isempty(csB)
  p2c = orientation.Burgers(csB, csA);
  oc = variants(p2c, orientation.id(csB));
  mori = inv(oc(1)) .* oc(2:end);
  ang = round(angle(mori) / degree * 100) / 100;
  [~, first] = unique(ang);                  % sorted by angle: 10.53 60 60.83 63.26 90
  types = mori(first);
end

T = [bTable(g.boundary, false, ebsd, types, csA); bTable(g.innerBoundary, true, ebsd, types, csA)];
f = fieldnames(T);
for k = 1:numel(f)
  data(end+1:end+2) = {['gb_' f{k}], vertcat(T.(f{k}))}; %#ok<AGROW>
end
if isfile(h5file), delete(h5file); end
for k = 1:2:numel(data)
  v = double(data{k+1});
  if isempty(v), v = zeros(0, 1); end
  h5create(h5file, ['/' data{k}], max(size(v'), 1), 'Datatype', 'double');
  if ~isempty(v), h5write(h5file, ['/' data{k}], v'); end
end
end

function T = bTable(gb, inner, ebsd, types, csA)
n = numel(gb);
V = gb.allV; F = gb.F;                     % F indexes all vertices (MTEX 7)
x = V.x; y = V.y;
gid = gb.grainId;
pmap = [ebsd.phaseMap(:); 0];
pid = gb.phaseId; pid(isnan(pid) | pid == 0) = numel(pmap);
pfile = reshape(pmap(pid), n, 2);
mis = nan(n, 1); bt = zeros(n, 1); bd = nan(n, 1);
for k = 1:numel(ebsd.CSList)
  cs = ebsd.CSList(k);
  if ~isa(cs, 'crystalSymmetry'), continue; end
  ph = ebsd.phaseMap(k);
  m = pfile(:, 1) == ph & pfile(:, 2) == ph & all(gid > 0, 2);
  if ~any(m), continue; end
  mo = gb(m).misorientation;
  mis(m) = angle(mo) / degree;
  if ~isempty(types) && strcmp(cs.mineral, csA.mineral)
    d = zeros(nnz(m), numel(types));
    for t = 1:numel(types), d(:, t) = angle(mo, types(t)) / degree; end
    [dmin, imin] = min(d, [], 2);
    bt(m) = imin; bd(m) = dmin;
  end
end
T = struct('x1', x(F(:,1)), 'y1', y(F(:,1)), 'x2', x(F(:,2)), 'y2', y(F(:,2)), ...
  'length_um', gb.segLength, 'phase1', pfile(:,1), 'phase2', pfile(:,2), ...
  'grain1', gid(:,1), 'grain2', gid(:,2), 'mis_deg', mis, 'burgers_type', bt, ...
  'burgers_dist_deg', bd, 'inner', double(inner) * ones(n, 1));
end
