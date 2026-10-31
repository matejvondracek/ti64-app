%% ========================================================================
%  Prior-beta reconstruction of EBM Ti-6Al-4V (MTEX 6.1)
%  Variables set by lib.py before this part: fname, outDir, x/y/z_label, rot_axis
%% ========================================================================
tAll = tic;

%% --- Settings (tune here) ----------------------------------------------
% data cleanup (dialled in - changing these invalidates the cache)
par.cleanAngle       = 3*degree;    % grain angle used to find noise
par.noiseMaxIndexed  = 2;           % indexed grains with <= this many pixels are noise
par.noiseMaxVoid     = 4;           % not-indexed regions with <= this many pixels are filled
% alpha grains for the reconstruction
par.segAngle         = 1.5*degree;  % MTEX Ti example: small angle so alpha from
                                    % different beta grains is not merged into one alpha grain
% reconstruction
par.method           = 'variantGraph';  % 'variantGraph' (MTEX recommended) or 'graph' (old method)
par.vgThreshold      = 5*degree;    % variant graph: misfit with probability 0.5
                                    %   = 'reconstruction threshold' of Yang et al. 2025 (IJLMM 9, 91-102): 5 deg (8 deg also ok)
par.vgTolerance      = 1.5*degree;  % variant graph: width of the probability transition
par.vgNumIter        = 3;           % variant graph: MCL iterations
par.vgInflation      = 1.05;        % variant graph: MCL inflation power (MTEX default)
par.graphThreshold   = 2*degree;    % old method: calcGraph threshold
par.graphTolerance   = 1.5*degree;  % old method: calcGraph tolerance
par.graphInflation   = 1.6;         % old method: clusterGraph inflation power
par.minProb          = 0;           % calcParentFromVote: min probability to accept a parent
par.mergeMode        = 'single';       % 'progressive': mergeSimilar in small steps up to mergeThreshold,
                                    %   so later steps compare averaged cluster orientations (less chaining)
                                    % 'single': one mergeSimilar call at mergeThreshold (old behaviour)
par.mergeThreshold   = 3*degree;    % = 'merging threshold' of Yang et al. 2025: 3 deg (their r5m3 / r8m3)
par.mergeStart       = 1*degree;    % progressive: first step
par.mergeStep        = 0.5*degree;  % progressive: step size
par.lineWidth        = 0.75;        % grain boundary line width in all maps (was 1.5)
par.hagbAngle        = 10*degree;   % high-angle prior-beta boundaries drawn black in the grain map
par.inclusionMaxSize = 5;           % mergeInclusions: merge inclusions with < this many pixels
% outputs
par.badFit           = 3*degree;    % fit above this counts as bad in the summary
par.histMinECD       = 5;           % grain size histogram ignores fragments smaller than this (um)
par.useCache         = true;        % reuse loaded + cleaned data from a .mat next to the .ang
par.savePixelMapFig  = true;        % also save .fig of the pixel maps (now small, image-based)
% diagnostics
par.doMergeSweep     = true;        % test several merge settings (numbers only, no figures)
par.sweepConfigs     = {'single', 0.5; 'single', 3; 'single', 6; 'single', 8; 'progressive', 3};   % {mode, threshold in deg}
                                    % merge values tested by Yang et al. 2025 (0.5, 3, 6, 8) + progressive 3 for comparison
par.stepAngle        = 4*degree;    % neighbour beta misorientation counted as a real step
par.noiseAngle       = 1*degree;    % neighbour beta misorientation counted as noise
par.misMapMin        = 1.5*degree;  % boundary misorientation map shows boundaries above this
par.misMapMax        = 8*degree;    % ... colour scale max

% output folder for figures (.fig + high-quality .png)
outDir = fullfile(fileparts(fname), 'figures_prior_beta');
if isempty(fileparts(fname)), outDir = fullfile(pwd, 'figures_prior_beta'); end
if ~exist(outDir, 'dir'), mkdir(outDir); end

%% --- Load data + noise cleanup + alpha grains (cached) -----------------
t = tic;
cacheFile = [fname(1:end-4) '_mtexcache.mat'];
angInfo   = dir(fname);
cacheKey  = sprintf('%s|%.6f|clean %.3f %d %d|seg %.3f', angInfo.name, angInfo.datenum, ...
                    par.cleanAngle/degree, par.noiseMaxIndexed, par.noiseMaxVoid, par.segAngle/degree);
useCached = false;
if par.useCache && exist(cacheFile, 'file')
  S = load(cacheFile, 'cacheKey');
  useCached = isfield(S, 'cacheKey') && strcmp(S.cacheKey, cacheKey);
end

if useCached
  load(cacheFile, 'ebsdRaw', 'keep', 'grainIdKept', 'grains');
  ebsd = ebsdRaw(keep);
  ebsd.grainId = grainIdKept;
  fprintf('Loaded cleaned data and alpha grains from cache (%.0f s)\n', toc(t));
else
  ebsdRaw = loadEBSD_ang(fname,'setting',2);

  % Grains from ALL pixels (not ebsd('indexed')): with ebsd('indexed') MTEX
  % lets grains grow into not-indexed regions, which filled the voids.
  [grains1, gid1] = calcGrains(ebsdRaw, 'angle', par.cleanAngle);

  % noise: tiny indexed grains and tiny voids; their pixels are removed and
  % filled by the neighbouring grains in the next calcGrains call
  isNoise = ( grains1.isIndexed & grains1.numPixel <= par.noiseMaxIndexed) | ...
            (~grains1.isIndexed & grains1.numPixel <= par.noiseMaxVoid);
  keep = ~ismember(gid1, grains1.id(isNoise));
  clear grains1 gid1

  % alpha grains used for the reconstruction
  ebsd = ebsdRaw(keep);
  [grains, ebsd.grainId] = calcGrains(ebsd, 'angle', par.segAngle);
  grains = smooth(grains, 5);
  grainIdKept = ebsd.grainId;

  if par.useCache
    save(cacheFile, 'ebsdRaw', 'keep', 'grainIdKept', 'grains', 'cacheKey', '-v7.3', '-nocompression');
  end
  fprintf('Loaded and cleaned data, computed alpha grains (%.0f s)\n', toc(t));
end
clear grainIdKept

alpha = ebsd("Ti (alpha)");
beta  = ebsd("Ti (beta)");
CS    = alpha.CS;

%% rotate data
ebsd.how2plot.east = xvector;
ebsd.how2plot.outOfScreen = -zvector;
% use the same plotting convention for the raw data and the grains
ebsdRaw.how2plot = ebsd.how2plot;
grains.how2plot  = ebsd.how2plot;

%% --- Set up reconstruction job -----------------------------------------
t = tic;
job = parentGrainReconstructor(ebsd, grains);

% make sure parent = beta (cubic), child = alpha (hexagonal)
job.csParent = beta.CS;     % if no beta indexed, use instead:
% job.csParent = crystalSymmetry('m-3m',[3.32 3.32 3.32],'mineral','Ti (beta)');
job.csChild  = alpha.CS;

% initial parent-to-child orientation relation: Burgers
% (alpha {0001}||beta {110}, alpha <11-20>||beta <111>)
p2cBurgers = orientation.Burgers(job.csParent, job.csChild);
job.p2c = p2cBurgers;

% refine the OR (with < 1% beta MTEX fits it from alpha/alpha boundaries);
% fall back to ideal Burgers if the fit is more than 1 deg away
job.calcParent2Child;
dOR = angle(job.p2c, p2cBurgers)/degree;
fprintf('Refined OR deviates from ideal Burgers by %.2f deg\n', dOR);
if dOR > 1
  job.p2c = p2cBurgers;
  fprintf('  -> deviation > 1 deg, using ideal Burgers OR instead\n');
end

%% --- Reconstruction ----------------------------------------------------
switch par.method
  case 'variantGraph'   % MTEX's recommended workflow for Ti (TiBetaReconstruction example)
    job.calcVariantGraph('threshold', par.vgThreshold, 'tolerance', par.vgTolerance);
    job.clusterVariantGraph('numIter', par.vgNumIter, 'inflationPower', par.vgInflation);
    job.calcParentFromVote('minProb', par.minProb);   % NB: option is 'minProb'

  case 'graph'          % older grain-graph workflow
    job.calcGraph('threshold', par.graphThreshold, 'tolerance', par.graphTolerance);
    job.clusterGraph('inflationPower', par.graphInflation);
    job.calcParentFromGraph;
    job.calcGBVotes('p2c', 'threshold', par.graphThreshold, 'tolerance', par.graphTolerance);
    job.calcParentFromVote('minProb', par.minProb);
end

fprintf('Reconstruction done (%.0f s)\n', toc(t));

% state before merging, so different merge settings can be tried
grainsPreMerge  = job.grains;
mergeIdPreMerge = job.mergeId;

%% --- Neighbour misorientation before merging ----------------------------
% Before mergeSimilar every alpha grain carries its own beta orientation.
% Misorientation between the beta orientations of neighbouring alpha grains:
% within one prior beta grain it is only noise (OR fit, alpha spread), across
% a real prior beta boundary it is larger.
t = tic;
gPre     = grainsPreMerge(grainsPreMerge.phaseId == job.parentPhaseId);
gBpre    = gPre.boundary(job.csParent.mineral, job.csParent.mineral);
pairInd  = gPre.id2ind(gBpre.grainId);
moPre    = gPre.meanOrientation;
misPre   = angle(moPre(pairInd(:,1)), moPre(pairInd(:,2))) / degree;
segLen   = gBpre.segLength;
pairFull = grainsPreMerge.id2ind(gBpre.grainId);   % same pairs as indices into all pre-merge grains
clear gPre moPre pairInd
fprintf('Neighbour misorientations computed (%.0f s)\n', toc(t));

%% --- Merge sweep (numbers only) -----------------------------------------
% For each setting: merge + mergeInclusions, then
%   bridged steps = share of the length of real steps (> stepAngle) that ends up
%                   INSIDE a final grain  -> too much merging / chaining
%   split noise   = share of the length of noise boundaries (< noiseAngle) that
%                   stays a final grain boundary -> too little merging
% The best setting keeps both low.
sweepRes = [];
if par.doMergeSweep
  t = tic;
  sweepRes = nan(size(par.sweepConfigs, 1), 4);
  for k = 1:size(par.sweepConfigs, 1)
    job.grains = grainsPreMerge;  job.mergeId = mergeIdPreMerge;
    doMerge(job, par, par.sweepConfigs{k,1}, par.sweepConfigs{k,2}*degree);
    job.mergeInclusions('maxSize', par.inclusionMaxSize);
    sweepRes(k,:) = mergeMetrics(job, par, mergeIdPreMerge, pairFull, misPre, segLen);
    fprintf('  %-11s %.1f deg: %d grains, bridged steps %.1f %%, split noise %.1f %%\n', ...
            par.sweepConfigs{k,1}, par.sweepConfigs{k,2}, sweepRes(k,1), sweepRes(k,3), sweepRes(k,4));
  end
  fprintf('Merge sweep done (%.0f s)\n', toc(t));
end

%% --- Cleanup with the chosen setting ------------------------------------
job.grains = grainsPreMerge;  job.mergeId = mergeIdPreMerge;
doMerge(job, par, par.mergeMode, par.mergeThreshold);
% absorb only tiny inclusions (< inclusionMaxSize pixels), keep larger ones
job.mergeInclusions('maxSize', par.inclusionMaxSize);
finalRes = mergeMetrics(job, par, mergeIdPreMerge, pairFull, misPre, segLen);
clear grainsPreMerge mergeIdPreMerge pairFull

%% --- Results -----------------------------------------------------------
t = tic;
parentGrains = job.parentGrains;
allEBSD      = job.calcParentEBSD;     % per-pixel fit and variantId of reconstructed alpha

% voids in the grain map: not-indexed grains and unreconstructed alpha grains
voidGrains   = job.grains(job.grains.phaseId ~= job.parentPhaseId);

ipfKey = ipfHKLKey(job.csParent);
ipfKey.inversePoleFigureDirection = vector3d.Z;

% fast pixel maps: the hex grid is drawn as ONE image instead of ~2.6 million
% hexagon patches (MTEX does this only for gridded data). Pixels without a
% value (voids, removed noise, unreconstructed alpha) are white.
R = hexRaster(ebsdRaw);

% --- prior beta grain map ---
% high-angle boundaries between prior-beta grains, from the grains' mean
% beta orientations (boundary.misorientation would use the alpha pixels)
gBpp    = parentGrains.boundary(job.csParent.mineral, job.csParent.mineral);
ppInd   = parentGrains.id2ind(gBpp.grainId);
moPar   = parentGrains.meanOrientation;
misPP   = angle(moPar(ppInd(:,1)), moPar(ppInd(:,2)));
gBhagb  = gBpp(misPP > par.hagbAngle);
clear ppInd moPar

f1 = figure;
plot(voidGrains, 'FaceColor', 'white', 'noBoundary');   % voids white
hold on
plot(parentGrains, ipfKey.orientation2color(parentGrains.meanOrientation), 'noBoundary');
plot(gBhagb, 'lineColor', 'black', 'linewidth', par.lineWidth);    % high-angle boundaries
hold off
legend(gca, 'off');
clipToMap(gca);
try, mP = getappdata(gca, 'mapPlot'); mP.micronBar.setOnTop; end   % scale bar on top
saveFig(f1, outDir, 'parent_grains_IPFZ', true);

% --- check figure: alpha IPF-Z map with the reconstructed prior-beta boundaries
ipfKeyAlpha = ipfHKLKey(alpha.CS);
ipfKeyAlpha.inversePoleFigureDirection = vector3d.Z;
alphaRaw = ebsdRaw('Ti (alpha)');
img = hexRasterImage(R, alphaRaw.id, ipfKeyAlpha.orientation2color(alphaRaw.orientations));
clear alphaRaw
f5 = rasterMap(R, img, parentGrains, 'black', par.lineWidth);
saveFig(f5, outDir, 'alpha_IPFZ_with_prior_beta_boundaries', par.savePixelMapFig);

% --- diagnostics: fit map ---
% fit = angle between each reconstructed alpha pixel and the closest Burgers
% variant of its beta grain's mean orientation. Small = consistent.
fitDeg = allEBSD.fit / degree;
hasFit = ~isnan(fitDeg);
cmap   = parula(256);
fitRGB = cmap(1 + round(255 * min(max(fitDeg(hasFit)/5, 0), 1)), :);
img = hexRasterImage(R, allEBSD.id(hasFit), fitRGB);
f6 = rasterMap(R, img, parentGrains, 'white', par.lineWidth);
colormap(gca, cmap); clim(gca, [0 5]);
cb = colorbar(gca); cb.Label.String = 'fit to Burgers OR (\circ)';
saveFig(f6, outDir, 'diagnostic_fit_map', par.savePixelMapFig);

% --- diagnostics: variant map ---
% variant IDs are numbered relative to each grain's parent orientation, so the
% same ID in different grains means nothing; look at the mix inside a grain
vId    = allEBSD.variantId;
hasVar = ~isnan(vId);
% number of variants: 12 for ideal Burgers, 24 if the refined OR breaks its symmetry
nV     = max([12; numel(variants(job.p2c)); vId(hasVar)]);
cmapV  = hsv(nV);
img = hexRasterImage(R, allEBSD.id(hasVar), cmapV(vId(hasVar), :));
f7 = rasterMap(R, img, parentGrains, 'white', par.lineWidth);
colormap(gca, cmapV); clim(gca, [0.5 nV+0.5]);
cb = colorbar(gca); cb.Ticks = 1:nV; cb.Label.String = 'variant ID';
saveFig(f7, outDir, 'diagnostic_variant_map', par.savePixelMapFig);

clear img fitRGB R

% --- diagnostics: neighbour misorientation histogram (before merging) ---
edges  = 0:0.25:15;
inR    = misPre < edges(end);
binLen = accumarray(discretize(misPre(inR), edges), segLen(inR), [numel(edges)-1 1]);
f9 = figure;
bar(edges(1:end-1) + 0.125, 100 * binLen / sum(segLen), 1, 'FaceColor', [0.3 0.45 0.7], 'EdgeColor', 'none');
set(gca, 'YScale', 'log');
xline(par.mergeThreshold/degree, 'r--', 'merge threshold', 'LineWidth', 1.5);
xlabel('misorientation between \beta of neighbouring \alpha grains (\circ)');
ylabel('share of boundary length (%)');
saveFig(f9, outDir, 'diagnostic_neighbour_misorientation_hist', true);

% --- diagnostics: boundary misorientation map (before merging) ---
% light grey: final prior-beta boundaries; coloured: alpha-grain boundaries
% whose beta orientations differ by >= misMapMin. A coloured line crossing
% the inside of a grey grain = a step that merging bridged.
t = tic;
sel = misPre >= par.misMapMin/degree;
f10 = figure;
plot(innerBoundary(parentGrains.boundary), 'lineColor', [0.75 0.75 0.75], 'linewidth', 3*par.lineWidth);
hold on
plot(gBpre(sel), misPre(sel), 'linewidth', par.lineWidth);
hold off
legend(gca, 'off');
clipToMap(gca);
colormap(gca, turbo); clim(gca, [par.misMapMin par.misMapMax]/degree);
cb = colorbar(gca); cb.Label.String = '\beta misorientation across boundary (\circ)';
try, mP = getappdata(gca, 'mapPlot'); mP.micronBar.setOnTop; end
saveFig(f10, outDir, 'diagnostic_boundary_misorientation_map', par.savePixelMapFig);
fprintf('Boundary misorientation map done (%.0f s)\n', toc(t));

% share of boundary length in misorientation classes (for the summary)
misClasses = [0 0.5 1 1.5 2 2.5 3 4 5 7.5 10 15 inf];
misShare   = accumarray(discretize(misPre, misClasses), segLen, [numel(misClasses)-1 1]) / sum(segLen);
clear gBpre segLen

% --- IPF colour key of the parent (beta, cubic) phase ---
% Same colour formula as MTEX's HKLDirectionKey, but rho is wrapped/clamped:
% grid points lying exactly on the sector edge can get rho = 2*pi - eps, which
% MTEX's own key plot turns into a thin blue/purple sliver along [001]-[101].
f4 = figure('Color', 'w');
dM = ipfKey.dirMap;
v  = plotS2Grid(dM.sR, 'resolution', 0.5*degree);
v  = Miller(v, dM.sym);
hv = project2FundamentalRegion(vector3d(v), dM.sym);
[theta, rho] = polar(hv(:));
[~, maxTheta]    = dM.sR.thetaRange;
[minRho, maxRho] = dM.sR.rhoRange;
rho(rho > pi) = rho(rho > pi) - 2*pi;        % wrap tiny negative rho back
rho = min(max(rho, minRho), maxRho);
r = 1 - theta ./ maxTheta;
g = theta ./ maxTheta .* (maxRho - rho) ./ (maxRho - minRho);
b = theta ./ maxTheta .* (rho - minRho) ./ (maxRho - minRho);
d = reshape([r(:) g(:) b(:)], [size(v), 3]);
plot(v, d, 'surf');
try
  mtexFig = gcm;
  mtexFig.drawNow('figSize', getMTEXpref('figSize'));
end
set(f4, 'Color', 'w');
saveFig(f4, outDir, 'parent_IPF_color_key', true);

% --- prior-beta grain size (fragments below histMinECD are left out) ---
gArea = area(parentGrains);
ECD   = 2*sqrt(gArea/pi);
isBig = ECD >= par.histMinECD;
f3 = figure;
histogram(ECD(isBig));
xlabel('Prior \beta grain diameter (\mum)'); ylabel('Count')
saveFig(f3, outDir, 'parent_grain_size_hist', true);
fprintf('Figures done (%.0f s)\n', toc(t));

%% --- Summary (printed and appended to a log for comparing runs) --------
childArea = area(job.grainsPrior);
isChild0  = job.grainsPrior.phaseId == job.childPhaseId;
recFrac   = sum(childArea(job.isTransformed)) / sum(childArea(isChild0));  % share of alpha area reconstructed
fracBad   = mean(fitDeg(hasFit) > par.badFit/degree);
meanFit   = mean(fitDeg(hasFit));
nBig      = nnz(isBig);
ecdAW     = sum(ECD .* gArea) / sum(gArea);   % area-weighted mean ECD (not dominated by fragments)

% everything printed from here on also goes to reconstruction_summary_latest.txt
summaryFile = fullfile(outDir, 'reconstruction_summary_latest.txt');
if exist(summaryFile, 'file'), delete(summaryFile); end
diary(summaryFile);
fprintf('\n--- reconstruction summary (%s) ---\n', par.method);
fprintf('Refined OR deviated %.2f deg from ideal Burgers (used if <= 1 deg)\n', dOR);
if ~isempty(sweepRes)
  fprintf(['\nmerge sweep (bridged steps = %% of boundaries > %.0f deg hidden inside grains,\n' ...
           '             split noise   = %% of boundaries < %.0f deg kept as grain boundaries)\n'], ...
          par.stepAngle/degree, par.noiseAngle/degree);
  fprintf('  mode         threshold | grains >= %g um | area-w. ECD | bridged steps | split noise\n', par.histMinECD);
  for k = 1:size(sweepRes,1)
    fprintf('  %-11s  %5.1f deg | %15d | %8.1f um | %11.1f %% | %9.1f %%\n', ...
            par.sweepConfigs{k,1}, par.sweepConfigs{k,2}, sweepRes(k,:));
  end
end
fprintf('\nchosen: %s merge at %.1f deg -> bridged steps %.1f %%, split noise %.1f %%\n', ...
        par.mergeMode, par.mergeThreshold/degree, finalRes(3), finalRes(4));
fprintf('\nboundary length share by beta misorientation of neighbouring alpha grains (before merging):\n');
for k = 1:numel(misShare)
  fprintf('  %5.1f - %5.1f deg: %5.1f %%\n', misClasses(k), misClasses(k+1), 100*misShare(k));
end
fprintf('prior beta grains >= %g um:   %d   (all fragments: %d)\n', par.histMinECD, nBig, numel(ECD));
fprintf('reconstructed alpha area:     %.1f %%\n', 100*recFrac);
fprintf('mean fit:                     %.2f deg\n', meanFit);
fprintf('pixels with fit > %.1f deg:   %.1f %%\n', par.badFit/degree, 100*fracBad);
fprintf('area-weighted mean ECD:       %.1f um\n', ecdAW);
% mean fit per variant: if a few variants are much worse than the rest, the
% orientation relation (not the clustering) causes the high-fit patches
ok = hasVar & hasFit;
fitPerVariant = accumarray(vId(ok), fitDeg(ok), [nV 1], @mean, NaN);
pixPerVariant = accumarray(vId(ok), 1, [nV 1]);
fprintf('mean fit per variant (deg):   %s\n', sprintf('%.1f ', fitPerVariant));
fprintf('pixel share per variant (%%):  %s\n', sprintf('%.0f ', 100*pixPerVariant/sum(pixPerVariant)));
fprintf('OR used: %.2f deg from ideal Burgers\n', angle(job.p2c, p2cBurgers)/degree);
fprintf('total run time:               %.1f min\n', toc(tAll)/60);
diary off

fid = fopen(fullfile(outDir, 'reconstruction_log.txt'), 'a');
fprintf(fid, ['%s | %s seg %.1f vgThr %.1f vgTol %.1f iter %d infl %.2f | gThr %.1f gTol %.1f gInfl %.2f | ' ...
              'minP %.2f merge %s %.1f incl %d | grains>=%gum %d recFrac %.1f%% meanFit %.2f badFit %.1f%% ' ...
              'ECDaw %.1f | %.1f min\n'], ...
        char(datetime('now', 'Format', 'yyyy-MM-dd HH:mm')), par.method, par.segAngle/degree, ...
        par.vgThreshold/degree, par.vgTolerance/degree, par.vgNumIter, par.vgInflation, ...
        par.graphThreshold/degree, par.graphTolerance/degree, par.graphInflation, ...
        par.minProb, par.mergeMode, par.mergeThreshold/degree, par.inclusionMaxSize, ...
        par.histMinECD, nBig, 100*recFrac, meanFit, 100*fracBad, ecdAW, toc(tAll)/60);
fclose(fid);

%% ========================================================================
%  Local functions
%% ========================================================================

function saveFig(f, outDir, name, withFig, dpi)
% save as .fig (optional) and high-quality .png, then close to free memory
    if nargin < 4, withFig = true; end
    if nargin < 5, dpi = 600; end
    if withFig
        savefig(f, fullfile(outDir, [name '.fig']));
    end
    exportgraphics(f, fullfile(outDir, [name '.png']), 'Resolution', dpi, ...
                   'BackgroundColor', 'white');
    close(f);
end

function doMerge(job, par, mode, thr)
% mergeSimilar either once or in small steps up to thr (job is a handle)
    if thr <= 0, return; end
    if strcmp(mode, 'progressive')
        steps = unique([par.mergeStart:par.mergeStep:thr, thr]);
        steps = steps(steps <= thr);
    else
        steps = thr;
    end
    for st = steps
        job.mergeSimilar('threshold', st);
    end
end

function m = mergeMetrics(job, par, mergeIdPreMerge, pairFull, misPre, segLen)
% [grains >= histMinECD, area-weighted ECD (um), bridged steps (%), split noise (%)]
    pg = job.parentGrains;
    A  = area(pg);
    E  = 2*sqrt(A/pi);
    % final grain of every pre-merge grain
    pre2final = zeros(max(mergeIdPreMerge), 1);
    pre2final(mergeIdPreMerge) = job.mergeId;
    inside  = pre2final(pairFull(:,1)) == pre2final(pairFull(:,2));
    isStep  = misPre > par.stepAngle/degree;
    isNoise = misPre < par.noiseAngle/degree;
    bridged = 100 * sum(segLen(isStep & inside))   / sum(segLen(isStep));
    split   = 100 * sum(segLen(isNoise & ~inside)) / sum(segLen(isNoise));
    m = [nnz(E >= par.histMinECD), sum(E.*A)/sum(A), bridged, split];
end

function R = hexRaster(ebsdRaw)
% mapping from the hexagonal EBSD grid (row aligned, as in .ang HexGrid) to
% an image: every hex pixel fills two half-width image columns, odd rows are
% shifted by half a column -> exact brick-layout copy of the hex grid
    pos = ebsdRaw.rot2Plane .* ebsdRaw.pos;
    x = pos.x(:);  y = pos.y(:);
    ys  = unique(round(y*1e4)/1e4);
    dy  = median(diff(ys));
    row = round((y - ys(1)) / dy) + 1;
    xr  = sort(x(row == 1));
    dx  = median(diff(xr));
    xmin = min(x);
    h   = round((x - xmin) / (dx/2));            % half-column index (0-based)
    R.H = max(row);
    R.W = max(h) + 2;
    R.ind1 = sub2ind([R.H R.W], row, h + 1);
    R.ind2 = sub2ind([R.H R.W], row, h + 2);
    R.lut  = zeros(max(ebsdRaw.id), 1);
    R.lut(ebsdRaw.id) = 1:numel(x);
    R.xdata = xmin - dx/4 + [0, (R.W - 1) * dx/2];
    R.ydata = ys(1) + [0, (R.H - 1) * dy];
end

function img = hexRasterImage(R, ids, rgb)
% uint8 RGB image of the given pixels (by EBSD id); everything else white
    img = 255 * ones(R.H * R.W, 3, 'uint8');   % no value = white
    k   = R.lut(ids(:));
    ok  = k > 0 & all(isfinite(rgb), 2);
    c   = uint8(round(255 * min(max(rgb(ok, :), 0), 1)));
    k   = k(ok);
    img(R.ind1(k), :) = c;
    img(R.ind2(k), :) = c;
    img = reshape(img, R.H, R.W, 3);
end

function gB = innerBoundary(gB)
% grain boundaries without the segments along the edge of the map
    gB = gB(all(gB.grainId > 0, 2));
end

function clipToMap(ax)
% clip everything (boundary lines included) to the map area
    ax.Clipping = 'on';
    set(findobj(ax, '-property', 'Clipping'), 'Clipping', 'on');
end

function f = rasterMap(R, img, parentGrains, lineColor, lineWidth)
% MTEX map axes (plotting convention + scale bar) from the boundary plot,
% raster image underneath, boundaries and scale bar on top
    f = figure;
    plot(innerBoundary(parentGrains.boundary), 'lineColor', lineColor, 'linewidth', lineWidth);
    ax = gca;
    % remember the view set up by MTEX (your flipped plotting convention)
    keepProps = {'XDir','YDir','XLim','YLim'};
    keepVals  = get(ax, keepProps);
    hold(ax, 'on');
    hImg = image(ax, 'XData', R.xdata, 'YData', R.ydata, 'CData', img);
    uistack(hImg, 'bottom');
    hold(ax, 'off');
    set(ax, keepProps, keepVals);
    ax.SortMethod = 'childorder';    % draw in stacking order: image, boundaries, scale bar
    legend(ax, 'off');
    clipToMap(ax);
    try, mP = getappdata(ax, 'mapPlot'); mP.micronBar.setOnTop; end
end
