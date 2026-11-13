function odfExport(odfId, file)
% ODFEXPORT  Write a stored ODF in MTEX's text format (phi1 Phi phi2 value, Bunge; loadODF / importODF) (phase 6.7).
S = ti64.store('get', odfId);
export(S.odf, char(file), 'Bunge');
end
