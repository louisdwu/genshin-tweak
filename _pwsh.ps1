$ErrorActionPreference = 'SilentlyContinue'
$paths = @()
$paths += ([Environment]::GetEnvironmentVariable('PATH','User') -split ';')
$paths += ([Environment]::GetEnvironmentVariable('PATH','Machine') -split ';')
$exes = New-Object System.Collections.Generic.HashSet[string]
foreach ($p in $paths) {
    if (-not $p) { continue }
    foreach ($n in @('python.exe','pythonw.exe')) {
        try {
            $f = Join-Path $p $n
            if (Test-Path -LiteralPath $f) {
                [void]$exes.Add((Resolve-Path -LiteralPath $f).Path)
            }
        } catch {}
    }
}
$lines = New-Object System.Collections.ArrayList
foreach ($exe in $exes) {
    $r = & $exe 'C:/Coding/scripts/genshin_tweak/_pkgs_probe.py' 2>$null
    $ver = (& $exe '-c' 'import sys; print(sys.version.split()[0])' 2>$null)
    [void]$lines.Add(("$exe | ver=$ver | pkgs=" + ($r -join '|')))
}
$lines | Out-File -FilePath 'C:/Coding/scripts/genshin_tweak/_pwsh_out.txt' -Encoding ASCII
