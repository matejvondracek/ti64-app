function [odfId, maxMrd, ti] = odfBuild(id, phase, h5in, halfwidthDeg)
% ODFBUILD  Kernel-density ODF of weighted orientations of one phase (phase 6.7; same as PymtexTexture.odf_build).
%   [odfId, maxMrd, ti] = ti64.odfBuild(id, phase, h5in, halfwidthDeg)
%   id: a stored map (gives the crystal symmetry); h5in: /quat (n x 4, a b c d, specimen frame), /weights (n)
%   calcDensity with the de la Vallee Poussin kernel of the given halfwidth; maxMrd = max(odf) (MRD), ti = texture
%   index = squared L2 norm (norm(odf)^2; textureindex is deprecated in MTEX 7.2).
S = ti64.store('get', id);
if isstruct(S), ebsd = S.ebsd; else, ebsd = S; end
cs = ti64.phaseCS(ebsd, phase);
Q = h5read(h5in, '/quat')';
w = h5read(h5in, '/weights'); w = w(:) / sum(w(:));
o = orientation(quaternion(Q(:, 1), Q(:, 2), Q(:, 3), Q(:, 4)), cs);
odf = calcDensity(o, 'weights', w, 'halfwidth', halfwidthDeg * degree);
maxMrd = max(odf);
ti = norm(odf)^2;
odfId = ti64.store('put', struct('odf', odf, 'cs', cs, 'phase', phase));
end
