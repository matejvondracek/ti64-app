function [cid, nColonies, nPairs] = mergeColonies(gid, p)
% MERGECOLONIES  Alpha colonies (ColonyParams in python/ti64app/backends/base.py, decisions D14):
%   neighbouring alpha grains whose MEAN orientations are within p.burgers_tol_deg of the Burgers
%   type-1 alpha/alpha misorientation (10.53 deg about <0001>, from orientation.Burgers(csBeta, csAlpha)
%   variants) are merged transitively (MTEX merge by pairs of grain ids).
%   Stores struct('grains', merged, 'ebsd', ebsd with merged grainId, 'source', gid).

S = ti64.store('get', gid);
g = S.grains; ebsd = S.ebsd;
csA = []; csB = [];
for k = 1:numel(ebsd.CSList)
  cs = ebsd.CSList(k);
  if isa(cs, 'crystalSymmetry') && contains(cs.mineral, 'alpha'), csA = cs; end
  if isa(cs, 'crystalSymmetry') && contains(cs.mineral, 'beta'),  csB = cs; end
end
pairs = zeros(0, 2);
if ~isempty(csA) && ~isempty(csB)
  gb = g.boundary(csA.mineral, csA.mineral);
  gidb = gb.grainId;
  gidb = gidb(all(gidb > 0, 2) & gidb(:, 1) ~= gidb(:, 2), :);
  cand = unique(sort(gidb, 2), 'rows');
  if ~isempty(cand)
    p2c = orientation.Burgers(csB, csA);
    oc = variants(p2c, orientation.id(csB));
    mori = inv(oc(1)) .* oc(2:end);
    [~, i1] = min(angle(mori));                    % 10.53 deg type
    t1 = mori(i1);
    ga = g(csA.mineral);                           % mean orientations of one phase only
    [~, ia] = ismember(cand(:, 1), ga.id);
    [~, ib] = ismember(cand(:, 2), ga.id);
    mo = ga.meanOrientation;
    d = angle(inv(mo(ia)) .* mo(ib), t1) / degree;
    pairs = cand(d(:) <= p.burgers_tol_deg, :);
  end
end
nPairs = size(pairs, 1);
if nPairs > 0
  [gm, parentId] = merge(g, pairs, 'calcMeanOrientation');
  old = ebsd.prop.grainId;
  ok = ~isnan(old) & old > 0;
  new = old;
  new(ok) = parentId(old(ok));
  ebsd.prop.grainId = new;
else
  gm = g;
end
cid = ti64.store('put', struct('grains', gm, 'ebsd', ebsd, 'source', gid));
nColonies = numel(gm);
end
