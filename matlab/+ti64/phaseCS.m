function cs = phaseCS(ebsd, phase)
% PHASECS  Crystal symmetry of a phase by name from the phase LIST (not from its pixels, which may be none:
%   a crop without beta pixels, or a raw APEX export without a beta phase). 'Ti (beta)' missing from the list:
%   m-3m, a = 3.32 A (EBSD_part2.m; only the symmetry matters for colours, ODFs and tensors). fix 6.7.1 item 1.
id = ebsd.name2id(phase);
if id > 0
  cs = ebsd.CSList(id);
elseif contains(lower(string(phase)), 'beta')
  cs = crystalSymmetry('m-3m', [3.32 3.32 3.32], 'mineral', char(phase));
else
  error('ti64:phaseCS', 'phase ''%s'' is not in the map', phase);
end
end
