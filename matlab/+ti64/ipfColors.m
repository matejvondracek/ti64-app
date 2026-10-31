function ipfColors(id, phase, keyName, vec, h5file)
% IPFCOLORS  IPF colours of one phase of a stored map (or grains object), FILE order, to HDF5 /rgb
%   (n x 3, NaN for other pixels). keyName: ipfHKLKey | ipfTSLKey | ipfHSVKey | ipfSpotKey
%   (same names as pymtex); vec: reference direction in map coordinates [x y z].

S = ti64.store('get', id);
if isstruct(S), ebsd = S.ebsd; else, ebsd = S; end
oldId = ebsd.prop.oldId(:);
real = ~isnan(oldId);
[~, order] = sort(oldId(real));
idx = find(real); idx = idx(order);
rgb = nan(numel(ebsd.phaseId), 3);
m = ebsd.phaseId(:) == ebsd.name2id(phase);
if any(m)
  sub = ebsd(phase);
  key = feval(keyName, sub.CS);
  key.inversePoleFigureDirection = vector3d(vec(1), vec(2), vec(3));
  c = key.orientation2color(sub.orientations);
  % sub keeps the grid order of the phase's pixels
  rgb(m, :) = reshape(c, [], 3);
end
v = rgb(idx, :);
if isfile(h5file), delete(h5file); end
h5create(h5file, '/rgb', size(v'), 'Datatype', 'double');
h5write(h5file, '/rgb', v');
end
