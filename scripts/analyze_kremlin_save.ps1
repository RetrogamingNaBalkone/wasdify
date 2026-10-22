param(
    [ValidateSet('ru', 'en')][string]$Language = 'ru',
    [string]$GhidraHome = 'C:/Users/dspev/AppData/Local/Programs/Ghidra/ghidra_12.1.2_PUBLIC',
    [string]$JavaHome = 'C:/Users/dspev/AppData/Local/Programs/Ghidra/java/jdk-21.0.12.1+1',
    [ValidateSet('continuation', 'state', 'live')][string]$Scope = 'continuation'
)
# Development only. Import into an ignored, disposable project; never save a
# program or write to the existing research projects or reference executables.
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskOutput = Join-Path $taskRoot 'build/save-research'
New-Item -ItemType Directory -Force "$taskOutput/project-$Language" | Out-Null
$taskProfiles = @{
    ru = @{
        File = 'RUS/PKR.EXE'
        Hash = '12f78afc0eba05f76b5dc0bb99da38d7daa20585024c0ba958fe828aa3864d28'
        Addresses = '1008:0719,1000:0b02,1038:0000,1038:005f,1038:0108,1038:0284,1050:0054,1050:00df,1068:0000,1068:02f5,1068:06d6,1068:085e,1068:09c5,1068:0a85,1068:1657,1068:193e,1068:1cc2,1068:1eec,1068:20bf,1068:2127,1068:22b6,1068:2368,1068:26e8,1068:2db9,1068:30a2,1068:30ff,1068:3262,1068:334e,1068:347e,1068:36b1,1068:3845,1098:004f,1098:017d,1098:0205,1098:125b,1130:0239,1130:026f,1130:02b6'
    }
    en = @{
        File = 'ENG/PKE.EXE'
        Hash = 'cce7bc5993d0734ad87e93db37a70b8b52faedfff14ac48d6e317e5ac996a37b'
        Addresses = '1008:0aa9,1010:13aa,1010:140f,1010:14be,1010:163a,1020:018c,1020:07cf,1020:1b61,1020:1efc,1030:0079,1078:18cf,1078:1905,1078:194c'
    }
}
$taskProfile = $taskProfiles[$Language]
if ($Scope -eq 'live') {
    if ($Language -ne 'ru') { throw 'Live reload currently targets RUS only' }
    $taskProfile.Addresses = '1008:0719,1008:02f7,1008:0545,1008:069c,1068:193e,1098:0000,1098:004f,1098:02a7,10c0:155d,10c0:1639,1038:0108,1110:0355,1110:037a,1110:0524'
}
if ($Scope -eq 'state') {
    if ($Language -ne 'ru') { throw 'The state inventory currently targets RUS only' }
    # Address inventory is evidence from the same hashed RUS sample. Export the
    # current read-only import, not the old decompiler text or project database.
    $taskListing = Join-Path $taskRoot 'originals/source/pkr-ghidra/export/listing.txt'
    $taskAddresses = @(Get-Content -LiteralPath $taskListing | ForEach-Object {
        if ($_ -match '^FUNCTION ((1068|1098|1050|1060|1108):[0-9a-f]{4}) ') { $Matches[1] }
    })
    $taskProfile.Addresses = ($taskAddresses + @('1038:0000', '1038:005f',
        '1038:0108', '1038:0214', '1038:0265', '1038:0284', '10a8:0065', '10a8:00b7',
        '10f8:0000', '10f8:0592', '10f8:05c9',
        '1130:01f5', '1130:0239', '1130:026f', '1130:02b6')) -join ','
}
$taskStem = "$Scope-$Language"
$taskFile = Join-Path "$taskRoot/originals/games/pkr" $taskProfile.File
if ((Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskProfile.Hash) {
    throw 'The reference version differs from the researched sample'
}
$taskPreviousLocalAppData = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = "$taskRoot/build/ghidra-local"
    $taskArguments = @(
        '-Djava.system.class.loader=ghidra.GhidraClassLoader',
        "-Duser.home=$taskRoot/build/ghidra-home",
        "-Dapplication.settingsdir=$taskRoot/build/ghidra-settings",
        '-Djava.awt.headless=true', '-Xmx2G', '-cp',
        "$GhidraHome/Ghidra/Framework/Utility/lib/Utility.jar",
        'ghidra.Ghidra', 'ghidra.app.util.headless.AnalyzeHeadless',
        "$taskOutput/project-$Language", 'SaveResearch',
        '-import', $taskFile, '-readOnly', '-analysisTimeoutPerFile', '120',
        '-scriptPath', $PSScriptRoot, '-postScript', 'GhidraKremlinWeapons.java',
        "$taskOutput/$taskStem.txt", $taskProfile.Addresses,
        '-postScript', 'GhidraKremlinSaveInventory.java',
        "$taskOutput/inventory-$Language.tsv", $Language
    )
    & "$JavaHome/bin/java.exe" @taskArguments *> "$taskOutput/$taskStem.log"
    if ($LASTEXITCODE -ne 0) { throw "Ghidra exited with code $LASTEXITCODE" }
    $taskExport = Get-Content -LiteralPath "$taskOutput/$taskStem.txt" -Raw
    foreach ($taskAddress in $taskProfile.Addresses.Split(',')) {
        if (-not $taskExport.Contains("FUNCTION $taskAddress")) {
            throw "Missing exported function $taskAddress"
        }
    }
    if (Select-String -LiteralPath "$taskOutput/$taskStem.log" -Pattern '^ERROR' -Quiet) {
        throw 'Ghidra reported an error; inspect the analysis log'
    }
    if ((Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskProfile.Hash) {
        throw 'Reference hash changed during analysis'
    }
    Write-Output "$taskOutput/$taskStem.txt"
} finally {
    $env:LOCALAPPDATA = $taskPreviousLocalAppData
}
