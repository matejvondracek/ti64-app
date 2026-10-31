function info = exportEbsd(id, h5file)
% EXPORTEBSD  Write the per-pixel arrays of a stored EBSD map to HDF5 in FILE row order.
%   info = ti64.exportEbsd(id, h5file)
%   Datasets: x, y (um), phase (file phase numbers, 0 = not indexed), quat (n x 4, a b c d,
%   NaN if not indexed), prop_ci, prop_iq, prop_fit, prop_sem (neutral names, as in the pymtex
%   backend; plus prop_<name> for every prop ti64_<name>). Padding pixels of the hex grid are dropped; rows are sorted by prop.oldId.
%   info: struct with phaseNames (cell, index = file phase number + 1), stepUm, isHex, setting, n.
%   Arrays are written transposed so h5py reads them as (n, cols).

S = ti64.store('get', id);
if isstruct(S), ebsd = S.ebsd; else, ebsd = S; end
oldId = ebsd.prop.oldId(:);
real = ~isnan(oldId);
[~, order] = sort(oldId(real));
idx = find(real);
idx = idx(order);

pid = ebsd.phaseId(:);
phaseFile = ebsd.phaseMap(pid(idx));                 % file numbering (0, 1, 2, ...)
q = ebsd.rotations(:);
quat = [q.a q.b q.c q.d];
quat = quat(idx, :);
quat(~ebsd.isIndexed(idx), :) = NaN;
x = ebsd.x(:); y = ebsd.y(:);

data = {'x', x(idx), 'y', y(idx), 'phase', double(phaseFile), 'quat', quat};
names = struct('ci', 'ci', 'iq', 'iq', 'fit', 'fit_prias', 'sem', 'sem');   % neutral -> MTEX 7.2
f = fieldnames(names);
for k = 1:numel(f)
  if isfield(ebsd.prop, names.(f{k}))
    v = ebsd.prop.(names.(f{k}))(:);
    data(end+1:end+2) = {['prop_' f{k}], v(idx)};    %#ok<AGROW>
  end
end
extra = fieldnames(ebsd.prop);
extra = extra(startsWith(extra, 'ti64_'));             % app-specific per-pixel flags
for k = 1:numel(extra)
  v = ebsd.prop.(extra{k})(:);
  data(end+1:end+2) = {['prop_' extra{k}(6:end)], v(idx)};    %#ok<AGROW>
end
if isfile(h5file), delete(h5file); end
for k = 1:2:numel(data)
  v = double(data{k+1});
  h5create(h5file, ['/' data{k}], size(v'), 'Datatype', 'double');
  h5write(h5file, ['/' data{k}], v');
end

mins = ebsd.mineralList;
info.phaseNames = mins;
info.phaseMap = ebsd.phaseMap(:)';
info.isHex = isa(ebsd, 'EBSDhex');
info.stepUm = ebsd.dx;
info.setting = ebsd.opt.ti64setting;
info.n = numel(idx);
end
