function s = audit_mtex(fname, outH5)
% AUDIT_MTEX  Load an .ang with MTEX 7.x (EDAX setting 2) and report what the loader sees.
%   s = audit_mtex(fname, outH5) returns a struct with the summary and writes per-pixel
%   x, y, phaseId (file numbering), quaternion a,b,c,d, ci, iq, oldId of the real (non-padding)
%   pixels to outH5 (a scratch/cache folder, never data/ or the repo).
%   Audit helper only, not part of the app (+ti64 functions come later).

t0 = tic;
ebsd = EBSD.load(fname, 'setting', 2);
s.load_s = toc(t0);
s.file = fname;
s.mtex = getMTEXpref('version');
s.class = class(ebsd);
s.gridSize = size(ebsd);
s.numel = numel(ebsd.phaseId);
s.isHex = isa(ebsd, 'EBSDhex');
s.dx = ebsd.dx; s.dy = ebsd.dy; s.dPos = ebsd.dPos;
s.props = strjoin(fieldnames(ebsd.prop)', ', ');
s.mineralList = strjoin(ebsd.mineralList, ', ');
s.EulerCorrection = char(ebsd.EulerCorrection);
s.viewOpt = ebsd.how2plot.viewOpt;

oldId = ebsd.prop.oldId(:);
pid = ebsd.phaseId(:);                       % 1 = notIndexed, 2 = alpha, 3 = beta (phaseMap 0 1 2)
real = ~isnan(oldId) & oldId > 0;
s.nPadding = nnz(~real);
s.nPoints = nnz(real);
s.phaseCounts = arrayfun(@(p) nnz(pid(real) == p), 1:numel(ebsd.phaseMap));
s.pctIndexed = 100 * nnz(ebsd.isIndexed(real)) / s.nPoints;
ci = ebsd.prop.ci(:);
s.ciMeanIndexed = mean(ci(real & ebsd.isIndexed(:)));
x = ebsd.x(:); y = ebsd.y(:);
s.xRange = [min(x(real)) max(x(real))];
s.yRange = [min(y(real)) max(y(real))];
s.isOldIdSorted = issorted(oldId(real));

q = ebsd.rotations(:);
A = [q.a q.b q.c q.d];
fileMap = nan(size(pid));                  % back to the file's phase numbering (0, 1, 2)
fileMap(real) = ebsd.phaseMap(pid(real));  % padding pixels have phaseId = NaN
data = {'x', x, 'y', y, 'phase', double(fileMap), 'quat', A, 'ci', ci, ...
        'iq', ebsd.prop.iq(:), 'oldId', oldId};
if exist(outH5, 'file'), delete(outH5); end
for k = 1:2:numel(data)
  v = data{k+1}(real, :);
  h5create(outH5, ['/' data{k}], size(v'), 'Datatype', 'double');
  h5write(outH5, ['/' data{k}], v');       % transposed so h5py reads (n, cols)
end
end
