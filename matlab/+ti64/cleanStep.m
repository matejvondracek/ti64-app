function [newId, rep] = cleanStep(id, operation, p)
% CLEANSTEP  ONE cleanup operation on a stored map (phase 6.6 fix 5); the input map stays untouched.
%   [newId, rep] = ti64.cleanStep(id, operation, p)
%   operation: 'ci_threshold' | 'spike_removal' | 'noise_removal' | 'void_fill' | 'grain_dilation' | 'grain_ci' |
%              'smoothing'
%   p: struct with the fields of the operation's parameters (python/ti64app/backends/base.py CLEAN_OPS: same names,
%   units and defaults as the pymtex backend, PymtexBackend.clean_step; keep both identical).
%   Grains for spike / noise / void vote / dilation use 'alpha', 0 (not-indexed regions stay separate, decisions D7).
%   Per-pixel flags prop.ti64_cleanup (kept from earlier steps): 1 CI, 2 spike, 3 noise, 4 filled, 5 unfilled,
%   6 dilated.
%   rep: n_points, n_indexed_before / after, n_not_indexed_after + the operation's counts; grain dilation also
%   changed_per_iteration (pixels changed per sweep).

w = ti64.store('get', id);
valid = ~isnan(w.prop.oldId(:));
pid = w.phaseId(:);
indexed0 = valid & w.isIndexed(:);
if isfield(w.prop, 'ti64_cleanup'), flag = int8(w.prop.ti64_cleanup(:)); else, flag = zeros(numel(pid), 1, 'int8'); end
rot = w.rotations; rot = rot(:);
rep = struct('operation', operation, 'n_points', nnz(valid), 'n_indexed_before', nnz(indexed0));
isHex = isa(w, 'EBSDhex');

  function unindex(m, code)
    rot(m) = rotation.nan;
    pid(m) = 1;                               % phaseId 1 = notIndexed (phaseMap 0)
    flag(m) = code;
  end

  function [nF, nU] = fillPx(toFill, gid, nbr)
    ok = valid & pid ~= 1 & ~isnan(pid);
    srcIdx = ti64.dilationFill(toFill, gid, ok, nbr);
    done = toFill & srcIdx > 0;
    rot(done) = rot(srcIdx(done));
    pid(done) = pid(srcIdx(done));
    flag(done) = 4;
    flag(toFill & srcIdx == 0) = 5;
    nF = nnz(done); nU = nnz(toFill & srcIdx == 0);
  end

switch operation
  case 'ci_threshold'
    m = indexed0 & w.prop.ci(:) < p.ci_threshold;
    unindex(m, 1);
    rep.n_removed = nnz(m);
  case 'spike_removal'
    [gid, npx, gidx] = grainsAlpha0(w, p.spike_angle_deg);
    m = valid & gidx(gid + 1) & npx(gid + 1) == 1;
    unindex(m, 2);
    rep.n_removed = nnz(m);
  case 'noise_removal'
    [gid, npx, gidx] = grainsAlpha0(w, p.noise_angle_deg);
    noise = valid & gidx(gid + 1) & npx(gid + 1) <= p.noise_max_indexed_px;
    unindex(noise, 3);
    nF = 0; nU = 0;
    if p.fill && any(noise)
      w = applyState(w, rot, pid);
      [nF, nU] = fillPx(noise, gid, ti64.neighbourTable(w.x(:), w.y(:), valid, isHex));
    end
    rep.n_removed = nnz(noise); rep.n_filled = nF; rep.n_unfilled = nU;
  case 'void_fill'
    nbr = ti64.neighbourTable(w.x(:), w.y(:), valid, isHex);
    ni = valid & pid == 1;
    sz = zeros(numel(pid), 1);
    sz(ni) = componentSizes(ni, nbr);
    void = ni & sz <= p.max_void_px;
    big = ni & sz > p.max_void_px;
    nF = 0; nU = 0;
    if any(void)
      gid = grainsAlpha0(w, p.angle_deg);
      [nF, nU] = fillPx(void, gid, nbr);
    end
    rep.n_not_indexed_before = nnz(ni); rep.n_void_px = nnz(void); rep.n_filled = nF; rep.n_unfilled = nU;
    rep.n_left_large_void_px = nnz(big);
    rep.n_large_voids = nRegions(big, nbr);
  case 'grain_dilation'
    [gid, npx, gidx] = grainsAlpha0(w, p.tolerance_deg);
    validG = gidx & npx >= p.min_grain_px;            % indexed by grain id + 1
    if p.multi_row
      y = w.y(:);
      ys = unique(round(y(valid), 4));
      if numel(ys) > 1, dy = median(diff(ys)); else, dy = 1; end
      row = round((y - ys(1)) / dy);
      m = valid & gid > 0;
      rmin = accumarray(gid(m) + 1, row(m), [numel(validG) 1], @min, Inf);
      rmax = accumarray(gid(m) + 1, row(m), [numel(validG) 1], @max, -Inf);
      validG = validG & rmax > rmin;
    end
    inGrain = valid & pid ~= 1 & validG(gid + 1);
    cand = valid & ~inGrain;
    nbr = ti64.neighbourTable(w.x(:), w.y(:), valid, isHex);
    if p.single_iteration, maxIt = 1; else, maxIt = Inf; end
    [srcIdx, ~, counts] = ti64.dilationFill(cand, gid, inGrain, nbr, maxIt);
    done = cand & srcIdx > 0;
    rot(done) = rot(srcIdx(done));
    pid(done) = pid(srcIdx(done));
    flag(done) = 6;
    rep.n_candidates = nnz(cand); rep.n_changed = nnz(done); rep.n_unchanged = nnz(cand & ~done);
    rep.n_iterations = numel(counts); rep.changed_per_iteration = counts(:)';
    rep.n_grains_kept = nnz(validG(2:end));
  case 'grain_ci'
    % Grain CI standardization (fix 6.7.1; OIM 'Grain CI Standardization'): CI of every indexed point of a grain
    % (alpha = 0 grains at tolerance_deg, >= min_grain_px points) := the grain's mean (default) or max CI
    [gid, npx, gidx] = grainsAlpha0(w, p.tolerance_deg);
    validG = gidx & npx >= p.min_grain_px;
    m = valid & pid ~= 1 & ~isnan(pid) & validG(gid + 1);
    ci = double(w.prop.ci(:));
    if strcmp(p.mode, 'max')
      val = accumarray(gid(m) + 1, ci(m), [numel(validG) 1], @max, -Inf);
    else
      val = accumarray(gid(m) + 1, ci(m), [numel(validG) 1], @sum, 0) ./ ...
            max(accumarray(gid(m) + 1, 1, [numel(validG) 1]), 1);
    end
    newCi = ci; newCi(m) = val(gid(m) + 1);
    rep.mode = char(p.mode); rep.n_in_grains = nnz(m);
    rep.n_changed = nnz(m & abs(newCi - ci) > 1e-9);
    rep.n_grains = nnz(validG(2:end));
    if any(m), rep.ci_mean_before = mean(ci(m)); rep.ci_mean_after = mean(newCi(m));
    else, rep.ci_mean_before = NaN; rep.ci_mean_after = NaN; end
    w.prop.ci = reshape(newCi, size(w));
  case 'smoothing'
    w = applyState(w, rot, pid);
    F = l1TVFilter;                               % = halfQuadraticFilter of MTEX < 7.2 / pymtex 1.0
    F.alpha = p.hq_alpha;
    F.threshold = p.hq_threshold_deg * degree;
    before = w.rotations(:);
    ws = smooth(w, F);
    after = ws.rotations(:);
    okf = valid & ~isnan(before.a) & ~isnan(after.a);
    rot = before; rot(okf) = after(okf);
    rep.n_changed_gt_0_1deg = nnz(angle(before(okf), after(okf)) > 0.1 * degree);
  otherwise
    error('ti64:cleanStep', 'unknown cleanup operation %s', operation);
end
w = applyState(w, rot, pid);
w.prop.ti64_cleanup = reshape(double(flag), size(w));
after = valid & pid ~= 1 & ~isnan(pid);
rep.n_indexed_after = nnz(after);
rep.n_not_indexed_after = nnz(valid & ~after);
newId = ti64.store('put', w);
end

function w = applyState(w, rot, pid)
w.rotations = reshape(rot, size(w));
w.phaseId = pid;
end

function [gid, npx, gidx] = grainsAlpha0(w, angleDeg)
% grains with alpha = 0; per-pixel grain id (0 = none) and lookups indexed by grain id + 1 (as cleanup.m)
[g, w2] = calcGrains(w, 'angle', angleDeg * degree, 'alpha', 0);
gid = w2.grainId(:);
gid(isnan(gid)) = 0;
n = max([double(g.id(:)); gid]) + 1;
npx = zeros(n, 1); gidx = false(n, 1);
npx(g.id + 1) = g.numPixel;
gidx(g.id + 1) = g.isIndexed;
end

function [sz, lab] = componentSizes(mask, nbr)
% size of the connected region within mask of every mask pixel (hexgrid.component_sizes in Python)
idx = find(mask);
loc = zeros(numel(mask), 1); loc(idx) = 1:numel(idx);
nb = nbr(idx, :);
ok = nb > 0;
ok(ok) = mask(nb(ok));
r = repmat((1:numel(idx))', 1, size(nb, 2));
G = graph(r(ok), loc(nb(ok)), [], numel(idx), 'omitselfloops');
lab = conncomp(G);
cnt = accumarray(lab(:), 1);
sz = cnt(lab(:));
end

function n = nRegions(mask, nbr)
if ~any(mask), n = 0; return; end
[~, lab] = componentSizes(mask, nbr);
n = max(lab);
end
