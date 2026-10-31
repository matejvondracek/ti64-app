classdef StoreLoadTest < matlab.unittest.TestCase
  % ti64.store / ti64.loadEbsd / ti64.exportEbsd on the JU02 300x300 crop.
  % Run:  runtests('matlab/tests')   (needs MTEX via ti64.setupMtex and data/test/crops)

  properties
    Crop
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
    end
  end

  methods (Test)
    function storeRoundTrip(tc)
      id = ti64.store('put', struct('a', 1));
      tc.verifyEqual(numel(id), 12);
      tc.verifyEqual(ti64.store('get', id).a, 1);
      tc.verifyTrue(ismember(id, ti64.store('list')));
      ti64.store('remove', id);
      tc.verifyError(@() ti64.store('get', id), 'ti64:store:missing');
    end

    function loadAndExportInFileOrder(tc)
      [id, n] = ti64.loadEbsd(tc.Crop, 2);
      c = onCleanup(@() ti64.store('remove', id));
      tc.verifyEqual(n, 90000);
      f = [tempname '.h5'];
      c2 = onCleanup(@() delete(f));
      info = ti64.exportEbsd(id, f);
      tc.verifyEqual(info.n, 90000);
      tc.verifyTrue(info.isHex);
      tc.verifyEqual(info.setting, 2);
      x = h5read(f, '/x'); y = h5read(f, '/y'); q = h5read(f, '/quat');
      tc.verifyEqual(size(q), [4 90000]);              % transposed for h5py
      % file order: row-major, first row at y = 0 with increasing x
      tc.verifyEqual(y(1:300), zeros(1, 300), 'AbsTol', 1e-9);
      tc.verifyTrue(all(diff(x(1:300)) > 0));
      ph = h5read(f, '/phase');
      tc.verifyEqual(sort(unique(ph)), [0 1 2]);
    end

    function rejectsBadSetting(tc)
      tc.verifyError(@() ti64.loadEbsd(tc.Crop, 7), 'MATLAB:validators:mustBeMember');
    end
  end
end
