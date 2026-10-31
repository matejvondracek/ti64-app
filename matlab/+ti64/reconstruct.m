function [rid, res] = reconstruct(id, par)
% RECONSTRUCT  Prior-beta reconstruction of a stored LOADED map, ported from EBSD_part2.m
%   (Research Assignment, MTEX 6.1; docs/beta_reconstruction_method.md) to MTEX 7.2.
%   [rid, res] = ti64.reconstruct(id, par)
%   par fields (degrees, same names as python ReconParams): cleanAngle, noiseMaxIndexed, noiseMaxVoid,
%   segAngle, smoothIter, method ('variantGraph'|'graph'), vgThreshold, vgTolerance, vgNumIter,
%   vgInflation, graphThreshold, graphTolerance, graphInflation, minProb, orRefine (true/false),
%   orMaxDev (refined OR kept if <= this from Burgers), orMode ('limit'|'burgers'|'refined'),
%   mergeMode ('single'|'progressive'), mergeThreshold, mergeStart, mergeStep, inclusionMaxSize,
%   badFit, histMinECD, stepAngle, noiseAngle, sweep (n x 2 cell {mode, deg}), logFile ('' = none).
%   Stores struct(ebsd = PARENT MAP, job, parentGrains, parentEBSD, ebsdKept, childGrains, ebsdRaw, gBpre,
%   misPre) under rid. The parent map is the loaded map with the parent EBSD on the kept pixels (removed
%   noise = not indexed) and props ti64_parent_grain, ti64_recon_fit (deg), ti64_recon_state (codes as
%   RECON_* in python/ti64app/backends/base.py). res: summary numbers.
%   MTEX 7 changes vs EBSD_part2.m: noise grains with 'alpha', 0 (MTEX 7 calcGrains would otherwise
%   swallow small voids, decisions D7); [grains, ebsd] = calcGrains syntax; how2plot read-only.

tAll = tic;
ebsdRaw = ti64.store('get', id);
deg = degree;

% --- noise cleanup (on ALL pixels so grains don't grow into voids) ---
[grains1, e1] = calcGrains(ebsdRaw, 'angle', par.cleanAngle * deg, 'alpha', 0);
gid1 = e1.grainId;
isNoise = (grains1.isIndexed & grains1.numPixel <= par.noiseMaxIndexed) | ...
          (~grains1.isIndexed & grains1.numPixel <= par.noiseMaxVoid);
keep = ~ismember(gid1, grains1.id(isNoise));
clear grains1 e1 gid1
ebsd = ebsdRaw(keep);
[grains, ebsd] = calcGrains(ebsd, 'angle', par.segAngle * deg);
if par.smoothIter > 0, grains = smooth(grains, par.smoothIter); end
res.n_noise_px = nnz(~keep);
res.n_child_grains = numel(grains);

% --- job and OR ---
alpha = ebsd('Ti (alpha)');
job = parentGrainReconstructor(ebsd, grains);
try
  job.csParent = ebsdRaw('Ti (beta)').CS;    % phase list of the file (beta may be all noise)
catch
  job.csParent = crystalSymmetry('m-3m', [3.32 3.32 3.32], 'mineral', 'Ti (beta)');
end
job.csChild = alpha.CS;
p2cB = orientation.Burgers(job.csParent, job.csChild);
job.p2c = p2cB;
res.or_dev_deg = NaN; res.or_used = 'burgers';
if par.orRefine
  job.calcParent2Child;
  res.or_dev_deg = angle(job.p2c, p2cB) / deg;
  useRefined = strcmp(par.orMode, 'refined') || (strcmp(par.orMode, 'limit') && res.or_dev_deg <= par.orMaxDev);
  if useRefined, res.or_used = 'refined'; else, job.p2c = p2cB; end
end

% --- reconstruction ---
switch par.method
  case 'variantGraph'
    job.calcVariantGraph('threshold', par.vgThreshold * deg, 'tolerance', par.vgTolerance * deg);
    job.clusterVariantGraph('numIter', par.vgNumIter, 'inflationPower', par.vgInflation);
    job.calcParentFromVote('minProb', par.minProb);
  case 'graph'
    job.calcGraph('threshold', par.graphThreshold * deg, 'tolerance', par.graphTolerance * deg);
    job.clusterGraph('inflationPower', par.graphInflation);
    job.calcParentFromGraph;
    job.calcGBVotes('p2c', 'threshold', par.graphThreshold * deg, 'tolerance', par.graphTolerance * deg);
    job.calcParentFromVote('minProb', par.minProb);
end
grainsPre = job.grains; mergeIdPre = job.mergeId;

% --- neighbour misorientation before merging ---
gPre = grainsPre(grainsPre.phaseId == job.parentPhaseId);
gBpre = gPre.boundary(job.csParent.mineral, job.csParent.mineral);
pairInd = gPre.id2ind(gBpre.grainId);
moPre = gPre.meanOrientation;
misPre = angle(moPre(pairInd(:, 1)), moPre(pairInd(:, 2))) / deg;
segLen = gBpre.segLength;
pairFull = grainsPre.id2ind(gBpre.grainId);

% --- merge sweep (numbers only) ---
if isfield(par, 'sweepModes') && isempty(par.sweepModes)
  sw = cell(0, 2);
elseif isfield(par, 'sweepModes')             % from Python: modes (cell) + thresholds (vector)
  sw = [cellstr(par.sweepModes(:)), num2cell(double(par.sweepThr(:)))];
else
  sw = par.sweep;
end
res.sweep = zeros(size(sw, 1), 4);
for k = 1:size(sw, 1)
  job.grains = grainsPre; job.mergeId = mergeIdPre;
  doMerge(job, par, sw{k, 1}, sw{k, 2} * deg);
  job.mergeInclusions('maxSize', par.inclusionMaxSize);
  res.sweep(k, :) = mergeMetrics(job, par, mergeIdPre, pairFull, misPre, segLen);
end

% --- chosen merge ---
job.grains = grainsPre; job.mergeId = mergeIdPre;
doMerge(job, par, par.mergeMode, par.mergeThreshold * deg);
job.mergeInclusions('maxSize', par.inclusionMaxSize);
fin = mergeMetrics(job, par, mergeIdPre, pairFull, misPre, segLen);

% --- results ---
pg = job.parentGrains;
pe = job.calcParentEBSD;
fitDeg = pe.fit / deg; hasFit = ~isnan(fitDeg);
A = area(pg); E = 2 * sqrt(A / pi);
childArea = area(job.grainsPrior);
isChild0 = job.grainsPrior.phaseId == job.childPhaseId;
res.n_grains_min_ecd = nnz(E >= par.histMinECD);
res.n_grains_all = numel(E);
res.rec_frac = sum(childArea(job.isTransformed)) / sum(childArea(isChild0));
res.mean_fit_deg = mean(fitDeg(hasFit));
res.bad_fit_frac = mean(fitDeg(hasFit) > par.badFit);
res.ecd_aw_um = sum(E .* A) / sum(A);
res.bridged_pct = fin(3); res.split_pct = fin(4);
misClasses = [0 0.5 1 1.5 2 2.5 3 4 5 7.5 10 15 inf];
res.mis_share = accumarray(discretize(misPre, misClasses), segLen, [numel(misClasses) - 1 1]) / sum(segLen);
res.fit_quantiles = quantile(fitDeg(hasFit), [0.1 0.25 0.5 0.75 0.9]);
res.minutes = toc(tAll) / 60;

pmap = parentMap(ebsdRaw, keep, pe);
rid = ti64.store('put', struct('ebsd', pmap, 'job', job, 'parentGrains', pg, 'parentEBSD', pe, ...
  'ebsdKept', ebsd, 'childGrains', grains, 'ebsdRaw', ebsdRaw, 'gBpre', gBpre, 'misPre', misPre));

if isfield(par, 'logFile') && strlength(string(par.logFile)) > 0
  fid = fopen(par.logFile, 'a');
  fprintf(fid, ['%s | %s seg %.1f vgThr %.1f vgTol %.1f iter %d infl %.2f | gThr %.1f gTol %.1f gInfl %.2f | ' ...
    'minP %.2f merge %s %.1f incl %d | grains>=%gum %d recFrac %.1f%% meanFit %.2f badFit %.1f%% ' ...
    'ECDaw %.1f | %.1f min\n'], char(datetime('now', 'Format', 'yyyy-MM-dd HH:mm')), par.method, ...
    par.segAngle, par.vgThreshold, par.vgTolerance, par.vgNumIter, par.vgInflation, par.graphThreshold, ...
    par.graphTolerance, par.graphInflation, par.minProb, par.mergeMode, par.mergeThreshold, ...
    par.inclusionMaxSize, par.histMinECD, res.n_grains_min_ecd, 100 * res.rec_frac, res.mean_fit_deg, ...
    100 * res.bad_fit_frac, res.ecd_aw_um, res.minutes);
  fclose(fid);
end
end

function doMerge(job, par, mode, thr)
if thr <= 0, return; end
if strcmp(mode, 'progressive')
  steps = unique([par.mergeStart * degree:par.mergeStep * degree:thr, thr]);
  steps = steps(steps <= thr);
else
  steps = thr;
end
for st = steps, job.mergeSimilar('threshold', st); end
end

function m = mergeMetrics(job, par, mergeIdPre, pairFull, misPre, segLen)
% [grains >= histMinECD, area-weighted ECD (um), bridged steps (%), split noise (%)]
pg = job.parentGrains; A = area(pg); E = 2 * sqrt(A / pi);
pre2final = zeros(max(mergeIdPre), 1);
pre2final(mergeIdPre) = job.mergeId;
inside = pre2final(pairFull(:, 1)) == pre2final(pairFull(:, 2));
isStep = misPre > par.stepAngle; isNoise = misPre < par.noiseAngle;
m = [nnz(E >= par.histMinECD), sum(E .* A) / sum(A), ...
     100 * sum(segLen(isStep & inside)) / sum(segLen(isStep)), ...
     100 * sum(segLen(isNoise & ~inside)) / sum(segLen(isNoise))];
end

function P = parentMap(ebsdRaw, keep, pe)
% loaded map with the parent EBSD put back on the kept pixels (same codes as pymtex_recon.parent_map)
P = ebsdRaw;
n = numel(P.phaseId);
ph0 = P.phaseId(:);
niId = P.name2id('notIndexed');
betaId = P.name2id('Ti (beta)');
real = ~isnan(P.prop.oldId(:));
removed = ~keep(:) & real & P.isIndexed(:);
P.phaseId(removed) = niId;
[tf, loc] = ismember(pe.id, P.id);
assert(all(tf), 'ti64:reconstruct:ids', 'parent EBSD pixels not found in the loaded map');
P.rotations(loc) = pe.rotations;
P.phaseId(loc) = pe.phaseId;
pgId = zeros(n, 1); pgId(loc) = pe.grainId;
fit = nan(n, 1); fit(loc) = pe.fit / degree;
ph = P.phaseId(:);
isChild = P.isIndexed(:) & ph0 ~= betaId & ph0 ~= niId & ~removed & real;
st = zeros(n, 1);                                         % 0 not indexed
st(removed) = 1;                                          % removed noise
st(isChild & ph == betaId) = 2;                           % reconstructed alpha
st(isChild & ph ~= betaId & ph ~= niId) = 3;              % untransformed alpha
st(ph0 == betaId & ~removed & real) = 4;                  % retained beta
P.phaseId(st == 0 & real) = niId;    % MTEX 7 calcGrains marks voids inside grains as indexed (D7)
ph = P.phaseId(:);
fit(st ~= 2) = NaN;
pgId(ph ~= betaId) = 0;
P.prop.ti64_parent_grain = reshape(pgId, size(P));
P.prop.ti64_recon_fit = reshape(fit, size(P));
P.prop.ti64_recon_state = reshape(st, size(P));
end
