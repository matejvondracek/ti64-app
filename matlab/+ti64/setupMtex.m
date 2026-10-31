function mtexPath = setupMtex(mtexPath)
% SETUPMTEX  Put MTEX 7.x on the MATLAB path and start it, using the machine settings.
%   ti64.setupMtex()            reads paths.mtex_path from the settings file (ti64.readSettings)
%   ti64.setupMtex(mtexPath)    uses the given MTEX folder (the Python backend passes it in)
%   Does nothing if the same MTEX is already running. Errors if the folder has no startup_mtex.m
%   or if a different MTEX version is already on the path.

if nargin < 1 || isempty(mtexPath)
  s = ti64.readSettings();
  mtexPath = s.mtex_path;
end
mtexPath = char(mtexPath);
if isempty(mtexPath)
  error('ti64:setupMtex:noPath', ...
    'paths.mtex_path is not set (settings.local.toml or %%APPDATA%%\\ti64app\\settings.toml)');
end
if ~isfile(fullfile(mtexPath, 'startup_mtex.m'))
  error('ti64:setupMtex:notMtex', 'No startup_mtex.m in %s', mtexPath);
end

current = which('startup_mtex');
if ~isempty(current)
  if strcmpi(fileparts(current), mtexPath) && exist('EBSD', 'class') == 8
    return                                   % already running from this folder
  elseif ~strcmpi(fileparts(current), mtexPath)
    error('ti64:setupMtex:otherMtex', 'Another MTEX is on the path: %s', fileparts(current));
  end
end

addpath(mtexPath);
startup_mtex;
v = getMTEXpref('version');
if ~startsWith(v, 'mtex-7.')
  warning('ti64:setupMtex:version', 'Expected MTEX 7.x, found %s', v);
end
end
