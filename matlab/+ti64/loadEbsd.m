function [id, nPoints] = loadEbsd(fname, setting, phaseIds, phaseNames)
% LOADEBSD  Load a TSL/OIM .ang with MTEX 7 and keep it in the session store.
%   [id, nPoints] = ti64.loadEbsd(fname, setting, phaseIds, phaseNames)
%   setting    = EDAX setting (default 2, decisions D1)
%   phaseIds   = file phase numbers to rename (double vector), phaseNames = their new names (cell of char):
%                the app's canonical 'Ti (alpha)' / 'Ti (beta)' chosen by crystal symmetry (ti64app.phases).
%   Same parameters as the pymtex backend: loadEBSD(f, setting=2) + CSList(id).mineral = name.
%
%   Phase numbers: MTEX 7.2 numbers the phase of a SINGLE-phase .ang 0 (the rows say 0) with notIndexed = -1,
%   pymtex numbers it 1. phaseMap is set to the CSList order (0 = notIndexed, 1, 2, ... as in the header), so both
%   backends report the same file phase numbers (a single-phase alpha map used to come out as 'not indexed').

arguments
  fname {mustBeTextScalar}
  setting (1,1) double {mustBeMember(setting, 0:4)} = 2
  phaseIds double = []
  phaseNames = {}
end
ws = warning('off', 'all');                   % MTEX's 'do not form a grid' warning, handled below
ebsd = EBSD.load(char(fname), 'setting', setting);
warning(ws);
if ~isfield(ebsd.prop, 'oldId')
  % fix 5: MTEX 7.2's automatic grid detection gives up on short, very wide maps (e.g. 120 x 1501 points: "the
  % measurements span 2433600 lattice cells") and keeps a list; an explicit gridify finds the hex / square grid
  ebsd = gridify(ebsd);
  if ~isfield(ebsd.prop, 'oldId')
    error('ti64:loadEbsd', '%s: the points do not form a regular (hex or square) grid', fname);
  end
end
ebsd.phaseMap = (0:numel(ebsd.CSList) - 1)';
if ischar(phaseNames) || isstring(phaseNames), phaseNames = cellstr(phaseNames); end
csl = ebsd.CSList;                          % MTEX 7.2: an array of phaseItem (crystalSymmetry / notIndexed)
for k = 1:numel(phaseIds)
  i = phaseIds(k) + 1;                      % csl(1) = notIndexed
  if i > 1 && i <= numel(csl) && isa(csl(i), 'crystalSymmetry')
    csl(i).mineral = char(phaseNames{k});
  end
end
ebsd.CSList = csl;
ebsd.opt.ti64setting = setting;
id = ti64.store('put', ebsd);
nPoints = nnz(~isnan(ebsd.prop.oldId));
end
