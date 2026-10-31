function [src, sweeps] = dilationFill(toFill, grainId, sourceOk, nbr)
% DILATIONFILL  Grain-dilation-like fill (same algorithm as
%   python/ti64app/backends/hexgrid.py dilation_fill; keep both identical).
%   Every pixel in toFill takes the orientation of one neighbour: among its neighbours that are
%   already valid (sourceOk or filled earlier), the grain with the most neighbouring pixels wins;
%   ties -> the grain of the first such neighbour in the fixed order (W, E, NW, NE, SW, SE).
%   Sweeps: all pixels of one sweep are decided from the state before it; repeat until no change.
%   src(i) = index of the pixel whose orientation i copies (i if not filled, 0 if it could not be).

n = numel(toFill);
toFill = toFill(:); grainId = double(grainId(:)); ok = logical(sourceOk(:));
src = (1:n)';
todo = find(toFill & ~ok);
src(todo) = 0;
sweeps = 0;
k = size(nbr, 2);
while ~isempty(todo)
  nb = nbr(todo, :);
  has = nb > 0;
  nbok = false(size(has));
  nbok(has) = ok(nb(has));
  g = -ones(size(nb));
  g(nbok) = grainId(nb(nbok));
  anyok = any(nbok, 2);
  if ~any(anyok), break; end
  cnt = zeros(size(nb));
  for a = 1:k
    cnt(:, a) = sum(g == g(:, a), 2);
  end
  cnt(~nbok) = -1;
  [~, best] = max(cnt, [], 2);                   % first slot with the highest count
  rows = find(anyok);
  winner = nb(sub2ind(size(nb), rows, best(rows)));
  t = todo(rows);
  src(t) = src(winner);
  grainId(t) = grainId(winner);
  ok(t) = true;
  todo = todo(~anyok);
  sweeps = sweeps + 1;
end
end
