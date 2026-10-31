function ipfColorsQuat(id, phase, keyName, vec, h5in, h5file)
% IPFCOLORSQUAT  IPF colours of arbitrary orientations of one phase of a stored map (e.g. parent
%   grain mean orientations). h5in: /quat (n x 4, a b c d, NaN rows allowed). Output /rgb (n x 3).
S = ti64.store('get', id);
if isstruct(S), ebsd = S.ebsd; else, ebsd = S; end
cs = ebsd(phase).CS;
Q = h5read(h5in, '/quat')';
rgb = nan(size(Q, 1), 3);
ok = all(isfinite(Q), 2);
if any(ok)
  o = orientation(quaternion(Q(ok, 1), Q(ok, 2), Q(ok, 3), Q(ok, 4)), cs);
  key = feval(keyName, cs);
  key.inversePoleFigureDirection = vector3d(vec(1), vec(2), vec(3));
  rgb(ok, :) = reshape(key.orientation2color(o), [], 3);
end
if isfile(h5file), delete(h5file); end
h5create(h5file, '/rgb', max(size(rgb'), 1), 'Datatype', 'double');
h5write(h5file, '/rgb', rgb');
end
