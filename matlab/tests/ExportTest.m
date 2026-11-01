classdef ExportTest < matlab.unittest.TestCase
  % Phase 5: ti64.valueColors (viewer / pixel-exact export colours) and ti64.render in export mode (exact width,
  % ROI limits, annotation primitives, PNG / TIFF / SVG / PDF) on the JU02 300x300 crop.

  properties
    Id
    N
    Dir
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
      [tc.Id, tc.N] = ti64.loadEbsd(fullfile(d(1).folder, d(1).name), 2);
      tc.Dir = tempname; mkdir(tc.Dir);
    end
  end

  methods (TestClassTeardown)
    function clear(tc)
      ti64.store('clear');
      rmdir(tc.Dir, 's');
    end
  end

  methods (Test)
    function valueColorsRule(tc)
      v = [0; 0.5; 1; NaN; 2];
      in = fullfile(tc.Dir, 'v.h5'); out = fullfile(tc.Dir, 'c.h5');
      h5create(in, '/values', numel(v)); h5write(in, '/values', v);
      ti64.valueColors(in, 'gray', [0 1], out);
      c = h5read(out, '/rgba')';
      tc.verifyEqual(c(:, 4), uint8([255; 255; 255; 0; 255]));
      tc.verifyEqual(c([1 3 5], 1), uint8([0; 255; 255]));          % clipped to the limits
      tc.verifyEqual(c(2, 1), uint8(round(255 * 128 / 255)));        % k = 1 + round(255 * 0.5) = 129 -> 128/255
      tc.verifyEqual(h5read(out, '/clim'), [0; 1]);
      delete(out);
      ti64.valueColors(in, 'parula', [NaN NaN], out);               % automatic limits
      tc.verifyEqual(h5read(out, '/clim'), [0; 2]);
    end

    function renderExactWidthAndFormats(tc)
      in = fullfile(tc.Dir, 'r.h5');
      rgb = 0.5 * ones(tc.N, 3);
      h5create(in, '/rgb', [3 tc.N]); h5write(in, '/rgb', rgb');
      xy = [5 5; 20 15; NaN NaN]';
      h5create(in, '/pl_xy', size(xy)); h5write(in, '/pl_xy', xy);
      h5create(in, '/pl_style', [4 1]); h5write(in, '/pl_style', [1 0 0 1.5]');
      h5create(in, '/tx_xy', [2 1]); h5write(in, '/tx_xy', [30 10]');
      h5create(in, '/tx_style', [4 1]); h5write(in, '/tx_style', [0 0 0 8]');
      o = struct('cmap', 'parula', 'clim', [NaN NaN], 'colorbar_label', '', 'seg_width', 1, 'title', '', ...
        'width_cm', 5, 'dpi', 200, 'scale_bar', false, 'legend_labels', {{}}, 'legend_rgb', [], 'exact', true, ...
        'xlim', [NaN NaN], 'ylim', [NaN NaN], 'text_str', {{'label'}}, 'text_ha', {{'left'}});
      png = fullfile(tc.Dir, 'a.png');
      ti64.render(tc.Id, in, png, o);
      info = imfinfo(png);
      tc.verifyEqual(info.Width, round(5 / 2.54 * 200));
      o.title = 'a title'; o.xlim = [10 40]; o.ylim = [10 30];
      for ext = {'.tif', '.svg', '.pdf'}
        f = fullfile(tc.Dir, ['b' ext{1}]);
        ti64.render(tc.Id, in, f, o);
        tc.verifyTrue(isfile(f), ext{1});
      end
      info = imfinfo(fullfile(tc.Dir, 'b.tif'));
      tc.verifyEqual(info(1).Width, round(5 / 2.54 * 200));
    end
  end
end
