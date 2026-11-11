function saveFigure(f, out, dpi)
%SAVEFIGURE Write figure f to file `out` by its extension (fix 3.14 of the GUI: "Save" / "Export" of a result).
%   .fig  MATLAB figure (editable in MATLAB; opens visible although drawn invisibly)
%   .svg  vector (print -dsvg, text kept as text)
%   .pdf  vector (exportgraphics ContentType vector)
%   else  raster with exportgraphics at dpi (png, tif, jpg)
[~, ~, ext] = fileparts(char(out));
switch lower(ext)
  case '.fig'
    set(f, 'CreateFcn', 'set(gcbo, ''Visible'', ''on'')');
    savefig(f, char(out), 'compact');
  case '.svg'
    set(f, 'Renderer', 'painters');
    print(f, char(out), '-dsvg', '-vector');
  case '.pdf'
    exportgraphics(f, char(out), 'ContentType', 'vector', 'BackgroundColor', 'white');
  otherwise
    exportgraphics(f, char(out), 'Resolution', dpi, 'BackgroundColor', 'white');
end
end
