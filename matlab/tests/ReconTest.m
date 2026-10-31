classdef ReconTest < matlab.unittest.TestCase
  % ti64.reconstruct (EBSD_part2.m port) and its exporters on the JU02 1000x1000 crop (~200 um, whole
  % prior-beta grains), ideal Burgers OR. Reference numbers = the pymtex unit tests (conformance).

  properties
    Id
    Rid
    Res
  end

  methods (TestClassSetup)
    function setup(tc)
      here = fileparts(mfilename('fullpath'));
      repo = fileparts(fileparts(here));
      tc.applyFixture(matlab.unittest.fixtures.PathFixture(fullfile(repo, 'matlab')));
      s = ti64.readSettings();
      tc.assumeNotEmpty(s.mtex_path, 'MTEX path not configured');
      ti64.setupMtex();
      d = dir(fullfile(repo, 'data', 'test', 'crops', '*JU02*_crop1000.ang'));
      tc.assumeNotEmpty(d, 'crop missing: run scripts/make_crops.py');
      tc.Id = ti64.loadEbsd(fullfile(d(1).folder, d(1).name), 2);
      [tc.Rid, tc.Res] = ti64.reconstruct(tc.Id, ReconTest.params('burgers'));
    end
  end

  methods (TestClassTeardown)
    function clear(~)
      ti64.store('clear');
    end
  end

  methods (Static)
    function p = params(orMode)
      % defaults of ReconParams (python/ti64app/backends/base.py)
      p = struct('cleanAngle', 3, 'noiseMaxIndexed', 2, 'noiseMaxVoid', 4, 'segAngle', 1.5, 'smoothIter', 5, ...
        'method', 'variantGraph', 'vgThreshold', 5, 'vgTolerance', 1.5, 'vgNumIter', 3, 'vgInflation', 1.05, ...
        'graphThreshold', 2, 'graphTolerance', 1.5, 'graphInflation', 1.6, 'minProb', 0, 'orRefine', true, ...
        'orMaxDev', 1, 'orMode', orMode, 'mergeMode', 'single', 'mergeThreshold', 3, 'mergeStart', 1, ...
        'mergeStep', 0.5, 'inclusionMaxSize', 5, 'badFit', 3, 'histMinECD', 5, 'stepAngle', 4, 'noiseAngle', 1, ...
        'hagbAngle', 10, 'misMapMin', 1.5, 'misMapMax', 8, 'logFile', '');
      p.sweep = {'single', 0.5; 'single', 3; 'progressive', 3};
    end
  end

  methods (Test)
    function summaryNumbers(tc)
      r = tc.Res;
      tc.verifyEqual(r.or_used, 'burgers');
      tc.verifyGreaterThan(r.or_dev_deg, 0);
      tc.verifyGreaterThan(r.rec_frac, 0.99);
      tc.verifyTrue(r.n_grains_all >= 250 && r.n_grains_all <= 360);       % pymtex 306, MATLAB 304
      tc.verifyTrue(r.n_grains_min_ecd >= 15 && r.n_grains_min_ecd <= 30);
      tc.verifyLessThan(abs(r.mean_fit_deg - 3.41), 0.1);
      tc.verifyEqual(size(r.sweep), [3 4]);
      tc.verifyGreaterThan(r.sweep(1, 1), r.sweep(2, 1));                    % 0.5 deg merge: more grains
      tc.verifyEqual(sum(r.mis_share), 1, 'AbsTol', 1e-9);
    end

    function parentMap(tc)
      S = ti64.store('get', tc.Rid);
      P = S.ebsd;
      st = P.prop.ti64_recon_state(:);
      real = ~isnan(P.prop.oldId(:));
      tc.verifyEqual(nnz(real), 1e6);
      tr = st == 2;
      tc.verifyTrue(all(P.phaseId(tr) == P.name2id('Ti (beta)')));
      tc.verifyTrue(all(P.prop.ti64_parent_grain(tr) > 0));
      tc.verifyTrue(all(isnan(P.prop.ti64_recon_fit(~tr))));
      tc.verifyTrue(all(~P.isIndexed(real & st <= 1)));                    % voids + removed noise
    end

    function exportRecon(tc)
      f = [tempname '.h5'];
      c = onCleanup(@() delete(f));
      ti64.exportRecon(tc.Rid, f);
      gid = h5read(f, '/grain_id');
      mq = h5read(f, '/grain_mean_quat')';
      tc.verifyEqual(numel(gid), tc.Res.n_grains_all);
      tc.verifyEqual(size(mq), [numel(gid) 4]);
      mis = h5read(f, '/gb_mis_deg'); g1 = h5read(f, '/gb_grain1'); g2 = h5read(f, '/gb_grain2');
      tc.verifyTrue(all(isnan(mis(g1 == 0 | g2 == 0))));
      tc.verifyTrue(all(mis(g1 > 0 & g2 > 0) > 0));
      pre = h5read(f, '/pre_mis_deg');
      tc.verifyEqual(numel(pre), numel(h5read(f, '/pre_length_um')));
    end

    function ipfColorsQuatAndChart(tc)
      f1 = [tempname '.h5']; f2 = [tempname '.h5']; png = [tempname '.png'];
      c = onCleanup(@() cellfun(@(x) delete(x), {f1, f2, png}));
      h5create(f1, '/quat', [4 2]); h5write(f1, '/quat', [1 0 0 0; NaN NaN NaN NaN]');
      ti64.ipfColorsQuat(tc.Rid, 'Ti (beta)', 'ipfHKLKey', [0 0 1], f1, f2);
      rgb = h5read(f2, '/rgb')';
      tc.verifyEqual(size(rgb), [2 3]);
      tc.verifyTrue(all(isnan(rgb(2, :))) && all(isfinite(rgb(1, :))));
      o = struct('bar_width', 1, 'xlabel', 'x', 'ylabel', 'y', 'title', 't', 'log_y', true, 'vline_x', 2, ...
        'vline_labels', {{'thr'}}, 'color', [0.3 0.45 0.7], 'width_cm', 8, 'dpi', 100);
      ti64.renderChart(0:4, 1:5, o, png);
      tc.verifyTrue(isfile(png));
    end
  end
end
