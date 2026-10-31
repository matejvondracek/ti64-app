classdef SetupTest < matlab.unittest.TestCase
  % Tests for ti64.readSettings / ti64.setupMtex.  Run:  runtests('matlab/tests')

  methods (TestClassSetup)
    function addRepoPath(tc)
      here = fileparts(mfilename('fullpath'));
      tc.applyFixture(matlab.unittest.fixtures.PathFixture(fullfile(here, '..')));
    end
  end

  methods (Test)
    function readsTomlPaths(tc)
      f = [tempname '.toml'];
      writelines(["[paths]", "mtex_path = 'C:\tools\mtex'", 'literature_dir = "D:\lit"'], f);
      c = onCleanup(@() delete(f));
      s = ti64.readSettings(f);
      tc.verifyEqual(s.mtex_path, 'C:\tools\mtex');
      tc.verifyEqual(s.literature_dir, 'D:\lit');
      tc.verifyEqual(s.cache_dir, '');
    end

    function rejectsFolderWithoutMtex(tc)
      tc.verifyError(@() ti64.setupMtex(tempdir), 'ti64:setupMtex:notMtex');
    end

    function startsConfiguredMtex(tc)
      s = ti64.readSettings();
      tc.assumeNotEmpty(s.mtex_path, 'no settings file with paths.mtex_path on this machine');
      ti64.setupMtex();
      tc.verifyTrue(startsWith(getMTEXpref('version'), 'mtex-7.'));
      tc.verifyEqual(exist('EBSD', 'class'), 8);
    end
  end
end
