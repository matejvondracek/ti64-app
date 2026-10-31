function [newId, rep] = cleanup(id, p)
% CLEANUP  Cleanup of a stored map; the input map stays untouched (a new ID is returned).
%   [newId, rep] = ti64.cleanup(id, p)    p: struct with the fields of CleanupParams
%   (python/ti64app/backends/base.py; same names, units and defaults as the pymtex backend).
%   Order: CI threshold -> wild-spike removal -> noise removal (EBSD_part2.m) -> fill -> filter.
%   Grains for spike/noise detection use 'alpha', 0 so not-indexed regions stay separate grains,
%   as in MTEX 6.1 for which EBSD_part2.m was written (docs/decisions.md D7).
%   Per-pixel flags in prop.ti64_cleanup: 0 kept, 1 CI removed, 2 spike, 3 noise, 4 filled, 5 unfilled.

src0 = ti64.store('get', id);
w = src0;                                     % value object: copy, the stored map is not changed
valid = ~isnan(w.prop.oldId(:));
pid = w.phaseId(:);
indexed0 = valid & w.isIndexed(:);
flag = zeros(numel(pid), 1, 'int8');
rot = w.rotations; rot = rot(:);

  function unindex(m, code)
    rot(m) = rotation.nan;
    pid(m) = 1;                               % phaseId 1 = notIndexed (phaseMap 0)
    flag(m) = code;
  end

nCi = 0;
if ~isempty(p.ci_threshold) && ~(isnumeric(p.ci_threshold) && isnan(p.ci_threshold))
  m = indexed0 & w.prop.ci(:) < p.ci_threshold;
  unindex(m, 1);
  nCi = nnz(m);
end
w = applyState(w, rot, pid);

nSpike = 0;
if p.spike_removal
  [gid, npx, gidx] = grainsAlpha0(w, p.spike_angle_deg);
  m = valid & gidx(gid + 1) & npx(gid + 1) == 1;
  unindex(m, 2);
  nSpike = nnz(m);
  w = applyState(w, rot, pid);
end

[gid, npx, gidx] = grainsAlpha0(w, p.noise_angle_deg);
noise = valid & gidx(gid + 1) & npx(gid + 1) <= p.noise_max_indexed_px;
void  = valid & ~gidx(gid + 1) & npx(gid + 1) <= p.noise_max_void_px;
unindex(noise, 3);
toFill = noise | void;
nFilled = 0; nUnfilled = 0;
if p.fill && any(toFill)
  ok = valid & pid ~= 1 & ~isnan(pid);
  nbr = ti64.neighbourTable(w.x(:), w.y(:), valid, isa(w, 'EBSDhex'));
  srcIdx = ti64.dilationFill(toFill, gid, ok, nbr);
  done = toFill & srcIdx > 0;
  rot(done) = rot(srcIdx(done));
  pid(done) = pid(srcIdx(done));
  flag(done) = 4;
  flag(toFill & srcIdx == 0) = 5;
  nFilled = nnz(done); nUnfilled = nnz(toFill & srcIdx == 0);
end
w = applyState(w, rot, pid);

nChanged = 0;
if p.hq_filter
  F = l1TVFilter;                               % = halfQuadraticFilter of MTEX < 7.2 / pymtex 1.0
  F.alpha = p.hq_alpha;
  F.threshold = p.hq_threshold_deg * degree;
  before = w.rotations(:);
  ws = smooth(w, F);
  after = ws.rotations(:);
  okf = valid & ~isnan(before.a) & ~isnan(after.a);
  rot = before; rot(okf) = after(okf);
  w = applyState(w, rot, pid);
  nChanged = nnz(angle(before(okf), after(okf)) > 0.1 * degree);
end

w.prop.ti64_cleanup = reshape(double(flag), size(w));
newId = ti64.store('put', w);
rep = struct('n_points', nnz(valid), 'n_indexed_before', nnz(indexed0), 'n_ci_removed', nCi, ...
  'n_spikes_removed', nSpike, 'n_noise_indexed_removed', nnz(noise), 'n_small_void_px', nnz(void), ...
  'n_filled', nFilled, 'n_unfilled', nUnfilled, ...
  'n_indexed_after', nnz(valid & pid ~= 1 & ~isnan(pid)), 'n_filtered_changed', nChanged);
end

function w = applyState(w, rot, pid)
w.rotations = reshape(rot, size(w));
w.phaseId = pid;
end

function [gid, npx, gidx] = grainsAlpha0(w, angleDeg)
% grains with alpha = 0; per-pixel grain id (0 = none) and lookups indexed by grain id + 1
[g, w2] = calcGrains(w, 'angle', angleDeg * degree, 'alpha', 0);
gid = w2.grainId(:);
gid(isnan(gid)) = 0;
n = max([double(g.id(:)); gid]) + 1;
npx = zeros(n, 1); gidx = false(n, 1);
npx(g.id + 1) = g.numPixel;
gidx(g.id + 1) = g.isIndexed;
end
