function [gid, nGrains] = calcGrains(id, p)
% CALCGRAINS  Alpha grain segmentation of a stored map (same parameters as the pymtex backend,
%   GrainParams in python/ti64app/backends/base.py): grains bounded by HAGB >= hagb_deg, LAGB
%   lagb_deg..hagb_deg inside grains ('angle', [hagb lagb]), 'alpha' and 'minPixel' passed explicitly.
%   [gid, nGrains] = ti64.calcGrains(id, p)   stores struct('grains', g, 'ebsd', ebsdWithGrainId).

ebsd = ti64.store('get', id);
[g, e2] = calcGrains(ebsd, 'angle', [p.hagb_deg p.lagb_deg] * degree, 'alpha', p.alpha, ...
                     'minPixel', p.min_px);
% MTEX 7 calcGrains also returns a MODIFIED map: not-indexed pixels within 'alpha' of a grain are
% marked indexed (with NaN orientation). Keep the measured map and only attach the grain ids, as
% the pymtex backend does (decisions D7).
ebsd.prop.grainId = e2.prop.grainId;
gid = ti64.store('put', struct('grains', g, 'ebsd', ebsd, 'source', id));
nGrains = numel(g);
end
