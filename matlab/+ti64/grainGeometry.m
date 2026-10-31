function grainGeometry(gid, h5file)
% GRAINGEOMETRY  MTEX per-grain geometry of a stored grains object to HDF5 (same keys as
%   GEOMETRY_KEYS in python/ti64app/backends/base.py and the pymtex backend):
%   id, perimeter_um (outer loop), caliper_long_um, caliper_short_um, caliper_long_angle_deg
%   (direction of the longest caliper, from map +x toward +y, 0..180).

S = ti64.store('get', gid);
g = S.grains;
cl = caliper(g, 'longest');
cs = caliper(g, 'shortest');
ang = mod(atan2d(cl.y(:), cl.x(:)), 180);
data = {'id', double(g.id(:)), 'perimeter_um', double(g.perimeter(:)), ...
  'caliper_long_um', double(norm(cl(:))), 'caliper_short_um', double(norm(cs(:))), ...
  'caliper_long_angle_deg', double(ang)};
if isfile(h5file), delete(h5file); end
for k = 1:2:numel(data)
  v = data{k+1};
  if isempty(v), v = zeros(0, 1); end
  h5create(h5file, ['/' data{k}], max(size(v'), 1), 'Datatype', 'double');
  if ~isempty(v), h5write(h5file, ['/' data{k}], v'); end
end
end
