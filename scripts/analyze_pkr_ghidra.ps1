param(
    [Parameter(Mandatory=$true)][string]$GhidraHome,
    [Parameter(Mandatory=$true)][string]$JavaHome
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskOutput = Join-Path $taskRoot 'originals/source/pkr-ghidra'
New-Item -ItemType Directory -Force $taskOutput | Out-Null
$taskPreviousLocalAppData = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = Join-Path $taskRoot 'build/ghidra-local'
    $taskArguments = @(
        '-Djava.system.class.loader=ghidra.GhidraClassLoader',
        "-Duser.home=$taskRoot/build/ghidra-home",
        "-Dapplication.settingsdir=$taskRoot/build/ghidra-settings",
        '-Djava.awt.headless=true', '-Xmx2G', '-cp',
        (Join-Path $GhidraHome 'Ghidra/Framework/Utility/lib/Utility.jar'),
        'ghidra.Ghidra', 'ghidra.app.util.headless.AnalyzeHeadless',
        $taskOutput, 'PKR-performance'
    )
    if (Test-Path (Join-Path $taskOutput 'PKR-performance.gpr')) {
        $taskArguments += @('-process', 'PKR.EXE', '-noanalysis')
    } else {
        $taskArguments += @('-import', (Join-Path $taskRoot 'originals/games/pkr/RUS/PKR.EXE'),
                            '-analysisTimeoutPerFile', '180')
    }
    $taskArguments += @('-scriptPath', $PSScriptRoot, '-postScript',
                        'GhidraPkrPerformance.java', (Join-Path $taskOutput 'export'))
    & (Join-Path $JavaHome 'bin/java.exe') @taskArguments
    if ($LASTEXITCODE -ne 0) { throw "Ghidra exited with code $LASTEXITCODE" }
} finally {
    $env:LOCALAPPDATA = $taskPreviousLocalAppData
}
