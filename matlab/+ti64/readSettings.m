function s = readSettings(file)
% READSETTINGS  Machine paths from the ti64app settings TOML (same lookup order as Python):
%   1. getenv('TI64_SETTINGS')  2. <repo>/settings.local.toml  3. %APPDATA%\ti64app\settings.toml
%   Returns a struct with mtex_path, matlab_root, literature_dir, cache_dir ('' if missing).
%   Only simple  key = 'value'  /  key = "value"  lines are parsed (enough for [paths]).

s = struct('mtex_path', '', 'matlab_root', '', 'literature_dir', '', 'cache_dir', '', 'file', '');
if nargin < 1 || isempty(file)
  repo = fileparts(fileparts(fileparts(mfilename('fullpath'))));   % matlab/+ti64 -> repo
  candidates = {getenv('TI64_SETTINGS'), fullfile(repo, 'settings.local.toml'), ...
                fullfile(getenv('APPDATA'), 'ti64app', 'settings.toml')};
  file = '';
  for k = 1:numel(candidates)
    if ~isempty(candidates{k}) && isfile(candidates{k}), file = candidates{k}; break; end
  end
  if isempty(file), return; end
end
s.file = char(file);
lines = readlines(file);
for k = 1:numel(lines)
  tok = regexp(lines(k), '^\s*(\w+)\s*=\s*([''"])(.*?)\2', 'tokens', 'once');
  if ~isempty(tok) && isfield(s, tok(1))
    s.(tok(1)) = char(tok(3));
  end
end
end
