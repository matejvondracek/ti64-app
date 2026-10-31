classdef Phase2Test < matlab.unittest.TestCase
  % Phase 2 +ti64 functions on the JU02 300x300 crop: cleanup, fill, grains, maps, rendering.
  % Reference numbers are the same as the pymtex unit tests (conformance).

  properties
    Crop
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
      tc.Crop = fullfile(d(1).folder, d(1).name);
      tc.Id = ti64.loadEbsd(tc.Crop, 2);
    end
  end

  methods (TestClassTeardown)
    function clear(~)
      ti64.store('clear');
    end
  end

  methods (Static)
    function p = preset(ci)
      p = struct('ci_threshold', ci, 'spike_removal', false, 'spike_angle_deg', 5, 'noise_angle_deg', 3, ...
        'noise_max_indexed_px', 2, 'noise_max_void_px', 4, 'fill', true, 'hq_filter', false, ...
        'hq_alpha', 0.5, 'hq_threshold_deg', 15);
    end
  end

  methods (Test)
    function neighbourTableHex(tc)
      x = [0 0.2 0.4 0.1 0.3]'; y = [0 0 0 0.173205 0.173205]';
      nb = ti64.neighbourTable(x, y, true(5, 1), true);
      tc.verifyEqual(nb(4, :), [0 5 1 2 0 0]);     % W none, E 5, NW 1, NE 2, SW/SE none
    end

    function dilationFillMajority(tc)
      nb = [0 2 0 0 0 0; 1 3 0 0 0 0; 2 0 0 0 0 0];   % chain 1 - 2 - 3
      [src, sw] = ti64.dilationFill(logical([0 1 0]), [7 0 9], logical([1 0 1]), nb);
      tc.verifyEqual(src(2), 1);                    % tie 1:1 -> first neighbour in order (W)
      tc.verifyEqual(sw, 1);
    end

    function cleanupOimCleaned(tc)
      [id2, rep] = ti64.cleanup(tc.Id, tc.preset(NaN));
      tc.verifyEqual([rep.n_noise_indexed_removed rep.n_small_void_px rep.n_filled rep.n_unfilled], [161 1 162 0]);
      e0 = ti64.store('get', tc.Id); e1 = ti64.store('get', id2);
      tc.verifyEqual(nnz(e0.isIndexed), 89865);     % input untouched
      tc.verifyEqual(nnz(e1.prop.ti64_cleanup == 4), 162);
    end

    function cleanupRaw(tc)
      [~, rep] = ti64.cleanup(tc.Id, tc.preset(0.1));
      tc.verifyEqual([rep.n_ci_removed rep.n_filled rep.n_unfilled rep.n_indexed_after], [2954 1066 21 87766]);
    end

    function grainsAndMaps(tc)
      id2 = ti64.cleanup(tc.Id, tc.preset(NaN));
      [gid, n] = ti64.calcGrains(id2, struct('hagb_deg', 15, 'lagb_deg', 2, 'alpha', 3.1, 'min_px', 1));
      tc.verifyEqual(n, 650);
      f = [tempname '.h5']; c = onCleanup(@() delete(f));
      ti64.exportGrains(gid, f);
      tc.verifyEqual(numel(h5read(f, '/grain_id')), 650);
      tc.verifyEqual(sum(h5read(f, '/gb_inner')), 2005);
      bt = h5read(f, '/gb_burgers_type');
      tc.verifyEqual(reshape(sort(unique(bt(bt > 0))), 1, []), 1:5);
      delete(f);
      p = struct('kind', 'kam', 'kam_order', 1, 'kam_threshold_deg', 5, 'gnd_phase', 'Ti (alpha)', ...
                 'poisson', 0.34, 'gnd_stencil', '1hop');
      ti64.computeMap(gid, p, f);
      v = h5read(f, '/values');
      tc.verifyEqual(mean(v, 'omitnan'), 0.3646, 'AbsTol', 1e-3);
      tc.verifyLessThanOrEqual(max(v), 5 + 1e-9);
    end

    function renderWritesPng(tc)
      f = [tempname '.h5']; png = [tempname '.png'];
      c = onCleanup(@() cellfun(@(x) delete(x), {f, png}));
      ti64.ipfColors(tc.Id, 'Ti (alpha)', 'ipfHKLKey', [0 0 1], f);
      o = struct('cmap', 'parula', 'clim', [NaN NaN], 'colorbar_label', '', 'seg_width', 1, ...
                 'title', 'test', 'width_cm', 8, 'dpi', 72, 'scale_bar', true, ...
                 'legend_labels', {{}}, 'legend_rgb', zeros(1, 0));
      ti64.render(tc.Id, f, png, o);
      tc.verifyTrue(isfile(png));
      im = imread(png);
      tc.verifyGreaterThan(size(im, 1), 100);
    end
  end
end
