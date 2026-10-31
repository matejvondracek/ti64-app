function [id, nPoints] = loadEbsd(fname, setting)
% LOADEBSD  Load a TSL/OIM .ang with MTEX 7 and keep it in the session store.
%   [id, nPoints] = ti64.loadEbsd(fname, setting)   setting = EDAX setting (default 2, decisions D1)
%   Same parameters as the pymtex backend: loadEBSD(f, setting=2).

arguments
  fname {mustBeTextScalar}
  setting (1,1) double {mustBeMember(setting, 0:4)} = 2
end
ebsd = EBSD.load(char(fname), 'setting', setting);
ebsd.opt.ti64setting = setting;
id = ti64.store('put', ebsd);
nPoints = nnz(~isnan(ebsd.prop.oldId));
end
