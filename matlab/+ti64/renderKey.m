function renderKey(id, phase, keyName, vec, outPng, dpi)
% RENDERKEY  IPF colour key of one phase as its own image.
%   For beta with ipfHKLKey the corrected key of EBSD_part2.m is drawn (MATLAB default for beta):
%   same colour formula as MTEX's HKLDirectionKey, but rho wrapped/clamped, so grid points lying
%   exactly on the sector edge do not form a thin blue/purple sliver along [001]-[101].
%   (pymtex's HKL key shows no sliver, see docs/decisions.md.)

S = ti64.store('get', id);
if isstruct(S), ebsd = S.ebsd; else, ebsd = S; end
cs = ebsd.CSList(ebsd.name2id(phase));
key = feval(keyName, cs);
key.inversePoleFigureDirection = vector3d(vec(1), vec(2), vec(3));
f = figure('Visible', 'off', 'Color', 'w');
if strcmp(keyName, 'ipfHKLKey') && contains(cs.mineral, 'beta')
  dM = key.dirMap;
  v  = plotS2Grid(dM.sR, 'resolution', 0.5 * degree);
  v  = Miller(v, dM.sym);
  hv = project2FundamentalRegion(vector3d(v), dM.sym);
  [theta, rho] = polar(hv(:));
  [~, maxTheta]    = dM.sR.thetaRange;
  [minRho, maxRho] = dM.sR.rhoRange;
  rho(rho > pi) = rho(rho > pi) - 2 * pi;          % wrap tiny negative rho back
  rho = min(max(rho, minRho), maxRho);
  r = 1 - theta ./ maxTheta;
  g = theta ./ maxTheta .* (maxRho - rho) ./ (maxRho - minRho);
  b = theta ./ maxTheta .* (rho - minRho) ./ (maxRho - minRho);
  d = reshape([r(:) g(:) b(:)], [size(v), 3]);
  plot(v, d, 'surf');
else
  plot(key);
end
set(f, 'Color', 'w');
exportgraphics(f, outPng, 'Resolution', dpi, 'BackgroundColor', 'white');
close(f);
end
