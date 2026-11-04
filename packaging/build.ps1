<#
.SYNOPSIS
    Builds Ghost Coder: Python wheel/sdist, Nuitka standalone app and NSIS installer.

.EXAMPLE
    ./packaging/build.ps1                 # everything
    ./packaging/build.ps1 -SkipInstaller  # wheel + exe only
    ./packaging/build.ps1 -Install        # build, then run the installer silently
#>
param(
    [switch]$SkipWheel,
    [switch]$SkipExe,
    [switch]$SkipInstaller,
    [switch]$Install
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$version = (Select-String -Path 'src/ghost_coder/__init__.py' -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$nuitkaOut = Join-Path $root 'build/nuitka'
$appDir = Join-Path $nuitkaOut 'GhostCoder'
Write-Host "Building Ghost Coder $version" -ForegroundColor Cyan

uv sync --all-groups
if ($LASTEXITCODE) { throw 'uv sync failed' }

if (-not $SkipWheel) {
    uv build --out-dir dist
    if ($LASTEXITCODE) { throw 'uv build failed' }
}

if (-not $SkipExe) {
    if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir }
    $nuitkaArgs = @(
        '-m', 'nuitka',
        '--standalone',
        '--assume-yes-for-downloads',
        '--msvc=latest',
        "--jobs=$([Environment]::ProcessorCount)",
        '--enable-plugin=pywebview',
        '--include-package=ghost_coder',
        '--include-package-data=ghost_coder',
        '--include-package=amqtt',          # broker plugins are imported by dotted name
        '--include-distribution-metadata=amqtt',
        '--include-package=pynput',         # platform backend is imported dynamically
        '--windows-console-mode=disable',
        '--windows-icon-from-ico=src/ghost_coder/assets/icon.ico',
        '--company-name=Seth Stenzel',
        '--product-name=Ghost Coder',
        '--file-description=Ghost Coder',
        "--file-version=$version",
        "--product-version=$version",
        '--copyright=MIT License, Seth Stenzel',
        "--output-dir=$nuitkaOut",
        '--output-folder-name=GhostCoder',
        '--output-filename=GhostCoder.exe',
        'packaging/launcher.py'
    )
    uv run python @nuitkaArgs
    if ($LASTEXITCODE) { throw 'Nuitka build failed' }
    Write-Host "App built: $appDir\GhostCoder.exe" -ForegroundColor Green
}

$installer = Join-Path $root "dist/GhostCoder-Setup-$version.exe"
if (-not $SkipInstaller) {
    $makensis = (Get-Command makensis -ErrorAction SilentlyContinue).Source
    if (-not $makensis) { $makensis = "${env:ProgramFiles(x86)}\NSIS\makensis.exe" }
    if (-not (Test-Path $makensis)) { throw 'makensis.exe not found; install NSIS from https://nsis.sourceforge.io' }
    New-Item -ItemType Directory -Force (Join-Path $root 'dist') | Out-Null
    & $makensis /V2 "/DVERSION=$version" "/DSOURCE_DIR=$appDir" "/DOUT_FILE=$installer" 'packaging/installer.nsi'
    if ($LASTEXITCODE) { throw 'makensis failed' }
    Write-Host "Installer built: $installer" -ForegroundColor Green
}

if ($Install) {
    Write-Host 'Installing...' -ForegroundColor Cyan
    Start-Process -FilePath $installer -ArgumentList '/S' -Wait
    Write-Host "Installed to $env:LOCALAPPDATA\Programs\Ghost Coder" -ForegroundColor Green
}
