param([string]$Distro = 'Ubuntu-24.04')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$portableRoot = $repoRoot.Replace([char]92, [char]47)
$convertedRoot = & wsl -d $Distro -- wslpath -a $portableRoot
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve workspace in WSL' }
$linuxRoot = $convertedRoot.Trim()
function Invoke-Check([string[]]$Arguments) {
    & wsl -d $Distro --cd $linuxRoot -- .venv/wsl/bin/python @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Check failed: ' + ($Arguments -join ' ')) }
}
Invoke-Check -Arguments @('tests/run_checks.py')
