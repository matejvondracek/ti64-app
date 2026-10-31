function applyStyle(fig, style)
% APPLYSTYLE  Apply the shared figure style (ti64app Settings.figure, passed in as a struct by the
%   Python backend) to a MATLAB figure: size, line widths, colour map. (Skeleton.)
%   Fields used: width_cm, boundary_line_width, colormap_scalar. Missing fields keep MATLAB defaults.

arguments
  fig (1,1) matlab.ui.Figure
  style (1,1) struct
end
if isfield(style, 'width_cm')
  fig.Units = 'centimeters';
  pos = fig.Position;
  fig.Position = [pos(1:2) style.width_cm style.width_cm * pos(4) / pos(3)];
end
if isfield(style, 'colormap_scalar')
  colormap(fig, style.colormap_scalar);
end
if isfield(style, 'boundary_line_width')
  set(findall(fig, 'Type', 'line'), 'LineWidth', style.boundary_line_width);
end
end
