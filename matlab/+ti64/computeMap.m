function computeMap(gid, p, h5file)
% COMPUTEMAP  Per-pixel orientation map of a stored grains object, written to HDF5 as /values in
%   FILE order (same parameters as MapParams in python/ti64app/backends/base.py):
%   kam  - KAM inside grains (ebsd carries grainId), kernel order p.kam_order, cutoff p.kam_threshold_deg [deg]
%   grod - grain reference orientation deviation per phase [deg]
%   gos  - grain mean of GROD [deg]
%   gnd  - GND density of p.gnd_phase [1/m^2], Pantleon 2008 doi:10.1016/j.scriptamat.2008.01.050:
%          basal, prismatic, pyramidal <a> + pyramidal <c+a>, edge + screw, screw energy 1 - nu;
%          gradient stencil p.gnd_stencil (default '1hop': symmetric least squares, decisions D8).

S = ti64.store('get', gid);
g = S.grains; ebsd = S.ebsd;
n = numel(ebsd.phaseId);
out = nan(n, 1);
pid = ebsd.phaseId(:);
switch p.kind
  case 'kam'
    v = ebsd.KAM('order', p.kam_order, 'threshold', p.kam_threshold_deg * degree);
    out = v(:) / degree;
    out(~ebsd.isIndexed(:)) = NaN;
  case {'grod', 'gos'}
    for k = 1:numel(ebsd.CSList)
      cs = ebsd.CSList(k);
      if ~isa(cs, 'crystalSymmetry') || ~any(pid == k), continue; end
      m = pid == k;
      sub = ebsd(m);
      mo = calcGROD(sub, g);
      out(m) = angle(mo(:)) / degree;
    end
    if strcmp(p.kind, 'gos')
      gidp = ebsd.grainId(:); gidp(isnan(gidp)) = 0;
      ok = ~isnan(out) & gidp > 0;
      s = accumarray(gidp(ok), out(ok), [max(gidp) 1]);
      c = accumarray(gidp(ok), 1, [max(gidp) 1]);
      meanG = s ./ c;
      out = nan(n, 1);
      out(gidp > 0) = meanG(gidp(gidp > 0));
      out(~ebsd.isIndexed(:)) = NaN;
    end
  case 'gnd'
    m = pid == ebsd.name2id(p.gnd_phase);
    sub = ebsd(p.gnd_phase);
    cs = sub.CS;
    sS = [slipSystem.basal(cs), slipSystem.prismaticA(cs), slipSystem.pyramidalA(cs), slipSystem.pyramidalCA(cs)];
    sS = sS.symmetrise('antipodal');
    dS = dislocationSystem(sS);
    dS(dS.isEdge).u = 1;
    dS(dS.isScrew).u = 1 - p.poisson;
    % = calcGND(sub, dS, 'stencil', ...) but the fit in chunks: the systems rotated into every pixel
    % need ~1.6 kB per pixel (out of memory on a 2.6 M-pixel map at once)
    kappa = sub.curvature('stencil', p.gnd_stencil);  % not across grain boundaries (grainId)
    ori = sub.orientations;
    gnd = nan(numel(ori), 1);
    chunk = 50000;                                     % ~1 GB of temporaries per 250 k px in MTEX 7.2
    for a = 1:chunk:numel(ori)
      k = a:min(a + chunk - 1, numel(ori));
      dSRot = ori(k) * dS;
      [rho, factor] = fitDislocationSystems(kappa(k), dSRot);
      gnd(k) = factor * sum(abs(rho .* dSRot.u), 2);
    end
    out(m) = gnd;
  otherwise
    error('ti64:computeMap:kind', 'unknown map kind %s', p.kind);
end
oldId = ebsd.prop.oldId(:);
real = ~isnan(oldId);
[~, order] = sort(oldId(real));
idx = find(real); idx = idx(order);
v = out(idx);
if isfile(h5file), delete(h5file); end
h5create(h5file, '/values', size(v'), 'Datatype', 'double');
h5write(h5file, '/values', v');
end
