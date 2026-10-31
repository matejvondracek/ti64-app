classdef GrainSizeTest < matlab.unittest.TestCase
  % ti64.grainGeometry and ti64.mergeColonies on the JU02 300x300 crop (laths at 5 deg so that 10.53 deg
  % packet pairs are separate grains). Reference numbers = pymtex (conformance test_phase4.py).

  properties
    Gid
    Tmp
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
      id = ti64.loadEbsd(fullfile(d(1).folder, d(1).name), 2);
      p = struct('ci_threshold', NaN, 'spike_removal', false, 'spike_angle_deg', 5, 'noise_angle_deg', 3, ...
        'noise_max_indexed_px', 2, 'noise_max_void_px', 4, 'fill', true, 'hq_filter', false, 'hq_alpha', 0.5, ...
        'hq_threshold_deg', 15);
      cid = ti64.cleanup(id, p);
      tc.Gid = ti64.calcGrains(cid, struct('hagb_deg', 5, 'lagb_deg', 2, 'alpha', 3.1, 'min_px', 1));
      tc.Tmp = [tempname '.h5'];
    end
  end

  methods (TestClassTeardown)
    function clear(tc)
      ti64.store('clear');
      if isfile(tc.Tmp), delete(tc.Tmp); end
    end
  end

  methods (Test)
    function geometry(tc)
      ti64.grainGeometry(tc.Gid, tc.Tmp);
      S = ti64.store('get', tc.Gid);
      id = h5read(tc.Tmp, '/id');
      tc.verifyEqual(id(:), double(S.grains.id(:)));
      cl = h5read(tc.Tmp, '/caliper_long_um'); cs = h5read(tc.Tmp, '/caliper_short_um');
      tc.verifyTrue(all(cl(:) >= cs(:) - 1e-9));
      ang = h5read(tc.Tmp, '/caliper_long_angle_deg');
      tc.verifyTrue(all(ang >= 0 & ang < 180));
      P = h5read(tc.Tmp, '/perimeter_um');
      tc.verifyTrue(all(P(:) >= 2 * cl(:) - 1e-6));     % perimeter of a convex outline >= 2 x diameter
    end

    function colonies(tc)
      [cid, n, np] = ti64.mergeColonies(tc.Gid, struct('burgers_tol_deg', 3));
      tc.verifyEqual([n np], [667 65]);                  % pymtex: 732 laths -> 667 colonies, 65 pairs
      C = ti64.store('get', cid);
      gid = C.ebsd.grainId; gid = gid(~isnan(gid) & gid > 0);
      tc.verifyEqual(numel(unique(gid)), n);
      [~, n0, np0] = ti64.mergeColonies(tc.Gid, struct('burgers_tol_deg', 0));
      tc.verifyEqual([n0 np0], [732 0]);
    end
  end
end
