classdef CleanStepTest < matlab.unittest.TestCase
  % Phase 6.6 fix 5: ti64.cleanStep (single cleanup operations incl. OIM-style grain dilation) and
  % ti64.dilationFill with an iteration limit, on the JU02 300x300 crop. Cross-backend numbers:
  % tests/conformance/test_fix5.py (JU04_area1).

  properties
    Id
  end

  methods (TestClassSetup)
    function setup(tc)
      here = fileparts(mfilename('fullpath'));
      repo = fileparts(fileparts(here));
      tc.applyFixture(matlab.unittest.fixtures.PathFixture(fullfile(repo, 'matlab')));
      s = ti64.readSettings();
      tc.assumeNotEmpty(s.mtex_path, 'MTEX path not configured');
      ti64.setupMtex();
      d = dir(fullfile(repo, 'data', 'test', 'crops', '*JU02*_crop300.ang'));
      tc.assumeNotEmpty(d, 'crop missing: run scripts/make_crops.py');
      tc.Id = ti64.loadEbsd(fullfile(d(1).folder, d(1).name), 2);
    end
  end

  methods (TestClassTeardown)
    function clear(~)
      ti64.store('clear');
    end
  end

  methods (Test)
    function chainCountsAddUp(tc)
      [a, ra] = ti64.cleanStep(tc.Id, 'ci_threshold', struct('ci_threshold', 0.3));
      tc.verifyEqual(ra.n_indexed_before - ra.n_removed, ra.n_indexed_after);
      [b, rb] = ti64.cleanStep(a, 'noise_removal', struct('noise_angle_deg', 3, 'noise_max_indexed_px', 2, ...
        'fill', true));
      tc.verifyEqual(rb.n_filled + rb.n_unfilled, rb.n_removed);
      [~, rc] = ti64.cleanStep(b, 'void_fill', struct('max_void_px', 4, 'angle_deg', 3));
      tc.verifyEqual(rc.n_void_px + rc.n_left_large_void_px, rc.n_not_indexed_before);
      tc.verifyEqual(rc.n_filled + rc.n_unfilled, rc.n_void_px);
    end

    function grainDilation(tc)
      a = ti64.cleanStep(tc.Id, 'ci_threshold', struct('ci_threshold', 0.3));
      p = struct('tolerance_deg', 5, 'min_grain_px', 2, 'multi_row', false, 'single_iteration', false);
      [~, r] = ti64.cleanStep(a, 'grain_dilation', p);
      tc.verifyEqual(sum(r.changed_per_iteration), r.n_changed);
      tc.verifyEqual(r.n_changed + r.n_unchanged, r.n_candidates);
      p.single_iteration = true; p.multi_row = true;
      [~, r1] = ti64.cleanStep(a, 'grain_dilation', p);
      tc.verifyEqual(r1.n_iterations, 1);
      tc.verifyLessThanOrEqual(r1.n_changed, r.n_changed);
    end

    function dilationFillLimit(tc)
      nbr = [0 2; 1 3; 2 0];                       % a chain 1-2-3, 1-based, 0 = none
      [src, sweeps, counts] = ti64.dilationFill([false; true; true], [1; 1; 1], [true; false; false], nbr, 1);
      tc.verifyEqual(sweeps, 1);
      tc.verifyEqual(counts, 1);
      tc.verifyEqual(src(:)', [1 1 0]);
    end
  end
end
