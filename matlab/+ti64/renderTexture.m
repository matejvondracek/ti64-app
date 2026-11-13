function renderTexture(odfId, spec, h5scatter, out)
% RENDERTEXTURE  Pole figures or inverse pole figures of a stored ODF in the style of XRD_part2.m (phase 6.7):
%   contoured density (MRD) and / or scatter, equal-area, upper hemisphere, antipodal, one colour range for the whole
%   figure (setColorRange equal), turbo, sample-direction labels with a white background; saved as `out` and, for a
%   PNG, also as a MATLAB .fig next to it (XRD_part2.m: .fig + PNG). Same content as PymtexTexture.render_texture.
%   spec: kind 'pf'|'ipf', labels (cell: titles), hkl (cell of index vectors, pf), vectors (n x 3, ipf),
%         mode 'density'|'scatter'|'both', axisLabels {x y z}, clim [lo hi] or [], title, widthCm, dpi
%   h5scatter: '' or an HDF5 file with /quat (k x 4) orientations in the frame for the scatter.
S = ti64.store('get', odfId);
odf = S.odf; cs = S.cs;
dens = any(strcmp(spec.mode, {'density', 'both'}));
sc = any(strcmp(spec.mode, {'scatter', 'both'})) && ~isempty(h5scatter);
if sc
  Q = h5read(h5scatter, '/quat')';
  ori = orientation(quaternion(Q(:, 1), Q(:, 2), Q(:, 3), Q(:, 4)), cs);
end
pC = plottingConvention(zvector, xvector);                % specimen frame: x east, y north, z out of the screen
n = numel(spec.labels);
f = figure('Visible', 'off', 'Color', 'w', 'Units', 'centimeters', ...
  'Position', [0 0 spec.widthCm spec.widthCm / max(n, 1) * 1.25 + 1]);
if strcmp(spec.kind, 'pf')
  h = cellfun(@(x) millerOf(x, cs), spec.hkl, 'UniformOutput', false);
  if dens
    plotPDF(odf, h, 'contourf', 'antipodal', 'upper', 'projection', 'eangle', pC);
  else
    plotPDF(ori, h, 'antipodal', 'upper', 'projection', 'eangle', pC, 'MarkerSize', 2, 'MarkerColor', [0.12 0.23 0.45]);
  end
  if dens && sc
    hold on
    plotPDF(ori, h, 'antipodal', 'upper', 'projection', 'eangle', pC, 'MarkerSize', 0.7, 'MarkerColor', 'k', ...
      'add2all');
    hold off
  end
  % MTEX's own X / Y / Z letters are replaced by the specimen-direction labels (XRD_part2.m: annotate)
  delete(findall(f, 'Type', 'text', '-regexp', 'String', '^[XYZxyz]$'));
  annotate([xvector, yvector, zvector], 'label', cellstr(spec.axisLabels), 'BackgroundColor', 'w');
else
  V = spec.vectors;
  r = vector3d(V(:, 1), V(:, 2), V(:, 3));
  if dens
    plotIPDF(odf, r, 'contourf', 'antipodal');
  else
    plotIPDF(ori, r, 'antipodal', 'MarkerSize', 2, 'MarkerColor', [0.12 0.23 0.45]);
  end
  if dens && sc
    hold on
    plotIPDF(ori, r, 'antipodal', 'MarkerSize', 0.7, 'MarkerColor', 'k', 'add2all');
    hold off
  end
end
mf = gcm;
for k = 1:min(n, numel(mf.children))
  title(mf.children(k), spec.labels{k}, 'Interpreter', 'none');
end
if dens
  if isempty(spec.clim)
    setColorRange('equal');
  else
    setColorRange(double(spec.clim(:)'));
  end
  colormap(turbo(256));
  mtexColorbar('title', 'MRD');
end
if ~isempty(spec.title), sgtitle(f, spec.title, 'FontSize', 9, 'Interpreter', 'none'); end
ti64.plot.saveFigure(f, out, spec.dpi);
[p, nm, ext] = fileparts(char(out));
if strcmpi(ext, '.png')
  ti64.plot.saveFigure(f, fullfile(p, [nm '.fig']), spec.dpi);
end
close(f);
end

function h = millerOf(x, cs)
x = num2cell(double(x(:)'));
h = Miller(x{:}, cs);
end
