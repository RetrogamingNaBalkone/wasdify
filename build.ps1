[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$buildRoot = Join-Path $repoRoot 'build\exe'
$buildPython = Join-Path $buildRoot 'venv\Scripts\python.exe'

function Assert-ExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed (exit code $LASTEXITCODE)."
    }
}

if ($env:OS -ne 'Windows_NT') {
    throw 'Windows EXE files must be built on Windows.'
}

New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
$taskTemp = Join-Path $buildRoot 'temp'
New-Item -ItemType Directory -Force -Path $taskTemp | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP

Push-Location $repoRoot
try {
    $env:TEMP = $taskTemp
    $env:TMP = $taskTemp
    if (-not (Test-Path -LiteralPath $buildPython)) {
        $localPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $localPython) {
            & $localPython -m venv (Join-Path $buildRoot 'venv')
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3 -m venv (Join-Path $buildRoot 'venv')
        } else {
            throw 'Install Python 3.11+ with Tcl/Tk, or create .venv before building.'
        }
        Assert-ExitCode 'Creating the build environment'
    }

    & $buildPython -c 'import sys, tkinter; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
    Assert-ExitCode 'Checking Python and Tcl/Tk'

    & $buildPython -m pip --disable-pip-version-check install '.[exe]'
    Assert-ExitCode 'Installing build dependencies'

    & $buildPython (Join-Path $repoRoot 'scripts\build_exe.py')
    Assert-ExitCode 'Building and checking EXE files'
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    Pop-Location
}
