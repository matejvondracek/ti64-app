"""Neighbour tables and the grain-dilation-like fill on the map grid (pymtex backend side).

The MATLAB backend has the same algorithm in matlab/+ti64/fillPixels.m; both must stay identical
so the two backends fill the same pixels with the same orientations (conformance test).

Grid: the 2-D array layout of the map (rows x cols, MTEX/pymtex grid order, padding allowed).
Neighbours are found from coordinates, so the row parity convention of the hex grid does not matter:
  hex grid:    key = (row, half-column h = round((x - xmin) / (dx/2)));
               neighbours (row, h-2), (row, h+2), (row-1, h-1), (row-1, h+1), (row+1, h-1), (row+1, h+1)
  square grid: (row, col +- 1), (row +- 1, col)
Fixed neighbour ORDER (used for tie-breaking): W, E, NW, NE, SW, SE  (N = previous row).
"""
from __future__ import annotations

import numpy as np


def neighbour_table(x: np.ndarray, y: np.ndarray, valid: np.ndarray, hex_grid: bool = True) -> np.ndarray:
    """(n, k) indices of the neighbours of every pixel (flat arrays), -1 where there is none.
    `valid` excludes padding pixels (they never appear as neighbours)."""
    x = np.asarray(x, float).ravel()
    y = np.asarray(y, float).ravel()
    valid = np.asarray(valid, bool).ravel()
    ys = np.unique(np.round(y[valid], 4))
    dy = float(np.median(np.diff(ys))) if ys.size > 1 else 1.0
    row = np.round((y - ys[0]) / dy).astype(np.int64)
    r0 = valid & (row == 0)
    xr = np.sort(x[r0])
    dx = float(np.median(np.diff(xr))) if xr.size > 1 else 1.0
    xmin = float(x[valid].min())
    if hex_grid:
        col = np.round((x - xmin) / (dx / 2)).astype(np.int64)
        offs = [(0, -2), (0, 2), (-1, -1), (-1, 1), (1, -1), (1, 1)]
    else:
        col = np.round((x - xmin) / dx).astype(np.int64)
        offs = [(0, -1), (0, 1), (-1, 0), (1, 0)]
    ncol = int(col[valid].max()) + 3
    key = row * ncol + col
    vk = key[valid]
    vi = np.flatnonzero(valid)
    order = np.argsort(vk)
    sk, si = vk[order], vi[order]
    out = np.full((x.size, len(offs)), -1, dtype=np.int64)
    for j, (dr, dc) in enumerate(offs):
        k = (row + dr) * ncol + (col + dc)
        pos = np.searchsorted(sk, k)
        pos = np.clip(pos, 0, sk.size - 1)
        hit = (sk[pos] == k) & valid & (col + dc >= 0)
        out[hit, j] = si[pos[hit]]
    return out


def dilation_fill(to_fill: np.ndarray, grain_id: np.ndarray, source_ok: np.ndarray,
                  nbr: np.ndarray, max_iter: int = 10_000) -> tuple[np.ndarray, int]:
    """Grain-dilation-like fill. Every pixel in `to_fill` takes the orientation of one neighbour:
    among its neighbours that are already valid (source_ok or filled earlier), the grain with the
    most neighbouring pixels wins; ties -> the grain of the first such neighbour in the fixed order.
    Repeats in sweeps (all pixels of one sweep decided from the state before it) until nothing changes.

    Returns (source, n_sweeps): source[i] = index of the pixel whose orientation i copies (i for
    pixels not filled; -1 for pixels that could not be filled). Grain ids of filled pixels are the
    winner's (grain_id is updated in place)."""
    n = to_fill.size
    src = np.arange(n, dtype=np.int64)
    ok = source_ok.copy()
    todo = np.flatnonzero(to_fill & ~ok)
    src[todo] = -1
    sweeps = 0
    while todo.size and sweeps < max_iter:
        nb = nbr[todo]                                       # (m, k)
        has = nb >= 0
        nbok = np.zeros_like(has)
        nbok[has] = ok[nb[has]]
        g = np.where(nbok, grain_id[np.where(has, nb, 0)], -1)    # grain of each usable neighbour
        anyok = nbok.any(axis=1)
        if not anyok.any():
            break
        # count, for every neighbour slot, how many usable neighbours share its grain
        cnt = (g[:, :, None] == g[:, None, :]).sum(axis=2)
        cnt[~nbok] = -1
        best = np.argmax(cnt, axis=1)                        # first slot with the highest count
        rows = np.flatnonzero(anyok)
        winner = nb[rows, best[rows]]
        t = todo[rows]
        src[t] = src[winner]                                 # copy the winner's (original) source
        grain_id[t] = grain_id[winner]
        ok[t] = True
        todo = todo[~anyok]
        sweeps += 1
    return src, sweeps


def _dilation_kernel():
    """numba kernel of dilation_sweeps (compiled on first use, cached on disk)."""
    import numba  # noqa: PLC0415

    @numba.njit(cache=True)
    def kern(todo, grain_id, ok0, nbr, src, max_iter, counts):
        n = ok0.size
        stamp = np.full(n, -1, np.int64)          # sweep in which a pixel was filled (-1: not filled)
        k = nbr.shape[1]
        g = np.empty(k, np.int64)
        use = np.empty(k, np.bool_)
        left = todo.copy()
        nleft = left.size
        sweep = 0
        while nleft > 0 and sweep < max_iter:
            nxt = 0
            done = 0
            for t in range(nleft):
                i = left[t]
                anyok = False
                for j in range(k):
                    nb = nbr[i, j]
                    u = nb >= 0 and (ok0[nb] or (stamp[nb] >= 0 and stamp[nb] < sweep))
                    use[j] = u
                    g[j] = grain_id[nb] if u else -1
                    anyok = anyok or u
                if not anyok:
                    left[nxt] = i
                    nxt += 1
                    continue
                best, bestc = -1, -1
                for j in range(k):                # first slot with the most usable neighbours of its grain
                    if not use[j]:
                        continue
                    c = 0
                    for jj in range(k):
                        if use[jj] and g[jj] == g[j]:
                            c += 1
                    if c > bestc:
                        best, bestc = j, c
                w = nbr[i, best]
                src[i] = src[w]
                grain_id[i] = grain_id[w]
                stamp[i] = sweep
                done += 1
            if done == 0:
                break
            counts[sweep] = done
            nleft = nxt
            sweep += 1
        return sweep

    return kern


_KERN = None


def dilation_sweeps(to_fill: np.ndarray, grain_id: np.ndarray, source_ok: np.ndarray, nbr: np.ndarray,
                    max_iter: int = 100_000) -> tuple[np.ndarray, list[int]]:
    """dilation_fill (same rules, same result) with numba, also returning how many pixels changed per sweep
    (grain dilation reports them per iteration). grain_id is updated in place."""
    global _KERN
    if _KERN is None:
        _KERN = _dilation_kernel()
    n = to_fill.size
    src = np.arange(n, dtype=np.int64)
    ok0 = np.ascontiguousarray(source_ok, dtype=np.bool_)
    todo = np.flatnonzero(to_fill & ~ok0).astype(np.int64)
    src[todo] = -1
    counts = np.zeros(max(1, min(max_iter, todo.size + 1)), np.int64)
    gid = np.ascontiguousarray(grain_id, dtype=np.int64)
    sweeps = _KERN(todo, gid, ok0, np.ascontiguousarray(nbr, dtype=np.int64), src, int(max_iter), counts)
    grain_id[:] = gid
    return src, [int(c) for c in counts[:sweeps]]


def component_sizes(mask: np.ndarray, nbr: np.ndarray) -> np.ndarray:
    """Per pixel: size of its connected region within `mask` (neighbours from neighbour_table), 0 outside the mask.
    Used for not-indexed regions that the grain reconstruction leaves without a grain (MTEX 7.2 / pymtex 1.0
    calcGrains with alpha = 0 give large not-indexed regions grain id 0, phase 6.6 fix 4): their real size decides
    whether they are a small void (filled) or not. matlab/+ti64/cleanup.m does the same with conncomp."""
    from scipy.sparse import coo_matrix  # noqa: PLC0415
    from scipy.sparse.csgraph import connected_components  # noqa: PLC0415

    mask = np.asarray(mask, bool).ravel()
    out = np.zeros(mask.size, np.int64)
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return out
    loc = np.full(mask.size, -1, np.int64)
    loc[idx] = np.arange(idx.size)
    nb = nbr[idx]
    ok = (nb >= 0) & mask[np.where(nb >= 0, nb, 0)]
    r = np.repeat(np.arange(idx.size), nb.shape[1])[ok.ravel()]
    c = loc[nb.ravel()[ok.ravel()]]
    g = coo_matrix((np.ones(r.size, np.int8), (r, c)), shape=(idx.size, idx.size))
    _, lab = connected_components(g, directed=False)
    out[idx] = np.bincount(lab)[lab]
    return out
