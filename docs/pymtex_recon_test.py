# pymtex 1.0.0 port of the EBSD_part2.m reconstruction core (no figures, no merge sweep).
# Test run on JU02, 2026-10-31: see chat. pip install "mtex[all]"  (Python >= 3.12)
import time, numpy as np, sys
try:
    import resource  # not available on Windows
except ImportError:
    resource = None
from mtex import *
def rss(): return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e6,2) if resource else float('nan')
from pathlib import Path
f = str(Path(__file__).resolve().parents[1] / "data" / "test" / "EBM_Ti64_JU02_C_Area 3_OIM Map 1_Rescan_Rescan_cleaned.ang")
T=time.time(); t=time.time()
def lap(s):
    global t; print(f"{s}: {time.time()-t:.1f} s, peak RSS {rss()} GB", flush=True); t=time.time()
ebsdRaw = loadEBSD(f, setting=2); lap("load")
e1 = ebsdRaw.copy()
grains1 = calcGrains(e1, angle=3*degree); lap("calcGrains 3deg (all pixels)")
gid1 = np.asarray(e1.prop['grainId']).reshape(-1) if 'grainId' in e1.prop else np.asarray(e1.grainId).reshape(-1)
npx = np.asarray(grains1.numPixel).reshape(-1); isIdx = np.asarray(grains1.isIndexed).reshape(-1)
isNoise = (isIdx & (npx<=2)) | (~isIdx & (npx<=4))
keep = ~np.isin(gid1, np.asarray(grains1.id).reshape(-1)[isNoise])
print("noise grains", isNoise.sum(), "pixels removed", (~keep).sum())
ebsd = ebsdRaw.flatten()[keep] if hasattr(ebsdRaw,'flatten') else ebsdRaw[keep]
lap("noise removal")
grains = calcGrains(ebsd, angle=1.5*degree); lap("calcGrains 1.5deg")
grains = smoothBoundary(grains, 5); lap("smooth 5")
print("alpha grains", len(grains))
job = parentGrainReconstructor(ebsd, grains)
csP = ebsd['Ti (beta)'].CS; csC = ebsd['Ti (alpha)'].CS
p2cB = orientation.Burgers(csP, csC); job.p2c = p2cB
job.calcParent2Child(); dOR = float(angle(job.p2c, p2cB)/degree); print("OR dev", round(dOR,2))
if dOR > 1: job.p2c = p2cB
lap("job + OR refine")
job.calcVariantGraph(threshold=5*degree, tol=1.5*degree); lap("variant graph")
job.clusterVariantGraph(numIter=3, inflationPower=1.05); lap("cluster")
job.calcParentFromVote(minProb=0); lap("vote")
job.mergeSimilar(threshold=3*degree); lap("mergeSimilar 3")
job.mergeInclusions(maxSize=5); lap("mergeInclusions 5")
pe = job.calcParentEBSD(); lap("calcParentEBSD")
pg = job.parentGrains
A = np.asarray(pg.area()).reshape(-1) if callable(getattr(pg,'area',None)) else np.asarray(pg.area).reshape(-1)
E = 2*np.sqrt(A/np.pi)
ca = job.grainsPrior.area; ca = np.asarray(ca() if callable(ca) else ca).reshape(-1)
isC = np.asarray(job.grainsPrior.phaseId == job.childPhaseId).reshape(-1)
rec = ca[np.asarray(job.isTransformed).reshape(-1)].sum()/ca[isC].sum()
fit = np.asarray(pe.fit).reshape(-1)/degree; fit = fit[~np.isnan(fit)]
print(f"RESULT grains>=5um {np.sum(E>=5)} (all {len(E)}) recFrac {100*rec:.1f}% meanFit {fit.mean():.2f} badFit {100*np.mean(fit>3):.1f}% ECDaw {np.sum(E*A)/A.sum():.1f} total {(time.time()-T)/60:.1f} min peak {rss()} GB")
