function stiffnessRotate(id, phase, C66, h5in, doMean, h5out)
% STIFFNESSROTATE  Single-crystal stiffness rotated into the specimen frame for every orientation (phase 6.7; MTEX
%   tensors, Mainprice et al. 2011, doi:10.1144/SP360.10; PymtexTexture.stiffness_rotate).
%   C66: 6x6 Voigt stiffness (GPa) in the crystal frame; h5in: /quat (n x 4), /weights (n) when doMean.
%   doMean: weighted means of the rotated stiffness and compliance (chunks of 200 000), output /C, /S (81 values,
%   the full 3x3x3x3 tensors); else /C, /S (n x 81) per orientation. Python converts to Voigt notation.
S0 = ti64.store('get', id);
if isstruct(S0), ebsd = S0.ebsd; else, ebsd = S0; end
cs = ebsd(phase).CS;
C = stiffnessTensor(C66, cs);
Sc = inv(C);
Q = h5read(h5in, '/quat')';
n = size(Q, 1);
if doMean
  w = h5read(h5in, '/weights'); w = w(:) / sum(w(:));
  accC = zeros(3, 3, 3, 3); accS = zeros(3, 3, 3, 3);
  chunk = 200000;
  for k = 1:chunk:n
    idx = k:min(n, k + chunk - 1);
    o = orientation(quaternion(Q(idx, 1), Q(idx, 2), Q(idx, 3), Q(idx, 4)), cs);
    Cr = rotate(C, o); Sr = rotate(Sc, o);
    accC = accC + sum(Cr.M .* reshape(w(idx), [1 1 1 1 numel(idx)]), 5);
    accS = accS + sum(Sr.M .* reshape(w(idx), [1 1 1 1 numel(idx)]), 5);
  end
  outC = reshape(permute(accC, [4 3 2 1]), 1, 81);       % row-major order (i j k l) as numpy reshape
  outS = reshape(permute(accS, [4 3 2 1]), 1, 81);
else
  o = orientation(quaternion(Q(:, 1), Q(:, 2), Q(:, 3), Q(:, 4)), cs);
  Cr = rotate(C, o); Sr = rotate(Sc, o);
  outC = reshape(permute(Cr.M, [5 4 3 2 1]), n, 81);
  outS = reshape(permute(Sr.M, [5 4 3 2 1]), n, 81);
end
if isfile(h5out), delete(h5out); end
h5create(h5out, '/C', size(outC'), 'Datatype', 'double'); h5write(h5out, '/C', outC');
h5create(h5out, '/S', size(outS'), 'Datatype', 'double'); h5write(h5out, '/S', outS');
end
