function odfEval(odfId, mode, hkl, r, h5in, h5out)
% ODFEVAL  Pole / inverse pole figure densities (MRD) of a stored ODF (phase 6.7; PymtexTexture.odf_pf / odf_ipf).
%   mode 'pf':  hkl = Miller indices (3 or 4 numbers), h5in /dirs (m x 3 specimen directions)
%   mode 'ipf': r = specimen direction (3), h5in /dirs (m x 3 crystal directions, cartesian)
%   Antipodal: the mean of the densities at +direction and -direction. Output /v (m).
S = ti64.store('get', odfId);
D = h5read(h5in, '/dirs')';
V = vector3d(D(:, 1), D(:, 2), D(:, 3));
switch mode
  case 'pf'
    hkl = num2cell(double(hkl(:)'));
    h = Miller(hkl{:}, S.cs);
    v = (calcPDF(S.odf, h, V) + calcPDF(S.odf, h, -V)) / 2;
  case 'ipf'
    rv = vector3d(r(1), r(2), r(3));
    H = Miller(V, S.cs);
    v = (calcPDF(S.odf, H, rv) + calcPDF(S.odf, H, -rv)) / 2;
  otherwise
    error('ti64:odfEval', 'mode must be pf or ipf');
end
v = double(v(:));
if isfile(h5out), delete(h5out); end
h5create(h5out, '/v', size(v'), 'Datatype', 'double');
h5write(h5out, '/v', v');
end
