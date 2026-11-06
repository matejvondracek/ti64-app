<#
.SYNOPSIS
  Creates "Ti64 EBSD/BSE" shortcuts on the Desktop and in the Start menu that start the desktop app
  (Scripts\ti64-gui.exe of the ti64 conda env: no console window, no conda activate needed).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\make_shortcut.ps1
  powershell -ExecutionPolicy Bypass -File scripts\make_shortcut.ps1 -EnvPath D:\conda\envs\ti64

  First install the app into the env (creates ti64-gui.exe):
    C:\Users\<you>\miniforge3\envs\ti64\python.exe -m pip install -e . --no-deps
#>
param(
  [string]$EnvPath = "",
  [switch]$NoDesktop,
  [switch]$NoStartMenu
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

if (-not $EnvPath) {
  $candidates = @(
    (Join-Path $env:USERPROFILE "miniforge3\envs\ti64"),
    (Join-Path $env:USERPROFILE "mambaforge\envs\ti64"),
    (Join-Path $env:USERPROFILE "miniconda3\envs\ti64"),
    (Join-Path $env:USERPROFILE "anaconda3\envs\ti64")
  )
  $EnvPath = $candidates | Where-Object { Test-Path (Join-Path $_ "Scripts\ti64-gui.exe") } | Select-Object -First 1
}
$exe = Join-Path $EnvPath "Scripts\ti64-gui.exe"
if (-not $EnvPath -or -not (Test-Path $exe)) {
  Write-Error ("ti64-gui.exe not found. Install the app into the ti64 env first:`n" +
               "  <env>\python.exe -m pip install -e `"$repo`" --no-deps`n" +
               "or give the env folder: -EnvPath C:\path\to\envs\ti64")
}
$icon = Join-Path $repo "docs\icon.ico"
$shell = New-Object -ComObject WScript.Shell
$targets = @()
if (-not $NoDesktop) { $targets += [Environment]::GetFolderPath("Desktop") }
if (-not $NoStartMenu) { $targets += (Join-Path ([Environment]::GetFolderPath("Programs")) "") }
foreach ($dir in $targets) {
  $lnk = Join-Path $dir "Ti64 EBSD-BSE.lnk"
  $s = $shell.CreateShortcut($lnk)
  $s.TargetPath = $exe
  $s.WorkingDirectory = $repo
  $s.Description = "Ti64 EBSD/BSE - EBSD and SEM microstructure analysis of Ti-6Al-4V"
  if (Test-Path $icon) { $s.IconLocation = "$icon,0" }
  $s.Save()
  Write-Output "created $lnk"
}

# WebView2 (draws the window): present on Windows 11; say what to install if not
$guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
$keys = @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$guid",
          "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$guid",
          "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$guid")
$pv = $keys | ForEach-Object { (Get-ItemProperty -Path $_ -Name pv -ErrorAction SilentlyContinue).pv } |
      Where-Object { $_ -and $_ -ne "0.0.0.0" } | Select-Object -First 1
if ($pv) {
  Write-Output "Microsoft Edge WebView2 runtime $pv found."
} else {
  Write-Warning ("Microsoft Edge WebView2 runtime not found. Install the Evergreen Bootstrapper from " +
                 "https://developer.microsoft.com/microsoft-edge/webview2/ before starting the app.")
}
